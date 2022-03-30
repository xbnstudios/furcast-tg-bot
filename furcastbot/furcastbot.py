#!/usr/bin/env python3

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from html import escape
import logging
import os
from typing import Dict, List, Tuple

from dateutil import tz
from ddate.base import DDate
from flask import make_response, Request, Response
import requests
from telegram import (
    Bot,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ParseMode,
    Update,
)
import telegram.constants
import telegram.error
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    Dispatcher,
    Filters,
    MessageHandler,
    Updater,
)
from tomlkit.toml_file import TOMLFile

config_file = os.environ.get("CONFIG", os.path.join(os.getcwd(), "config.toml"))
config: dict
chat_map: Dict[str, dict]
managed_chats: Dict[str, List[str]]
timezones: Dict[str, str]
join_rate_limit_delay: Dict[str, timedelta]


def load_config(file):
    new_config = TOMLFile(file).read()

    # show slugs and copy show references to alias names
    for slug, show in new_config["shows"].items():
        show["slug"] = slug
        new_config["shows"] = {
            alias: show for alias in show.get("aliases", [])
        } | new_config["shows"]

    # chat slugs
    for slug, chat in new_config["chats"].items():
        chat["slug"] = slug

    # chat ID -> chat object
    new_chat_map = {chat["id"]: chat for chat in new_config["chats"].values()}

    # admin chat ID -> [managed chat names]
    new_managed_chats = {}
    for name, chat in new_config["chats"].items():
        if "admin_chat" not in chat:
            continue
        admin_chat_id = new_config["chats"][chat["admin_chat"]]["id"]
        if admin_chat_id not in new_managed_chats:
            new_managed_chats[admin_chat_id] = []
        new_managed_chats[admin_chat_id].append(name)

    # timezone alias -> canonical timezone name
    new_timezones = {}
    for canonical, aliases in new_config["timezones"].items():
        new_timezones |= {alias: canonical for alias in aliases}

    new_join_delay = {
        chat["id"]: timedelta(minutes=chat.get("rate_limit_delay_minutes", 0))
        for chat in new_config["chats"].values()
    }

    global config, chat_map, managed_chats, timezones, join_rate_limit_delay
    config, chat_map, managed_chats, timezones, join_rate_limit_delay = (
        new_config,
        new_chat_map,
        new_managed_chats,
        new_timezones,
        new_join_delay,
    )


load_config(config_file)

