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


async def version(update: Update, context: CallbackContext) -> None:
    """Bot /version callback
    Posts bot info and Cloud Function version"""

    await update.effective_chat.send_message(
        "<a href='https://github.com/xbnstudios/furcast-tg-bot'>furcast-tg-bot</a>",
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML,
    )
