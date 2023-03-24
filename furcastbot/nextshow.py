from __future__ import annotations

from datetime import datetime
from datetime import timezone
import logging

from dateutil import tz
from ddate.base import DDate
import requests
from telegram import Update
from telegram.constants import ParseMode
import telegram.error
from telegram.ext import CallbackContext

from .config import Config

config = Config.get_config()


def beat(showtime: datetime) -> str:
    showtimez = showtime.astimezone(tz.gettz("UTC+1"))
    beatseconds = (
        showtimez - showtimez.replace(hour=0, minute=0, second=0, microsecond=0)
    ).total_seconds()
    beats = beatseconds / (60 * 60 * 24) * 1000
    return showtimez.strftime(f"d%d.%m.%y @{beats:03.0f}")


async def next_pin_callback(context: CallbackContext) -> None:
    """Creates/updates pin for next show info
    Called every minute by JobQueue after eg. /next fc pin
    """

    job_data = context.job.data
    logging.debug(
        "Running next-pin job for %s (%s)", job_data["chat"].title, job_data["chat"].id
    )
    show = config.shows[job_data["slug"]]
    delta = job_data["showtime"] - datetime.now(tz=timezone.utc)

    if delta.total_seconds() < 0:
        context.job.schedule_removal()
        text = "<a href='https://{}/'>{}</a> is starting!".format(
            show["domain"], show["name"]
        )
        job_data["message"].edit_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        # Need a new copy to get the current pinned_message
        chat = context.bot.get_chat(job_data["chat"].id)
        if (
            getattr(chat.pinned_message, "message_id", None)
            == job_data["message"].message_id
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
        if job_data["message"] is None:
            job_data["message"] = await job_data["chat"].send_message(
                text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
            try:
                await context.bot.pin_chat_message(
                    job_data["chat"].id,
                    job_data["message"].message_id,
                    disable_notification=True,
                )
            except telegram.error.BadRequest as e:
                # Usually "Not enough rights to pin a message"
                logging.warning(
                    "Next-show pin failed in %s: %s", job_data["chat"].id, e
                )
        else:
            try:
                await job_data["message"].edit_text(
                    text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
            except telegram.error.BadRequest as e:
                if e.message == "Message to edit not found":
                    logging.debug("Next-show pinned message deleted, removing job")
                    context.job.schedule_removal()
                elif "exactly the same" not in e.message:
                    raise e
    except Exception as e:
        logging.error("Next-show job failed: %s: %s", job_data["chat"].id, e)
        context.job.schedule_removal()
        raise e


async def nextshow(update: Update, context: CallbackContext) -> None:
    """Bot /next callback
    Posts the next scheduled show for a given slug/name and timezone"""

    args = update.message.text.split()

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
        user = await update.effective_chat.get_member(update.effective_user.id)
        if not user.can_pin_messages and user.status != "creator":
            await update.message.reply_text(text="You aren't allowed to do that")
            return
        job_data = {
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
            data=job_data,
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
            tzlink = "https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
            # TZ or show error
            text = (
                "Sorry, I don't understand.\nFor timezones, try e.g. "
                "<code>America/Chicago</code> or another from the "
                "<a href='{}'>tzdata list</a>".format(tzlink)
            )
            await update.message.reply_text(
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return
        datestr = (
            showtime.astimezone(tzobj).strftime("%a %e %b, %H:%M %Z").replace("  ", " ")
        )

    delta = showtime - datetime.now(tz=timezone.utc)
    if delta.total_seconds() < 0:
        await update.effective_chat.send_message(
            "A show is currently live or just ended!"
        )
        return

    deltastr = "{} days, {:02}:{:02}".format(
        delta.days, delta.seconds // (60 * 60), (delta.seconds // 60) % 60
    )
    await update.effective_chat.send_message(
        text="The next {} is {}. That's {} from now.".format(
            show["name"], datestr, deltastr
        )
    )
