from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from html import escape
import logging
from typing import Dict, List, Tuple

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
import telegram.constants
import telegram.error
from telegram.ext import CallbackContext

from .config import Config

config = Config.get_config()

# List of (invite_link, chat_id) because revoking an invite link
# requires both the URL and the chat ID
join_link_list: List[Tuple[str, int]] = []
join_rate_limit_last_join: Dict[str, datetime] = defaultdict(
    lambda: datetime(1970, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
)


def revoke_invite_links(update: Update, context: CallbackContext) -> None:
    """Bot /newlink callback
    Revokes all known invite links for target chat
    NOTES: Each admin has a DIFFERENT INVITE LINK.
    The bot API does not allow it to fetch a list of links it's created, so
    it can only revoke ones created since startup or the one fed to it with
    /newlink CHAT_SLUG INVITE_LINK
    """

    # if this chat doesn't manage any chats
    if update.effective_chat.id not in config.managed_chats:
        return

    args = update.message.text.split(" ")
    target = None
    managed = config.managed_chats.get(update.effective_chat.id, [])
    if len(args) < 2:
        if len(managed) == 1:
            target = managed[0]
    else:
        if args[1].lower() in managed:
            target = args[1].lower()
    if not target:
        update.message.reply_text(
            "Chat slug missing or invalid. You can act on these chats: "
            + ", ".join(managed)
        )
        return

    target_id = config.chats[target]["id"]
    specific_link_str = None
    if len(args) > 2:
        links = [(link, target_id) for link in args[2:]]
        specific_link_str = ", ".join(args[2:])
    else:
        links = [x for x in join_link_list if x[1] == target_id]

    logging.info(
        "%s (%s) requested invite link revocation for %s: %s",
        update.effective_user.name,
        update.effective_user.id,
        target,
        specific_link_str or "(all)",
    )

    reply_text = ""
    # Regenerate the bot's own invite link, just in case.
    if specific_link_str is None:
        try:
            bot_join_link = context.bot.export_chat_invite_link(
                config.chats[target]["id"]
            )
            if bot_join_link is None:
                raise Exception("exportChatInviteLink returned None")
            logging.info("New bot primary invite link: %s", bot_join_link)
        except Exception as e:
            logging.error("Invite link rotation failed: %s", e)
            reply_text += "Rotation of bot's primary invite link failed: " + str(e)
        else:
            reply_text += "Bot's primary invite link rotated."

    # Revoke all of the per-user invite links that the bot has issued.
    error_links = []
    removed_count = 0
    for link_tuple in links:
        link, chat_id = link_tuple
        if link_tuple in join_link_list:
            join_link_list.remove(link_tuple)

        logging.info(
            "Revoking invite link for %s: %s",
            config.chat_map[chat_id]["slug"],
            link,
        )
        try:
            revoked_link = context.bot.revoke_chat_invite_link(chat_id, link)
            if not revoked_link.is_revoked:
                raise Exception("Mysterious failure")
            removed_count += 1
        except Exception as e:
            logging.error("Revocation failed for %s with error: %s", link, e)
            error_links.append(link)
    reply_text += "\n{} per-user invite links revoked, {} failed.".format(
        removed_count, len(error_links)
    )
    reply_text += "".join(["\nFailed: " + link for link in error_links])
    update.message.reply_text(reply_text, disable_web_page_preview=True)


def start(update: Update, context: CallbackContext) -> None:
    """Bot /start callback
    Gives user invite link button"""
    chat_to_join = config.chats[config.config["default_invite_chat"]]
    current_timestamp = datetime.now(tz=timezone.utc)
    user = update.effective_user

    # Ignore messages that aren't PMed to the bot.
    if update.effective_chat.type != "private":
        return

    user_status = context.bot.get_chat_member(chat_to_join["id"], user.id)
    # user_status.LEFT is "they are not a member, but can join on their own"
    # This means that people who are banned are also excluded from joining
    # through the bot (with a somewhat confusing error).
    if user_status.status != user_status.LEFT:
        logging.info(
            "Denying join by %s (@%s, %r) to %s because they're already a member "
            "or were banned. status=%s",
            user.id,
            user.username,
            user.full_name,
            chat_to_join["slug"],
            user_status.status,
        )
        update.message.reply_text("You're already in that group!")
        return

    # If join rate limits are enabled, throttle joins to prevent join flooding.
    if config.chat_map[chat_to_join["id"]].get("rate_limit_delay_minutes", 0) > 0:
        logging.debug("rate limiting is active for chat %s", chat_to_join["slug"])
        time_since_last_join = (
            current_timestamp - join_rate_limit_last_join[chat_to_join["id"]]
        )
        logging.debug(
            "it has been %s since the last permitted join", time_since_last_join
        )
        if time_since_last_join < config.join_rate_limit_delay[chat_to_join["id"]]:
            logging.info(
                "Denying join by %s (%s, %s) to %s due to rate limit",
                user.username,
                user.full_name,
                user.id,
                chat_to_join["slug"],
            )
            update.message.reply_html(
                text=config.config["rate_limit_template"]
                .replace("\n", " ")
                .replace("<br>", "\n")
                .format(escaped_fname=escape(user.first_name)),
                disable_web_page_preview=True,
            )
            return
    join_rate_limit_last_join[chat_to_join["id"]] = current_timestamp

    # Create and send link. creates_join_request prevents use of member_limit.
    # We use the link name to associate it with one user, and revoke it after
    # use. This avoids issues with links reactivating after the user leaves the
    # chat again, and issues with the bot forgetting about links between
    # restarts.
    try:
        # 5 second minimum
        expiry_date = current_timestamp + timedelta(
            minutes=config.config.get("join_link_valid_minutes", 10), seconds=5
        )
        logging.info(
            "Inviting %s (%s, %s) to %s, link expiry %s",
            user.username,
            user.full_name,
            user.id,
            chat_to_join["slug"],
            expiry_date,
        )

        user_reference = ("@" + user.username) if user.username else user.full_name
        custom_join_link = context.bot.create_chat_invite_link(
            chat_to_join["id"],
            expire_date=expiry_date,
            name=f"{user.id} {user_reference}",
            creates_join_request=True,
        )
        join_link_list.append((custom_join_link.invite_link, chat_to_join["id"]))

        update.message.reply_html(
            text=config.config["join_template"]
            .replace("\n", " ")
            .replace("<br>", "\n")
            .format(escaped_fname=escape(user.first_name)),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text=config.config.get("join_button_text", "Join"),
                            url=custom_join_link.invite_link,
                        )
                    ]
                ]
            ),
            disable_web_page_preview=True,
        )
    except telegram.error.TelegramError as e:
        logging.info("Could not generate invite link: %s", e)
        update.message.reply_html(text="Uh oh, something went wrong. Poke an admin.")


