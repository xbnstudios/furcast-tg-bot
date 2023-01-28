from __future__ import annotations

from html import escape

from telegram import Update
from telegram.constants import MessageEntityType, ParseMode
from telegram.ext import CallbackContext

from .config import Config

config = Config.get_config()


async def report_mention_wrapper(update: Update, context: CallbackContext) -> None:
    """Bot @admin callback
    Given a message which has an @mention in it, see if that @mention is for
    @admin or @admins, and then do the /report callback.
    """
    entities = update.message.parse_entities(types=[MessageEntityType.MENTION])
    for entity, text in entities.items():
        if text == "@admin" or text == "@admins":
            return await report(update, context)


async def report(update: Update, context: CallbackContext) -> None:
    """Bot /report callback
    Gives instructions for reporting problems
    In the future, may support "Forward me any problem messages", etc"""

    # Ignore messages that aren't PMed to the bot.
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            text="Reporting messages in PMs isn't done yet; for now please PM an admin directly."
        )
    else:
        if "admin_chat" not in config.chat_map[update.effective_chat.id]:
            await update.message.reply_text(
                "Sorry, that's not configured for this group."
            )
            return
        if update.message is None or update.message.reply_to_message is None:
            await update.message.reply_text(
                text="Please reply to the message you want to report."
            )
        else:
            mention = update.message.from_user.mention_html()
            summon_link = update.message.link
            reply_link = update.message.reply_to_message.link
            escaped_report_text = escape(update.message.text)
            admin_chat = config.chats[
                config.chat_map[update.effective_chat.id]["admin_chat"]
            ]["id"]
            await update.message.reply_to_message.forward(admin_chat)
            await context.bot.send_message(
                admin_chat,
                f'{mention} has <a href="{summon_link}">summoned</a> admins in reply '
                f'to <a href="{reply_link}">the above message</a>; they said:\n'
                f"{escaped_report_text}",
                parse_mode=ParseMode.HTML,
            )
            await update.message.reply_text("Thank you; we’re on it.")
