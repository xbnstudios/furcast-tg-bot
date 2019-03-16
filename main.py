#!/usr/bin/env python3

from datetime import datetime
from dateutil import tz
from ddate.base import DDate
from flask import make_response
import logging
import os
import requests
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, Update
import telegram.error
from telegram.ext import Dispatcher, CommandHandler

if (
    "JOIN_LINK" not in os.environ
    or "TELEGRAM_TOKEN" not in os.environ
    or "APIKEY" not in os.environ
):
    logging.error("You forgot to set one of the environment vars!")
    exit(3)

join_template = (
    "Hello, {fname}! Here's your invite link to join the FurCast chat.\n"
    "Don't forget to read the rules on https://furcast.fm/chat/ !"
)
button_text = "CLICK ME OH YEAH JUST LIKE THAT"


class Chats(object):
    xbn = "-1001170434051"
    furcast = "-1001462860928"
    riley_test_channel = "-1001263448135"
    riley_test_group = "-1001422900025"
    xana_ts = "-1001195641999"


group_ids = {  # Array of groups to post to. Posts in first, forwards to subsequent.
    "fc": [Chats.xbn, Chats.furcast],
    "fc-np": [Chats.furcast],
    "fnt": [Chats.xbn, Chats.furcast],
    "fnt-np": [Chats.furcast],
    "mp": [Chats.xbn, Chats.furcast],
    "mp-np": [Chats.furcast],
    "test": [Chats.riley_test_channel, Chats.riley_test_group],
    "test-np": [Chats.riley_test_group],
}
allow_topics = [Chats.furcast, Chats.xana_ts, Chats.riley_test_group]
domains = {
    "fc": "furcast.fm",
    "furcast": "furcast.fm",
    "fnt": "fridaynighttracks.com",
    "fridaynighttracks": "fridaynighttracks.com",
    "fridaynighttech": "fridaynighttracks.com",
    "mp": "maestropaws.com",
    "maestropaws": "maestropaws.com",
}
show_names = {
    "furcast.fm": "FurCast",
    "fridaynighttracks.com": "Friday Night Tracks",
    "maestropaws.com": "MaestroPaws",
}
timezones = {  # Additional mappings
    "eastern": "America/New_York",
    "et": "America/New_York",
}


join_link = os.environ["JOIN_LINK"]
apikey = os.environ["APIKEY"]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
dispatcher = Dispatcher(bot, None, workers=0)


def chatinfo(bot, update):
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


def post_pin(bot, group, message=None, pin=None, notify=False, forward=False):
    """Post a message to a group, pin/unpin
    :bot: The telegram Bot object
    :group: The group slug, ie "fc", to match group_ids entry
    :message: None, or the message to post
    :pin: None or True/False, whether or not to quiet-pin in first chat
    :notify: True/False, enable notify for channel messages
    :forward: True/False, forward from the first chat to the others
    """

    if group not in group_ids:
        return make_response('{"status": "Error", "message": "Unknown group}\n', 400)

    if message is not None:
        root_message = bot.send_message(
            group_ids[group][0], message, disable_notification=not notify
        )
        sent_messages = {group_ids[group][0]: root_message}

        if forward:
            for target_chat_id in group_ids[group][1:]:
                sent_messages[target_chat_id] = bot.forward_message(
                    target_chat_id,
                    root_message.chat_id,
                    root_message.message_id,
                    not notify,
                )

        if notify is True and pin is not False:  # quiet-pin in all chats
            for chat_id, message in sent_messages.items():
                # Don't pin in channels / first-groups
                if chat_id == group_ids[group][0]:
                    continue
                try:
                    bot.pin_chat_message(
                        chat_id, message.message_id, disable_notification=True
                    )
                except telegram.error.BadRequest as e:
                    # Usually "Not enough rights to pin a message"
                    logging.warning("Pin failed in %s: %s", chat_id, e)

    if pin is False:
        for chat_id in group_ids[group]:
            try:
                bot.unpin_chat_message(chat_id)
            except telegram.error.BadRequest as e:
                # Usually "Not enough rights to unpin a message"
                logging.warning("Unpin failed in %s: %s", chat_id, e)
    return make_response('{"status":"OK"}\n', 200)


def beat(showtime: datetime) -> str:
    showtimez = showtime.astimezone(tz.gettz("UTC+1"))
    beatseconds = (
        showtimez - showtimez.replace(hour=0, minute=0, second=0, microsecond=0)
    ).total_seconds()
    beats = beatseconds / (60 * 60 * 24) * 1000
    return showtimez.strftime(f"d%d.%m.%y @{beats:03.0f}")


