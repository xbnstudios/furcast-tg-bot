#!/usr/bin/env python3

from __future__ import annotations

import logging

from telegram.constants import MessageEntityType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    CommandHandler,
    filters,
    MessageHandler,
)

from .config import Config
from .live import webhook  # noqa: F401
from .membership import chat_join_request, join_handler, revoke_invite_links
from .nextshow import nextshow
from .report import report, report_mention_wrapper
from .topics import button, topic
from .utility import chatinfo, start, version

config = Config.get_config()

log_level = getattr(logging, config.config.get("log_level", "INFO"))
logging.basicConfig(level=log_level)
logging.getLogger("telegram").setLevel(max(logging.INFO, log_level))
logging.getLogger("apscheduler").setLevel(max(logging.INFO, log_level))

application = Application.builder().token(config.config["telegram_token"]).build()


def main():
    logging.info("Running standalone")

    application.add_handlers(
        [
            CommandHandler("start", start, ~filters.UpdateType.EDITED),
            CommandHandler("chatinfo", chatinfo, ~filters.UpdateType.EDITED),
            CommandHandler("newlink", revoke_invite_links, ~filters.UpdateType.EDITED),
            CommandHandler("next", nextshow, ~filters.UpdateType.EDITED),
            CommandHandler("report", report, ~filters.UpdateType.EDITED),
            CommandHandler("admin", report, ~filters.UpdateType.EDITED),
            CommandHandler("admins", report, ~filters.UpdateType.EDITED),
            ChatJoinRequestHandler(chat_join_request),
            CommandHandler("topic", topic, ~filters.UpdateType.EDITED),
            CommandHandler("stopic", topic, ~filters.UpdateType.EDITED),
            CommandHandler("version", version, ~filters.UpdateType.EDITED),
            MessageHandler(
                filters.Entity(MessageEntityType.MENTION),
                report_mention_wrapper,
            ),
            CallbackQueryHandler(button),
            join_handler,
        ]
    )
    application.run_polling()


if __name__ == "__main__":
    main()
