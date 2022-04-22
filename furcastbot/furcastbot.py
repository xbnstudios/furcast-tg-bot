#!/usr/bin/env python3

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import logging
import os

from dateutil import tz
from ddate.base import DDate
from flask import make_response, Request
import requests
from telegram import (
    Bot,
    ParseMode,
    Update,
)
import telegram.constants
import telegram.error
from telegram.ext import (
    CallbackContext,
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


def beat(showtime: datetime) -> str:
    showtimez = showtime.astimezone(tz.gettz("UTC+1"))
    beatseconds = (
        showtimez - showtimez.replace(hour=0, minute=0, second=0, microsecond=0)
    ).total_seconds()
    beats = beatseconds / (60 * 60 * 24) * 1000
    return showtimez.strftime(f"d%d.%m.%y @{beats:03.0f}")


def next_pin_callback(context: CallbackContext) -> None:
    """Creates/updates pin for next show info
    Called every minute by JobQueue after eg. /next fc pin
    """

    ctx = context.job.context
    logging.debug("Running next-pin job for %s (%s)", ctx["chat"].title, ctx["chat"].id)
    show = config.shows[ctx["slug"]]
    delta = ctx["showtime"] - datetime.now(tz=timezone.utc)

    if delta.total_seconds() < 0:
        context.job.schedule_removal()
        text = "<a href='https://{}/'>{}</a> is starting!".format(
            show["domain"], show["name"]
        )
        ctx["message"].edit_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        # Need a new copy to get the current pinned_message
        chat = context.bot.get_chat(ctx["chat"].id)
        if (
            getattr(chat.pinned_message, "message_id", None)
            == ctx["message"].message_id
        ):
            context.bot.unpin_chat_message(chat.id)
        return

    daystr = "" if delta.days < 1 else f"{delta.days} days, "
    hours = delta.seconds // (60 * 60)
    if hours > 0 or delta.days > 0:
        hourstr = str(hours) + (" hour" if hours == 1 else " hours") + ", "
    else:
        hourstr = ""
    minutes = (delta.seconds // 60) % 60
    minutestr = str(minutes) + (" minute" if minutes == 1 else " minutes")
    text = "<a href='https://{}/'>{}</a> starts in {}{}{}".format(
        show["domain"],
        show["name"],
        daystr,
        hourstr,
        minutestr,
    )
    try:
        if ctx["message"] is None:
            ctx["message"] = ctx["chat"].send_message(
                text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
            try:
                context.bot.pin_chat_message(
                    ctx["chat"].id, ctx["message"].message_id, disable_notification=True
                )
            except telegram.error.BadRequest as e:
                # Usually "Not enough rights to pin a message"
                logging.warning("Next-show pin failed in %s: %s", ctx["chat"].id, e)
        else:
            try:
                ctx["message"].edit_text(
                    text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
            except telegram.error.BadRequest as e:
                if e.message == "Message to edit not found":
                    logging.debug("Next-show pinned message deleted, removing job")
                    context.job.schedule_removal()
                elif "exactly the same" not in e.message:
                    raise e
    except Exception as e:
        logging.error("Next-show job failed: %s: %s", ctx["chat"].id, e)
        context.job.schedule_removal()
        raise e


def nextshow(update: Update, context: CallbackContext) -> None:
    """Bot /next callback
    Posts the next scheduled show for a given slug/name and timezone"""

    args = update.message.text.split(" ")

    # Which show
    if len(args) > 1 and args[1].lower() in config.shows:
        slug = args[1].lower()
    else:
        slug = config.chat_map[update.effective_chat.id]["next_show_default"]
        args.insert(1, "")  # reverse shift to offer timezone
    show = config.shows[slug]
    domain = show["domain"]

    try:
        r = requests.get("https://{}/nextshow/".format(domain))
        if r.status_code != 200:
            raise Exception("API returned " + str(r.status_code))
    except Exception as e:
        update.message.reply_text(text="Error: " + str(e))
        raise e
    showtime = datetime.fromtimestamp(int(r.text), tz=timezone.utc)

    # Start update job
    if "pin" in args:
        user = update.effective_chat.get_member(update.effective_user.id)
        if not user.can_pin_messages and user.status != "creator":
            update.message.reply_text(text="You aren't allowed to do that")
            return
        ctx = {
            "chat": update.effective_chat,
            "message": None,
            "slug": slug,
            "showtime": showtime,
        }
        logging.info(
            "Scheduled next-pin job, %s (%s) for %s",
            update.effective_user.name,
            update.effective_user.id,
            update.effective_chat.title,
        )
        context.job_queue.run_repeating(
            next_pin_callback,
            60,
            1,  # 0 results in no first-run, probably a library bug...
            context=ctx,
            name=f"next_pin_{update.effective_chat.id}",
        )
        return

    # Timezones
    if len(args) < 3:  # no TZ
        tzstr = "America/New_York"
    else:
        tzstr = args[2]

    if tzstr.lower() in ["ddate", "discordian"]:
        datestr = str(DDate(showtime))
        if datestr.startswith("Today is "):
            datestr = datestr[9:]
    elif tzstr.lower() in ["beat", "swatch", "internet"]:
        datestr = beat(showtime)
    else:
        if tzstr.lower() in config.timezones:  # custom map
            tzstr = config.timezones[args[2].lower()]
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

    delta = showtime - datetime.now(tz=timezone.utc)
    if delta.total_seconds() < 0:
        update.effective_chat.send_message("A show is currently live or just ended!")
        return

    deltastr = "{} days, {:02}:{:02}".format(
        delta.days, delta.seconds // (60 * 60), (delta.seconds // 60) % 60
    )
    update.effective_chat.send_message(
        text="The next {} is {}. That's {} from now.".format(
            show["name"], datestr, deltastr
        )
    )


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