# List of (invite_link, chat_id) because revoking an invite link
# requires both the URL and the chat ID
join_link_list: List[Tuple[str, int]] = []
join_rate_limit_last_join: Dict[str, datetime] = defaultdict(
    lambda: datetime(1970, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
)

logging.basicConfig(level=getattr(logging, config.get("log_level", "INFO")))
logging.getLogger("telegram").setLevel(logging.INFO)
logging.getLogger("apscheduler").setLevel(logging.INFO)

bot: Bot
dispatcher: Dispatcher
updater: Updater
flask = "X_GOOGLE_FUNCTION_VERSION" in os.environ
if flask:
    logging.info("Running in flask")
    bot = Bot(token=config["telegram_token"])
    dispatcher = Dispatcher(bot, None, workers=0, use_context=True)


def chatinfo(update: Update, context: CallbackContext) -> None:
    """Bot /chatinfo callback
    Posts info about the current chat"""

    update.effective_chat.send_message(
        text="Name: {}\nID: {}\nUsername: {}\nType: {}".format(
            update.effective_chat.title,
            update.effective_chat.id,
            update.effective_chat.username,
            update.effective_chat.type,
        )
    )


def post_pin(
    bot: Bot, group: str, message=None, pin=None, notify=False, forward=False
) -> Response:
    """Post a message to a group, pin/unpin
    :bot: The telegram Bot object
    :group: The group slug, ie "fc", to match ``announce`` entry
    :message: None, or the message to post
    :pin: None or True/False, whether or not to quiet-pin in first chat
    :notify: True/False, enable notify for channel messages
    :forward: True/False, forward from the first chat to the others
    """

    if group not in config["announce"]:
        return make_response({"status": "Error", "message": "Unknown group"}, 400)

    announce_list = config["announce"][group]

    if message is not None:
        root_message = bot.send_message(
            announce_list[0], message, disable_notification=not notify
        )
        sent_messages = {announce_list[0]: root_message}

        if forward:
            for target_chat_id in announce_list[1:]:
                sent_messages[target_chat_id] = bot.forward_message(
                    target_chat_id,
                    root_message.chat_id,
                    root_message.message_id,
                    not notify,
                )

        if notify is True and pin is not False:  # quiet-pin in all chats
            for chat_id, message in sent_messages.items():
                # Don't pin in channels / first-groups
                if chat_id == announce_list[0]:
                    continue
                try:
                    bot.pin_chat_message(
                        chat_id, message.message_id, disable_notification=True
                    )
                except telegram.error.BadRequest as e:
                    # Usually "Not enough rights to pin a message"
                    logging.warning("Pin failed in %s: %s", chat_id, e)

    if pin is False:
        for chat_id in announce_list:
            try:
                bot.unpin_chat_message(chat_id)
            except telegram.error.BadRequest as e:
                # Usually "Not enough rights to unpin a message"
                logging.warning("Unpin failed in %s: %s", chat_id, e)
    return make_response({"status": "OK"}, 200)


def beat(showtime: datetime) -> str:
    showtimez = showtime.astimezone(tz.gettz("UTC+1"))
    beatseconds = (
        showtimez - showtimez.replace(hour=0, minute=0, second=0, microsecond=0)
    ).total_seconds()
    beats = beatseconds / (60 * 60 * 24) * 1000
    return showtimez.strftime(f"d%d.%m.%y @{beats:03.0f}")


def post_np_group(group_id: int, text: str, oneshot: bool = False) -> None:
    chat = bot.get_chat(group_id)
    pin_id = getattr(chat.pinned_message, "message_id", None)

    if pin_id is None:
        pin = chat.send_message(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        try:
            bot.pin_chat_message(chat.id, pin.message_id, disable_notification=True)
        except telegram.error.BadRequest as e:
            # Usually "Not enough rights to pin a message"
            logging.warning("post_pin pin failed in %s: %s", chat, e)
    else:
        try:
            bot.edit_message_text(
                text,
                group_id,
                pin_id,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except telegram.error.BadRequest as e:
            if "can't be edited" in e.message:
                if not oneshot:  # Try once to unpin/post
                    bot.unpin_chat_message(chat.id)
                    return post_np_group(group_id, text, oneshot=True)
                return make_response({"status": "Error", "error": "Not my pin"}, 200)
            if "exactly the same" not in e.message:
                raise e


def post_np(title: str, show_slug: str) -> None:
    """Creates/updates pin for Now Playing
    Called by Gelo
    """

    logging.debug("Now playing on %r: %r", show_slug, title)

    if show_slug not in config["announce"]:
        return make_response({"status": "Error", "error": "Unknown show slug"}, 404)

    show = config["shows"][show_slug]

    text = "Now playing: {title}\n🎵 {show_name} is live!\n"
    if show != "dd":
        text += "📺 <a href='https://{domain}/video/'>Watch</a> "
    text += (
        "🎧 <a href='https://{domain}/audio/'>Listen</a> "
        "💬 <a href='https://{domain}/chat/'>Chat</a> "
    )
    text = text.format(title=title, show_name=show["name"], domain=show["domain"])

    groups = config["announce"].get(show_slug + "-np")
    if groups is None:
        return make_response(
            {"status": "Error", "error": "No now-playing chat for show"}, 200
        )
    for group_id in groups:
        try:
            post_np_group(group_id, text)
        except Exception as e:
            logging.error("post_np failed: %s: %s", show_slug, e)
            raise e

        # context.bot.unpin_chat_message(chat.id)

    return make_response({"status": "OK"}, 200)


def next_pin_callback(context: CallbackContext) -> None:
    """Creates/updates pin for next show info
    Called every minute by JobQueue after eg. /next fc pin
    """

    ctx = context.job.context
    logging.debug("Running next-pin job for %s (%s)", ctx["chat"].title, ctx["chat"].id)
    show = config["shows"][ctx["slug"]]
    delta = ctx["showtime"] - datetime.now(tz=timezone.utc)

    if delta.total_seconds() < 0:
        context.job.schedule_removal()
        text = "<a href='https://{}/'>{}</a> is starting!".format(
            show["domain"], show["name"]
        )
        ctx["message"].edit_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        # Need a new copy to get the current pinned_message
        chat = context.bot.get_chat(ctx["chat"].id)
        if (
            getattr(chat.pinned_message, "message_id", None)
            == ctx["message"].message_id
        ):
            context.bot.unpin_chat_message(chat.id)
        return

    daystr = "" if delta.days < 1 else f"{delta.days} days, "
    hours = delta.seconds // (60 * 60)
    if hours > 0 or delta.days > 0:
        hourstr = str(hours) + (" hour" if hours == 1 else " hours") + ", "
    else:
        hourstr = ""
    minutes = (delta.seconds // 60) % 60
    minutestr = str(minutes) + (" minute" if minutes == 1 else " minutes")
    text = "<a href='https://{}/'>{}</a> starts in {}{}{}".format(
        show["domain"],
        show["name"],
        daystr,
        hourstr,
        minutestr,
    )
    try:
        if ctx["message"] is None:
            ctx["message"] = ctx["chat"].send_message(
                text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
            try:
                context.bot.pin_chat_message(
                    ctx["chat"].id, ctx["message"].message_id, disable_notification=True
                )
            except telegram.error.BadRequest as e:
                # Usually "Not enough rights to pin a message"
                logging.warning("Next-show pin failed in %s: %s", ctx["chat"].id, e)
        else:
            try:
                ctx["message"].edit_text(
                    text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
            except telegram.error.BadRequest as e:
                if "exactly the same" not in e.message:
                    raise e
    except Exception as e:
        logging.error("Next-show job failed: %s: %s", ctx["chat"].id, e)
        context.job.schedule_removal()
        raise e


def nextshow(update: Update, context: CallbackContext) -> None:
    """Bot /next callback
    Posts the next scheduled show for a given slug/name and timezone"""

    # Message edit.
    if update.message is None:
        return

    args = update.message.text.split(" ")

    # Which show
    if len(args) > 1 and args[1].lower() in config["shows"]:
        slug = args[1].lower()
    else:
        slug = chat_map[update.effective_chat.id]["next_show_default"]
        args.insert(1, "")  # reverse shift to offer timezone
    show = config["shows"][slug]
    domain = show["domain"]

    try:
        r = requests.get("https://{}/nextshow/".format(domain))
        if r.status_code != 200:
            raise Exception("API returned " + str(r.status_code))
    except Exception as e:
        update.message.reply_text(text="Error: " + str(e))
        raise e
    showtime = datetime.fromtimestamp(int(r.text), tz=timezone.utc)

    # Start update job
    if "pin" in args:
        user = update.effective_chat.get_member(update.effective_user.id)
        if not user.can_pin_messages and user.status != "creator":
            update.message.reply_text(text="You aren't allowed to do that")
            return
        ctx = {
            "chat": update.effective_chat,
            "message": None,
            "slug": slug,
            "showtime": showtime,
        }
        logging.info(
            "Scheduled next-pin job, %s (%s) for %s",
            update.effective_user.name,
            update.effective_user.id,
            update.effective_chat.title,
        )
        updater.job_queue.run_repeating(
            next_pin_callback,
            60,
            1,  # 0 results in no first-run, probably a library bug...
            context=ctx,
            name=f"next_pin_{update.effective_chat.id}",
        )
        return

    # Timezones
    if len(args) < 3:  # no TZ
        tzstr = "America/New_York"
    else:
        tzstr = args[2]

    if tzstr.lower() in ["ddate", "discordian"]:
        datestr = str(DDate(showtime))
        if datestr.startswith("Today is "):
            datestr = datestr[9:]
    elif tzstr.lower() in ["beat", "swatch", "internet"]:
        datestr = beat(showtime)
    else:
        if tzstr.lower() in timezones:  # custom map
            tzstr = timezones[args[2].lower()]
        elif len(tzstr) < 5:  # probably "EDT" style
            tzstr = tzstr.upper()
        # Otherwise try verbatim
        tzobj = tz.gettz(tzstr)
        if tzobj is None:
            update.message.reply_text(text="Sorry, I don't understand")  # TZ or show
            return
        datestr = (
            showtime.astimezone(tzobj).strftime("%a %e %b, %H:%M %Z").replace("  ", " ")
        )

    delta = showtime - datetime.now(tz=timezone.utc)
    if delta.total_seconds() < 0:
        update.effective_chat.send_message("A show is currently live or just ended!")
        return

    deltastr = "{} days, {:02}:{:02}".format(
        delta.days, delta.seconds // (60 * 60), (delta.seconds // 60) % 60
    )
    update.effective_chat.send_message(
        text="The next {} is {}. That's {} from now.".format(
            show["name"], datestr, deltastr
        )
    )


def report_mention_wrapper(update: Update, context: CallbackContext) -> None:
    """Bot @admin callback
    Given a message which has an @mention in it, see if that @mention is for
    @admin or @admins, and then do the /report callback.
    """
    entities = update.message.parse_entities(
        types=[telegram.constants.MESSAGEENTITY_MENTION]
    )
    for entity, text in entities.items():
        if text == "@admin" or text == "@admins":
            return report(update, context)


def report(update: Update, context: CallbackContext) -> None:
    """Bot /report callback
    Gives instructions for reporting problems
    In the future, may support "Forward me any problem messages", etc"""

    # Ignore messages that aren't PMed to the bot.
    if update.effective_chat.type == "private":
        update.message.reply_text(
            text="Reporting messages in PMs isn't done yet; for now please PM an admin directly."
        )
    else:
        if "admin_chat" not in chat_map[update.effective_chat.id]:
            update.message.reply_text("Sorry, that's not configured for this group.")
            return
        if update.message is None or update.message.reply_to_message is None:
            update.message.reply_text(
                text="Please reply to the message you want to report."
            )
        else:
            mention = update.message.from_user.mention_html()
            summon_link = update.message.link
            reply_link = update.message.reply_to_message.link
            escaped_report_text = escape(update.message.text)
            admin_chat = config["chats"][
                chat_map[update.effective_chat.id]["admin_chat"]
            ]["id"]
            update.message.reply_to_message.forward(admin_chat)
            context.bot.send_message(
                admin_chat,
                f'{mention} has <a href="{summon_link}">summoned</a> admins in reply '
                f'to <a href="{reply_link}">the above message</a>; they said:\n'
                f"{escaped_report_text}",
                parse_mode=ParseMode.HTML,
            )
            update.message.reply_text("Thank you; we’re on it.")


def revoke_invite_links(update: Update, context: CallbackContext) -> None:
    """Bot /newlink callback
    Revokes all known invite links for target chat
    NOTES: Each admin has a DIFFERENT INVITE LINK.
    The bot API does not allow it to fetch a list of links it's created, so
    it can only revoke ones created since startup or the one fed to it with
    /newlink CHAT_SLUG INVITE_LINK
    """

    if update.effective_chat.id not in managed_chats:
        return

    args = update.message.text.split(" ")
    target = None
    managed = managed_chats.get(update.effective_chat.id, [])
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

    target_id = config["chats"][target]["id"]
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
            bot_join_link = updater.bot.export_chat_invite_link(
                config["chats"][target]["id"]
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
            chat_map[chat_id]["slug"],
            link,
        )
        try:
            revoked_link = updater.bot.revoke_chat_invite_link(chat_id, link)
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
    chat_to_join = config["chats"][config["default_invite_chat"]]
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
    if chat_map[chat_to_join["id"]].get("rate_limit_delay_minutes", 0) > 0:
        logging.debug("rate limiting is active for chat %s", chat_to_join["slug"])
        time_since_last_join = (
            current_timestamp - join_rate_limit_last_join[chat_to_join["id"]]
        )
        logging.debug(
            "it has been %s since the last permitted join", time_since_last_join
        )
        if time_since_last_join < join_rate_limit_delay[chat_to_join["id"]]:
            logging.info(
                "Denying join by %s (%s, %s) to %s due to rate limit",
                user.username,
                user.full_name,
                user.id,
                chat_to_join["slug"],
            )
            update.message.reply_html(
                text=config["rate_limit_template"].format(
                    escaped_fname=escape(user.first_name)
                ),
                disable_web_page_preview=True,
            )
            return
    join_rate_limit_last_join[chat_to_join["id"]] = current_timestamp

    logging.info(
        "Inviting %s (%s, %s) to %s",
        user.username,
        user.full_name,
        user.id,
        chat_to_join["slug"],
    )
    # Create custom invite link for this user, which limits how many times the
    # link can be used (to help prevent spam/abuse).
    try:
        # 5 second minimum
        expiry_date = current_timestamp + timedelta(
            minutes=config.get("join_link_valid_minutes", 10), seconds=5
        )
        logging.debug(
            "Creating join link for %s that expires at %s",
            chat_to_join["slug"],
            expiry_date,
        )
        user_reference = ("@" + user.username) if user.username else user.full_name
        custom_join_link = context.bot.create_chat_invite_link(
            chat_to_join["id"],
            expire_date=expiry_date,
            member_limit=1,
            name=f"{user.id} {user_reference}",
        )
        join_link_list.append((custom_join_link.invite_link, chat_to_join["id"]))
        update.message.reply_html(
            text=config["join_template"].format(escaped_fname=escape(user.first_name)),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text=config.get("join_button_text", "Join"),
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


def topic(update: Update, context: CallbackContext) -> None:
    """Bot /topic callback
    Changes chat title, if allowed"""

    # No PMs
    if update.effective_chat.type == "private":
        update.message.reply_text("Sorry, that only works in groups.")
        return

    parts = update.message.text.split(" ", 1)
    if len(parts) < 2 or len(parts[1].strip()) == 0:
        update.message.reply_html(
            f"Try e.g. <code>{parts[0]} Not My Cup Of Legs</code> to suggest "
            f"a chat topic, or <code>{parts[0]} -</code> to clear"
        )
        return

    # Get just the requested title
    if parts[1].strip().lower() in ["-delete", "-", '""']:
        requested = ""
    else:
        requested = parts[1].strip()

    # Unrestricted chat, or admin
    chat = chat_map[update.effective_chat.id]
    user = update.effective_chat.get_member(update.effective_user.id)
    if (
        not chat.get("topic_approval_required", True)
        # No reason to require full can_change_info for this.
        # Chatops have can_delete_messages, so let's use that.
        or user.can_delete_messages
        # I have no idea why being the creator doesn't imply that perm
        or user.status == "creator"
    ):
        logging.info(
            "%s: %s: %s",
            update.effective_chat.title,
            update.effective_user.username,
            update.message.text,
        )
        # If silent change
        if parts[0] == "/stopic":
            try:
                update.message.delete()
            except telegram.error.BadRequest as e:
                logging.warning(
                    "stopic message delete failed in %s: %s",
                    update.effective_chat.id,
                    e,
                )
        topic_set(context.bot, update.effective_chat, requested)
        if "topic_approval_chat" in chat:
            mention = update.message.from_user.mention_html()
            link = update.message.link
            context.bot.send_message(
                config["chats"][chat["topic_approval_chat"]]["id"],
                f'{mention} <a href="{link}">set</a> topic "{requested}"\n',
                parse_mode=ParseMode.HTML,
                disable_notification=True,
            )
        return

    if "topic_approval_chat" in chat:
        mention = update.message.from_user.mention_html()
        link = update.message.link
        callback_data = ",{},{},{},{}".format(
            update.effective_chat.id,
            update.effective_user.id,
            update.message.message_id,
            requested,
        )
        if len(callback_data) + 2 > 64:
            update.message.reply_text("Sorry, that's too long.")
            return
        context.bot.send_message(
            config["chats"][chat["topic_approval_chat"]]["id"],
            (
                f'{mention} <a href="{link}">proposed</a> topic "{requested}"\n'
                "Admins can accept, admins or op can reject:"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Accept", callback_data="ta" + callback_data
                        ),
                        InlineKeyboardButton(
                            "Reject", callback_data="tr" + callback_data
                        ),
                    ]
                ]
            ),
            disable_notification=True,
        )
        if chat["topic_approval_chat"] != chat["slug"]:
            update.message.reply_text(f'Requested topic "{requested}"')
        return


def button(update: Update, context: CallbackContext) -> None:
    """Bot button callback"""

    data = update.callback_query.data
    # Topic accept/reject buttons
    if data.startswith("t"):
        action, chat_id, user_id, message_id, requested = data.split(",", 4)
        chat_id = int(chat_id)
        target_chat = context.bot.get_chat(chat_id)
        user_id = int(user_id)
        message_id = int(message_id)

        # Get user's perms in /topic'd chat
        user = target_chat.get_member(update.effective_user.id)

        # Not authorized
        if not (
            # Topic requester can reject their own
            (update.effective_user.id == user_id and action == "tr")
            # Chatops in the /topic'd chat can approve (see topic() note)
            or user.can_delete_messages
            # I have no idea why being the creator doesn't imply that perm
            or user.status == "creator"
            # Admin group approval: allow anyone
            or (
                update.effective_chat.id != chat_id
                and update.effective_chat.id
                == config["chats"][chat_map[chat_id]["topic_approval_chat"]]["id"]
            )
        ):
            update.callback_query.answer(text="Nice try")
            return
        logging.debug(
            "%s: %s: %s bytes: %s",
            update.effective_chat.title,
            update.effective_user.username,
            len(data),
            data,
        )

        # Buttons
        if action == "ta":
            topic_set(context.bot, context.bot.get_chat(chat_id), requested)
            if (
                chat_id
                != config["chats"][chat_map[chat_id]["topic_approval_chat"]]["id"]
            ):
                context.bot.send_message(
                    chat_id, "Accepted!", reply_to_message_id=message_id
                )
            update.callback_query.answer(text="Accepted")
            update.callback_query.message.edit_text(
                update.callback_query.message.text_html
                + "\nApproved by "
                + update.effective_user.mention_html(),
                parse_mode=ParseMode.HTML,
            )
            return
        elif action == "tr":
            update.callback_query.answer(text="Rejected")
            update.callback_query.message.edit_text(
                update.callback_query.message.text_html
                + "\nRejected by "
                + update.effective_user.mention_html(),
                parse_mode=ParseMode.HTML,
            )
            return

    logging.error("Button didn't understand callback: %s", data)


def topic_set(bot: Bot, chat: Chat, requested_topic: str) -> None:
    """Enact a topic change"""

    logging.info(
        '%s: Setting topic "%s"',
        chat.title,
        requested_topic,
    )

    sep = " – "
    requested_topic = requested_topic.strip()
    if len(requested_topic) > 0:
        requested_topic = sep + requested_topic
    title = chat.title.split(sep, 1)[0] + requested_topic
    try:
        bot.set_chat_title(chat.id, title)
    except telegram.error.BadRequest as e:
        logging.warning("Title change failed in %s: %s", chat.id, e)


def version(update: Update, context: CallbackContext) -> None:
    """Bot /version callback
    Posts bot info and Cloud Function version"""

    update.effective_chat.send_message(
        "<a href='https://github.com/xbnstudios/furcast-tg-bot'>furcast-tg-bot</a>\n"
        "GCF version: {}".format(os.environ.get("X_GOOGLE_FUNCTION_VERSION")),
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML,
    )


def webhook(request: Request):
    logging.info("access_route: %s", ",".join(request.access_route))
    logging.info("args: %s", request.args)
    logging.info("data: %s", request.data)
    logging.info("form: %s", request.form)
    if "api_key" not in config or (
        request.args.get("apikey") != config["api_key"]
        and request.form.get("apikey") != config["api_key"]
    ):
        return make_response("", 404)
    if "version" in request.args:
        return str(os.environ.get("X_GOOGLE_FUNCTION_VERSION")) + "\n"
    if "title" in request.form:
        return post_np(request.form["title"], request.form.get("show"))
    if request.form.get("group") in config["announce"]:
        pin = request.form.get("pin")
        if pin in ["true", "1"]:
            pin = True
        elif pin in ["false", "0"]:
            pin = False
        notify = True if request.form.get("notify") in ["true", "1"] else False
        forward = True if request.form.get("forward") in ["true", "1"] else False
        return post_pin(
            bot,
            request.form["group"],
            request.form.get("message"),
            pin,
            notify,
            forward,
        )
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)


def main():
    global bot, dispatcher, updater
    logging.info("Running standalone")
    updater = Updater(token=config["telegram_token"], use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("chatinfo", chatinfo))
    dispatcher.add_handler(CommandHandler("newlink", revoke_invite_links))
    dispatcher.add_handler(CommandHandler("next", nextshow))
    dispatcher.add_handler(CommandHandler("report", report))
    dispatcher.add_handler(CommandHandler("admin", report))
    dispatcher.add_handler(CommandHandler("admins", report))
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("topic", topic))
    dispatcher.add_handler(CommandHandler("stopic", topic))
    dispatcher.add_handler(CommandHandler("version", version))
    dispatcher.add_handler(
        MessageHandler(
            Filters.entity(telegram.constants.MESSAGEENTITY_MENTION)
            & Filters.update.message,
            report_mention_wrapper,
        )
    )
    dispatcher.add_handler(CallbackQueryHandler(button))

    # Start responding
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":  # Poll bot
    main()
