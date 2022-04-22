#!/usr/bin/env python3

from __future__ import annotations

import logging
import os

from flask import make_response, Request
from telegram import (
    Bot,
    Update,
)
import telegram.constants
import telegram.error
from telegram.ext import (
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    CommandHandler,
    Dispatcher,
    Filters,
    MessageHandler,
    Updater,
)

from .config import Config
from .live import post_np, post_pin
from .membership import chat_join_request, revoke_invite_links, start
from .nextshow import nextshow
from .report import report, report_mention_wrapper
from .topics import button, topic
from .utility import chatinfo, version

config = Config.get_config()

log_level = getattr(logging, config.config.get("log_level", "INFO"))
logging.basicConfig(level=log_level)
logging.getLogger("telegram").setLevel(max(logging.INFO, log_level))
logging.getLogger("apscheduler").setLevel(max(logging.INFO, log_level))

bot: Bot
dispatcher: Dispatcher
updater: Updater
flask = "X_GOOGLE_FUNCTION_VERSION" in os.environ
if flask:
    logging.info("Running in flask")
    bot = Bot(token=config.config["telegram_token"])
    dispatcher = Dispatcher(bot, None, workers=0, use_context=True)


def webhook(request: Request):
    logging.info("access_route: %s", ",".join(request.access_route))
    logging.info("args: %s", request.args)
    logging.info("data: %s", request.data)
    logging.info("form: %s", request.form)
    if "api_key" not in config or (
        request.args.get("apikey") != config.config["api_key"]
        and request.form.get("apikey") != config.config["api_key"]
    ):
        return make_response("", 404)
    if "version" in request.args:
        return str(os.environ.get("X_GOOGLE_FUNCTION_VERSION")) + "\n"
    if "title" in request.form:
        return post_np(bot, request.form["title"], request.form.get("show"))
    if request.form.get("group") in config.config["announce"]:
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
    updater = Updater(token=config.config["telegram_token"], use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(
        CommandHandler("chatinfo", chatinfo, ~Filters.update.edited_message)
    )
    dispatcher.add_handler(
        CommandHandler("newlink", revoke_invite_links, ~Filters.update.edited_message)
    )
    dispatcher.add_handler(
        CommandHandler("next", nextshow, ~Filters.update.edited_message)
    )
    dispatcher.add_handler(
        CommandHandler("report", report, ~Filters.update.edited_message)
    )
    dispatcher.add_handler(
        CommandHandler("admin", report, ~Filters.update.edited_message)
    )
    dispatcher.add_handler(
        CommandHandler("admins", report, ~Filters.update.edited_message)
    )
    dispatcher.add_handler(
        CommandHandler("start", start, ~Filters.update.edited_message)
    )
    dispatcher.add_handler(ChatJoinRequestHandler(chat_join_request))
    dispatcher.add_handler(
        CommandHandler("topic", topic, ~Filters.update.edited_message)
    )
    dispatcher.add_handler(
        CommandHandler("stopic", topic, ~Filters.update.edited_message)
    )
    dispatcher.add_handler(
        CommandHandler("version", version, ~Filters.update.edited_message)
    )
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
