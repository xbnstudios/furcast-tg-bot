#!/usr/bin/env python3

from datetime import datetime, timedelta
from dateutil import tz
from ddate.base import DDate
from flask import make_response
import logging
import os
import requests
from telegram import Bot, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Dispatcher, CommandHandler

if "JOIN_LINK" not in os.environ or "TELEGRAM_TOKEN" not in os.environ or "APIKEY" not in os.environ:
    print("You forgot to set one of the environment vars!")
    exit(3)

join_template = ("Hello, {fname}! Here's your invite link to join the FurCast chat.\n"
                 "Don't forget to read the rules on https://furcast.fm/chat/ !")
button_text = "CLICK ME OH YEAH JUST LIKE THAT"
furcast_link = os.environ["JOIN_LINK"]
apikey = os.environ["APIKEY"]
main_chat_id = "-1001422900025"
domains = {
        "fc": "furcast.fm",
        "fnt": "fridaynighttracks.com",
        "mp": "maestropaws.com",
        }
show_names = {
        "fc": "FurCast",
        "fnt": "Friday Night Tracks",
        "mp": "MaestroPaws",
        }
timezones = { # Additional mappings
        "eastern": "America/New_York",
        "et": "America/New_York",
        }

logging.basicConfig(level=logging.INFO)
bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
dispatcher = Dispatcher(bot, None, workers=0)

def chatinfo(bot, update):
    update.message.reply_text(
            text="Name: {}\nID: {}\nUsername: {}\nType: {}".format(
                    update.effective_chat.title,
                    update.effective_chat.id,
                    update.effective_chat.username,
                    update.effective_chat.type))

def start(bot, update):
    if update.effective_chat.type != "private":
        return
    update.message.reply_markdown(
            text=join_template.format(fname=update.message.from_user.first_name),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(text=button_text, url=furcast_link)]]))

def live(bot, update):
    args = update.message.text.split(" ")
    live = args[1].lower() in ["on", "true", "yes", "1"]
    return set_live(bot, live, " ".join(args[2:]))

def set_live(bot, state, message):
    if state:
        msg = bot.send_message(main_chat_id, message)
        bot.pin_chat_message(main_chat_id, msg.message_id)
    else:
        bot.unpin_chat_message(main_chat_id)
    return make_response('{"status":"OK"}\n', 200)

def nextshow(bot, update):
    args = update.message.text.split(" ")

    # Which show
    if len(args) < 2 or args[1].lower() not in domains:
        slug = "fc"
    else:
        slug = args[1].lower()
    domain = domains[slug.lower()]

    try:
        r = requests.get("https://{}/nextshow/".format(domain))
        if r.status_code != 200:
            update.message.reply_text(text=
                    "Oops, API returned {}".format(r.status_code))
            raise Exception()
    except Exception as e:
        update.message.reply_text(text="Error")
        raise e
    showtime = datetime.utcfromtimestamp(int(r.text))

    # Timezones
    if len(args) < 3: # no TZ
        tzstr = "America/New_York"
    else:
        tzstr = args[2]

    if tzstr.lower() in ["ddate", "discordian"]:
        datestr = DDate(showtime)
    else:
        if tzstr.lower() in timezones: # custom map
            tzstr = timezones[args[2].lower()]
        elif len(tzstr) < 5: # probably "EDT" style
            tzstr = tzstr.upper()
        # Otherwise try verbatim
        tzobj = tz.gettz(tzstr)
        if tzobj is None:
            update.message.reply_text(
                    text="Sorry, I don't know that time zone")
            return
        datestr = (showtime.astimezone(tzobj)
            .strftime("%a %e %b, %H:%M %Z").replace("  ", " "))

    delta = showtime - datetime.now()
    deltastr = "{} days, {:02}:{:02}".format(delta.days,
            delta.seconds//(60*60), (delta.seconds//60)%60)
    update.message.reply_markdown(
            text="The next {} is {}. That's {} from now.".format(
                    show_names[slug], datestr, deltastr))

def report(bot, update):
    update.message.reply_text(
            text=("Please forward the problem messages and a brief explanation"
                  " to @RawrJesse, @rileywd, @s0ph0s, or another op."))

def webhook(request):
    print("access_route", ",".join(request.access_route))
    #print("args", request.args)
    #print("data", request.data)
    #print("form", request.form)
    if request.args.get("apikey") != apikey:
        return make_response("Nice try\n", 403)
    if request.args.get("cron") == "1":
        return cron()
    if request.args.get("live") in ["true", "false"]:
        return set_live(bot, request.form["live"] == "true", request.form.get("message"))
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)

dispatcher.add_handler(CommandHandler("chatinfo", chatinfo))
#dispatcher.add_handler(CommandHandler("live", live))
dispatcher.add_handler(CommandHandler("next", nextshow))
dispatcher.add_handler(CommandHandler("report", report))
dispatcher.add_handler(CommandHandler("start", start))