def chat_join_request(update: Update, context: CallbackContext) -> None:
    request = update.chat_join_request

    if request.invite_link.creator.id != context.bot.id:
        logging.debug("Ignoring join request via invite link I didn't create")
        return

    # Revoke and forget link
    try:
        revoked_link = context.bot.revoke_chat_invite_link(
            request.chat.id, request.invite_link.invite_link
        )
        if not revoked_link.is_revoked:
            raise Exception("Mysterious failure")
    except Exception as e:
        logging.error(
            "Revocation failed for %s with error: %s",
            request.invite_link.invite_link,
            e,
        )
    else:
        invite_tuple = (request.invite_link.invite_link, request.chat.id)
        if invite_tuple in join_link_list:
            join_link_list.remove(invite_tuple)

    # Approve or decline request
    request_user_id = int(request.invite_link.name.split(" ", 1)[0])
    if request_user_id != request.from_user.id:
        logging.warning(
            "Declining join request to %s: %s (%s, %s) used a link meant for %s",
            config.chat_map[request.chat.id]["slug"],
            request.from_user.id,
            request.from_user.username,
            request.from_user.full_name,
            request.invite_link.name,
        )
        request.decline()
    else:
        logging.info(
            "Approving join request to %s by %s (%s, %s)",
            config.chat_map[request.chat.id]["slug"],
            request.from_user.id,
            request.from_user.username,
            request.from_user.full_name,
        )
        request.approve()
