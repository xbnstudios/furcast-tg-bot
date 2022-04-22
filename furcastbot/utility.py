from __future__ import annotations

import os

from telegram import (
    ParseMode,
    Update,
)
from telegram.ext import CallbackContext

from .config import Config

config = Config.get_config()


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


def version(update: Update, context: CallbackContext) -> None:
    """Bot /version callback
    Posts bot info and Cloud Function version"""

    update.effective_chat.send_message(
        "<a href='https://github.com/xbnstudios/furcast-tg-bot'>furcast-tg-bot</a>\n"
        "GCF version: {}".format(os.environ.get("X_GOOGLE_FUNCTION_VERSION")),
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML,
    )
