#!/usr/bin/env python3

from dotenv import load_dotenv
from html import escape
from flask import make_response, Request
import logging
import os
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ParseMode,
    Update,
)
from telegram.ext import (
    CallbackContext,
    CommandHandler,
    Dispatcher,
    Updater,
)

load_dotenv()
if "TELEGRAM_TOKEN" not in os.environ or "APIKEY" not in os.environ:
    logging.error("You forgot to set one of the environment vars!")
    exit(3)

# Parsed as HTML - be sure to escape anything you put in!
join_template = (
    "Hello, {escaped_fname}! Please "
    "<a href='https://furcast.fm/chat/#rules'>read the rules</a>, "
    "then your invite link is below."
)
button_text = "CLICK ME OH YEAH JUST LIKE THAT"


invite_chat = -10012341234  # The chat the bot invites to
admin_chat = -10043214321  # The staff group, if applicable

join_link = os.environ.get("JOIN_LINK")
apikey = os.environ["APIKEY"]

logging.basicConfig(level=logging.INFO)
if __name__ == "__main__":  # Poll bot
    updater = Updater(token=os.environ["TELEGRAM_TOKEN"], use_context=True)
    dispatcher = updater.dispatcher
else:  # Webhook bot
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    dispatcher = Dispatcher(bot, None, workers=0, use_context=True)


def start(update: Update, context: CallbackContext) -> None:
    """Bot /start callback
    Gives user invite link button"""

    if update.effective_chat.type != "private":
        return
    logging.info(
        "Inviting %s (%s, %s)",
        update.effective_user.username,
        update.effective_user.full_name,
        update.effective_user.id,
    )
    update.message.reply_html(
        text=join_template.format(
            escaped_fname=escape(update.message.from_user.first_name)
        ),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(text=button_text, url=join_link)]]
        ),
        disable_web_page_preview=True,
    )


def version(update: Update, context: CallbackContext) -> None:
    """Bot /version callback
    Posts bot info and Cloud Function version"""

    update.effective_chat.send_message(
        "[furcast-tg-bot](https://git.xbn.fm/xbn/furcast-tg-bot)\n"
        "GCF version: {}".format(os.environ.get("X_GOOGLE_FUNCTION_VERSION")),
        disable_web_page_preview=True,
        parse_mode=ParseMode.MARKDOWN,
    )


def webhook(request: Request):
    logging.info("access_route: %s", ",".join(request.access_route))
    logging.info("args: %s", request.args)
    logging.info("data: %s", request.data)
    logging.info("form: %s", request.form)
    if request.args.get("apikey") != apikey:
        return make_response("", 404)
    if "version" in request.args:
        return str(os.environ.get("X_GOOGLE_FUNCTION_VERSION")) + "\n"
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)


dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("version", version))

if __name__ == "__main__":
    # Get current bot invite link
    try:
        chat = updater.bot.get_chat(invite_chat)
        bot_join_link = chat.invite_link
    except Exception as e:
        logging.info("Failed to get invite link: %s", e)
        bot_join_link = None

    if bot_join_link is None:
        logging.info("Generating new bot invite link...")
        try:
            bot_join_link = updater.bot.export_chat_invite_link(invite_chat)
        except Exception as e:  # Probably no rights
            logging.warning("Unable to generate bot invite link: %s", e)
            pass
    if bot_join_link is not None:
        join_link = bot_join_link

    # Start responding
    updater.start_polling()
