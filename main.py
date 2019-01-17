#!/usr/bin/env python3

import os
import logging
from telegram import Bot, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Dispatcher, CommandHandler

if "JOIN_LINK" not in os.environ or "TELEGRAM_TOKEN" not in os.environ:
    print("You forgot to set one of the environment vars!")
    exit(3)

join_template = ("Hello, {fname}! Here's your invite link to join the FurCast chat.\n"
                 "Don't forget to read the rules on https://furcast.fm/chat/ !")
button_text = "CLICK ME OH YEAH JUST LIKE THAT"
furcast_link = os.environ["JOIN_LINK"]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
dispatcher = Dispatcher(bot, None, workers=0)

def start(bot, update):
    update.message.reply_markdown(
            text=join_template.format(fname=update.message.from_user.first_name),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(text=button_text, url=furcast_link)]]))

def webhook(request):
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)

dispatcher.add_handler(CommandHandler("start", start))
