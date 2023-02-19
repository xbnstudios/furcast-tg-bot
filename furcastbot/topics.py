from __future__ import annotations

import logging

from telegram import (
    Bot,
    Chat,
    ChatMemberAdministrator,
    ChatMemberOwner,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
import telegram.error
from telegram.ext import CallbackContext

from .config import Config

config = Config.get_config()


async def topic(update: Update, context: CallbackContext) -> None:
    """Bot /topic callback
    Changes chat title, if allowed"""

    # No PMs
    if update.effective_chat.type == "private":
        await update.message.reply_text("Sorry, that only works in groups.")
        return

    parts = update.message.text.split(" ", 1)
    if len(parts) < 2 or len(parts[1].strip()) == 0:
        await update.message.reply_html(
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
    chat = config.chat_map[update.effective_chat.id]
    user = await update.effective_chat.get_member(update.effective_user.id)
    if (
        chat.get("topic_approval_required", True) is False
        or isinstance(user, ChatMemberOwner)
        # No reason to require full can_change_info for this.
        # Chatops have can_delete_messages, so let's use that.
        or (isinstance(user, ChatMemberAdministrator) and user.can_delete_messages)
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
                await update.message.delete()
            except telegram.error.BadRequest as e:
                logging.warning(
                    "stopic message delete failed in %s: %s",
                    update.effective_chat.id,
                    e,
                )
        await topic_set(context.bot, update.effective_chat, requested)
        if "topic_approval_chat" in chat:
            mention = update.message.from_user.mention_html()
            link = update.message.link
            await context.bot.send_message(
                config.chats[chat["topic_approval_chat"]]["id"],
                (
                    f'<b>{chat["slug"]}</b>: '
                    f'{mention} <a href="{link}">set</a> topic "{requested}"\n'
                ),
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
            await update.message.reply_text("Sorry, that's too long.")
            return
        await context.bot.send_message(
            config.chats[chat["topic_approval_chat"]]["id"],
            (
                f'<b>{chat["slug"]}</b>: '
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
            await update.message.reply_text(f'Requested topic "{requested}"')
        return


async def button(update: Update, context: CallbackContext) -> None:
    """Bot button callback"""

    data = update.callback_query.data
    # Topic accept/reject buttons
    if data.startswith("t"):
        action, chat_id, user_id, message_id, requested = data.split(",", 4)
        chat_id = int(chat_id)
        target_chat = await context.bot.get_chat(chat_id)
        user_id = int(user_id)
        message_id = int(message_id)

        # Get user's perms in /topic'd chat
        user = await target_chat.get_member(update.effective_user.id)

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
                == config.chats[config.chat_map[chat_id]["topic_approval_chat"]]["id"]
            )
        ):
            await update.callback_query.answer(text="Nice try")
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
            await topic_set(context.bot, await context.bot.get_chat(chat_id), requested)
            if (
                chat_id
                != config.chats[config.chat_map[chat_id]["topic_approval_chat"]]["id"]
            ):
                await context.bot.send_message(
                    chat_id, "Accepted!", reply_to_message_id=message_id
                )
            await update.callback_query.answer(text="Accepted")
            await update.callback_query.message.edit_text(
                update.callback_query.message.text_html
                + "\nApproved by "
                + update.effective_user.mention_html(),
                parse_mode=ParseMode.HTML,
            )
            return
        elif action == "tr":
            await update.callback_query.answer(text="Rejected")
            await update.callback_query.message.edit_text(
                update.callback_query.message.text_html
                + "\nRejected by "
                + update.effective_user.mention_html(),
                parse_mode=ParseMode.HTML,
            )
            return

    logging.error("Button didn't understand callback: %s", data)


async def topic_set(bot: Bot, chat: Chat, requested_topic: str) -> None:
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
        await bot.set_chat_title(chat.id, title)
    except telegram.error.BadRequest as e:
        logging.warning("Title change failed in %s: %s", chat.id, e)