def nextshow(bot, update):
    """Bot /next callback
    Posts the next scheduled show for a given slug/name and timezone"""

    args = update.message.text.split(" ")

    # Which show
    if len(args) < 2:
        slug = "fc"
    elif args[1].lower() in domains:
        slug = args[1].lower()
    else:
        slug = "fc"
        args.insert(1, "")  # reverse shift to offer timezone
    domain = domains[slug.lower()]

    try:
        r = requests.get("https://{}/nextshow/".format(domain))
        if r.status_code != 200:
            update.message.reply_text(
                text="Oops, API returned {}".format(r.status_code)
            )
            raise Exception()
    except Exception as e:
        update.message.reply_text(text="Error")
        raise e
    showtime = datetime.utcfromtimestamp(int(r.text))

    # Timezones
    if len(args) < 3:  # no TZ
        tzstr = "America/New_York"
    else:
        tzstr = args[2]

    if tzstr.lower() in ["ddate", "discordian"]:
        datestr = DDate(showtime)
    elif tzstr.lower() in ["beat", "swatch", "internet"]:
        datestr = beat(showtime)
    else:
        if tzstr.lower() in timezones:  # custom map
            tzstr = timezones[args[2].lower()]
        elif len(tzstr) < 5:  # probably "EDT" style
            tzstr = tzstr.upper()
        # Otherwise try verbatim
        tzobj = tz.gettz(tzstr)
        if tzobj is None:
            update.message.reply_text(text="Sorry, I don't understand")  # TZ or show
            return
        datestr = (
            showtime.astimezone(tzobj).strftime("%a %e %b, %H:%M %Z").replace("  ", " ")
        )

    delta = showtime - datetime.now()
    if delta.total_seconds() < 0:
        update.effective_chat.send_message("A show is currently live or just ended!")
        return

    deltastr = "{} days, {:02}:{:02}".format(
        delta.days, delta.seconds // (60 * 60), (delta.seconds // 60) % 60
    )
    update.effective_chat.send_message(
        text="The next {} is {}. That's {} from now.".format(
            show_names[domain], datestr, deltastr
        )
    )


def report(bot, update):
    """Bot /report callback
    Gives instructions for reporting problems
    In the future, may support "Forward me any problem messages", etc"""

    update.message.reply_text(
        text=(
            "Please forward the problem messages and a brief explanation"
            " to @RawrJesse, @rileywd, @s0ph0s, or another op."
        )
    )


def start(bot, update):
    """Bot /start callback
    Gives user invite link button"""

    if update.effective_chat.type != "private":
        return
    update.message.reply(
        text=join_template.format(fname=update.message.from_user.first_name),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(text=button_text, url=join_link)]]
        ),
    )


def topic(bot, update):
    """Bot /topic callback
    Changes chat title, if allowed"""

    if (
        update.effective_chat.type == "private"
        or str(update.effective_chat.id) not in allow_topics
    ):
        return

    parts = update.message.text.split(" ", 1)
    requested = ""
    if len(parts) > 1 and len(parts[1]) > 0 and parts[1] != "clear":
        requested = " – " + parts[1]
    title = update.effective_chat.title.split(" – ", 1)[0] + requested
    try:
        bot.set_chat_title(update.effective_chat.id, title)
    except telegram.error.BadRequest as e:
        logging.warning("Title change failed in %s: %s", update.effective_chat.id, e)


def version(bot, update):
    """Bot /version callback
    Posts bot info and Cloud Function version"""

    update.effective_chat.send_message(
        "[furcast-tg-bot](https://git.xbn.fm/xbn/furcast-tg-bot)\n"
        "GCF version {}".format(os.environ["X_GOOGLE_FUNCTION_VERSION"]),
        disable_web_page_preview=True,
        parse_mode=ParseMode.MARKDOWN,
    )


def webhook(request):
    logging.info("access_route: %s", ",".join(request.access_route))
    logging.info("args: %s", request.args)
    logging.info("data: %s", request.data)
    logging.info("form: %s", request.form)
    if request.args.get("apikey") != apikey:
        return make_response("", 404)
    if "version" in request.args:
        return os.environ["X_GOOGLE_FUNCTION_VERSION"] + "\n"
    if request.form.get("group") in group_ids:
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


dispatcher.add_handler(CommandHandler("chatinfo", chatinfo))
dispatcher.add_handler(CommandHandler("next", nextshow))
dispatcher.add_handler(CommandHandler("report", report))
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("topic", topic))
dispatcher.add_handler(CommandHandler("version", version))
