import os
import logging
from telegram import Bot, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Dispatcher, CommandHandler

join_template = ("Hello, {fname}! Here's your invite link to join the FurCast chat.\n"
                 "Don't forget to read the rules on https://furcast.fm/chat/ !")
furcast_link = "https://t.me/joinchat/DRFvFVcxfIDqx65h2VElww"
button_text = "CLICK ME OH YEAH JUST LIKE THAT"

logging.basicConfig(level=logging.INFO)

def start(bot, update):
    update.message.reply_markdown(
            text=join_template.format(**locals()),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(text=button_text, url=furcast_link)]]))

def webhook(request):
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)

if __name__ == "__main__":
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    dispatcher = Dispatcher(bot, None, workers=0)
    dispatcher.add_handler(CommandHandler("start", start))
