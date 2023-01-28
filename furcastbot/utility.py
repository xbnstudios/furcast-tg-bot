from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext

from .config import Config

config = Config.get_config()


async def chatinfo(update: Update, context: CallbackContext) -> None:
    """Bot /chatinfo callback
    Posts info about the current chat"""

    await update.effective_chat.send_message(
        text="Name: {}\nID: <code>{}</code>\nUsername: {}\nType: {}".format(
            update.effective_chat.title,
            update.effective_chat.id,
            update.effective_chat.username,
            update.effective_chat.type,
        ),
        parse_mode=ParseMode.HTML,
    )


async def start(update: Update, context: CallbackContext) -> None:
    """Bot /start callback
    Give tips about commands to use"""

    await update.effective_chat.send_message(
        (
            "Hello! You can see my command list in the menu below.\n"
            "Joining a chat? Try sending /join or <code>/join chatname</code>."
        ),
        parse_mode=ParseMode.HTML,
    )


async def version(update: Update, context: CallbackContext) -> None:
    """Bot /version callback
    Posts bot info and Cloud Function version"""

    await update.effective_chat.send_message(
        "<a href='https://github.com/xbnstudios/furcast-tg-bot'>furcast-tg-bot</a>",
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML,
    )
