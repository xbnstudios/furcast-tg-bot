#!/usr/bin/env python3

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import logging
import os

from dateutil import tz
from ddate.base import DDate
from flask import make_response, Request, Response
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
from .membership import chat_join_request, revoke_invite_links, start
from .report import report, report_mention_wrapper
from .topics import button, topic

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


def post_pin(
    bot: Bot, group: str, message=None, pin=None, notify=False, forward=False
) -> Response:
    """Post a message to a group, pin/unpin
    :bot: The telegram Bot object
    :group: The group slug, ie "fc", to match ``announce`` entry
    :message: None, or the message to post
    :pin: None or True/False, whether or not to quiet-pin in first chat
    :notify: True/False, enable notify for channel messages
    :forward: True/False, forward from the first chat to the others
    """

    if group not in config.config["announce"]:
        return make_response({"status": "Error", "message": "Unknown group"}, 400)

    announce_list = config.config["announce"][group]

    if message is not None:
        root_message = bot.send_message(
            announce_list[0], message, disable_notification=not notify
        )
        sent_messages = {announce_list[0]: root_message}

        if forward:
            for target_chat_id in announce_list[1:]:
                sent_messages[target_chat_id] = bot.forward_message(
                    target_chat_id,
                    root_message.chat_id,
                    root_message.message_id,
                    not notify,
                )

        if notify is True and pin is not False:  # quiet-pin in all chats
            for chat_id, message in sent_messages.items():
                # Don't pin in channels / first-groups
                if chat_id == announce_list[0]:
                    continue
                try:
                    bot.pin_chat_message(
                        chat_id, message.message_id, disable_notification=True
                    )
                except telegram.error.BadRequest as e:
                    # Usually "Not enough rights to pin a message"
                    logging.warning("Pin failed in %s: %s", chat_id, e)

    if pin is False:
        for chat_id in announce_list:
            try:
                bot.unpin_chat_message(chat_id)
            except telegram.error.BadRequest as e:
                # Usually "Not enough rights to unpin a message"
                logging.warning("Unpin failed in %s: %s", chat_id, e)
    return make_response({"status": "OK"}, 200)


def beat(showtime: datetime) -> str:
    showtimez = showtime.astimezone(tz.gettz("UTC+1"))
    beatseconds = (
        showtimez - showtimez.replace(hour=0, minute=0, second=0, microsecond=0)
    ).total_seconds()
    beats = beatseconds / (60 * 60 * 24) * 1000
    return showtimez.strftime(f"d%d.%m.%y @{beats:03.0f}")


def post_np_group(group_id: int, text: str, oneshot: bool = False) -> None:
    chat = bot.get_chat(group_id)
    pin_id = getattr(chat.pinned_message, "message_id", None)

    if pin_id is None:
        pin = chat.send_message(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        try:
            bot.pin_chat_message(chat.id, pin.message_id, disable_notification=True)
        except telegram.error.BadRequest as e:
            # Usually "Not enough rights to pin a message"
            logging.warning("post_pin pin failed in %s: %s", chat, e)
    else:
        try:
            bot.edit_message_text(
                text,
                group_id,
                pin_id,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except telegram.error.BadRequest as e:
            if "can't be edited" in e.message:
                if not oneshot:  # Try once to unpin/post
                    bot.unpin_chat_message(chat.id)
                    return post_np_group(group_id, text, oneshot=True)
                return make_response({"status": "Error", "error": "Not my pin"}, 200)
            if "exactly the same" not in e.message:
                raise e


def post_np(title: str, show_slug: str) -> None:
    """Creates/updates pin for Now Playing
    Called by Gelo
    """

    logging.debug("Now playing on %r: %r", show_slug, title)

    if show_slug not in config.config["announce"]:
        return make_response({"status": "Error", "error": "Unknown show slug"}, 404)

    show = config.shows[show_slug]

    text = "Now playing: {title}\n🎵 {show_name} is live!\n"
    if show != "dd":
        text += "📺 <a href='https://{domain}/video/'>Watch</a> "
    text += (
        "🎧 <a href='https://{domain}/audio/'>Listen</a> "
        "💬 <a href='https://{domain}/chat/'>Chat</a> "
    )
    text = text.format(title=title, show_name=show["name"], domain=show["domain"])

    groups = config.config["announce"].get(show_slug + "-np")
    if groups is None:
        return make_response(
            {"status": "Error", "error": "No now-playing chat for show"}, 200
        )
    for group_id in groups:
        try:
            post_np_group(group_id, text)
        except Exception as e:
            logging.error("post_np failed: %s: %s", show_slug, e)
            raise e

        # context.bot.unpin_chat_message(chat.id)

    return make_response({"status": "OK"}, 200)


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


def version(update: Update, context: CallbackContext) -> None:
    """Bot /version callback
    Posts bot info and Cloud Function version"""

    update.effective_chat.send_message(
        "<a href='https://github.com/xbnstudios/furcast-tg-bot'>furcast-tg-bot</a>\n"
        "GCF version: {}".format(os.environ.get("X_GOOGLE_FUNCTION_VERSION")),
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML,
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
        return post_np(request.form["title"], request.form.get("show"))
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
