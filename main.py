#!/usr/bin/env python3

from datetime import datetime
from dateutil import tz
from ddate.base import DDate
from dotenv import load_dotenv
from flask import make_response, Response, Request
from html import escape
import logging
import os
import requests
from telegram import (
    Bot,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ParseMode,
    Update,
)
import telegram.error
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
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


class Chats(object):
    xbn = -1001170434051
    xbn_chatops = -1001498895240
    furcast = -1001462860928
    riley_test_channel = -1001263448135
    riley_test_group = -1001422900025
    xana_ts = -1001195641999


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
# Channels to allow /topic requests - None = no approval required
allow_topics = {
    Chats.xana_ts: None,
    Chats.furcast: Chats.xbn_chatops,
    Chats.riley_test_group: Chats.riley_test_channel,
}

timezones = {  # Additional mappings
    "eastern": "America/New_York",
    "et": "America/New_York",
}

invite_chat = Chats.furcast
admin_chat = Chats.xbn_chatops

join_link = os.environ.get("JOIN_LINK")
apikey = os.environ["APIKEY"]

logging.basicConfig(level=logging.INFO)
logging.getLogger("telegram").setLevel(logging.INFO)
logging.getLogger("JobQueue").setLevel(logging.INFO)
if __name__ == "__main__":  # Poll bot
    updater = Updater(token=os.environ["TELEGRAM_TOKEN"], use_context=True)
    dispatcher = updater.dispatcher
else:  # Webhook bot
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
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


def next_pin_callback(context: CallbackContext) -> None:
    """Creates/updates pin for next show info
    Called every minute by JobQueue after eg. /next fc pin
    """

    ctx = context.job.context
    logging.debug("Running next-pin job for %s (%s)", ctx["chat"].title, ctx["chat"].id)
    delta = ctx["showtime"] - datetime.utcnow()

    if delta.total_seconds() < 0:
        context.job.schedule_removal()
        ctx["message"].edit_text("Show starting!")
        # Need a new copy to get the current pinned_message
        chat = context.bot.get_chat(ctx["chat"].id)
        if getattr(chat.pinned_message, "message_id", None) == ctx["message"].message_id:
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
    text = (show_names[domains[ctx["slug"]]] +
            f" starts in {daystr}{hourstr}{minutestr}")
    try:
        if ctx["message"] is None:
            ctx["message"] = ctx["chat"].send_message(text)
            try:
                context.bot.pin_chat_message(
                    ctx["chat"].id, ctx["message"].message_id, disable_notification=True
                )
            except telegram.error.BadRequest as e:
                # Usually "Not enough rights to pin a message"
                logging.warning("Next-show pin failed in %s: %s", chat_id, e)
        else:
            try:
                ctx["message"].edit_text(text)
            except telegram.error.BadRequest as e:
                if "exactly the same" not in e.message:
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

    # Start update job
    if "pin" in args:
        try:
            chat_user_status = update.effective_chat.get_member(
                update.effective_user.id
            ).status
        except telegram.error.BadRequest as e:
            logging.warning(
                "Failed to get chat user %s (%s): %s",
                update.effective_user.name,
                update.effective_user.id,
                e,
            )
            update.message.reply_text(text="Error checking permissions")
            return
        if chat_user_status not in ["administrator", "creator"]:
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
        updater.job_queue.run_repeating(
            next_pin_callback,
            60,
            0,
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

    delta = showtime - datetime.utcnow()
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


def report(update: Update, context: CallbackContext) -> None:
    """Bot /report callback
    Gives instructions for reporting problems
    In the future, may support "Forward me any problem messages", etc"""

    update.message.reply_text(
        text=(
            "Please forward the problem messages and a brief explanation"
            " to @RawrJesse, @rileywd, @s0ph0s, or another op."
        )
    )


def replace_invite_link(update: Update, context: CallbackContext) -> None:
    """Bot /newlink callback
    Replaces bot's invite link for {invite_chat}
    NOTE: Each admin has a DIFFERENT INVITE LINK."""

    if update.effective_chat.id != admin_chat:
        update.message.reply_text("Unauthorized")
        return

    logging.info(
        "%s (%s) requested invite link rotation",
        update.effective_user.name,
        update.effective_user.id,
    )
    try:
        bot_join_link = updater.bot.export_chat_invite_link(invite_chat)
        if bot_join_link is None:
            raise Exception("exportChatInviteLink returned None")
    except Exception as e:
        logging.error("Invite link rotation failed: %s", e)
        update.message.reply_text("Invite link rotation failed: " + str(e))
        return
    global join_link
    join_link = bot_join_link
    logging.info("New bot invite link: %s", join_link)
    update.message.reply_text(
        "Success. Bot's new invite link: " + join_link, disable_web_page_preview=True
    )


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


def topic(update: Update, context: CallbackContext) -> None:
    """Bot /topic callback
    Changes chat title, if allowed"""

    # No PMs
    if update.effective_chat.type == "private":
        update.message.reply_text("Sorry, that only works in groups.")
        return

    parts = update.message.text.split(" ", 1)
    if len(parts) < 2 or len(parts[1].strip()) == 0:
        update.message.reply_markdown(
            f"Try e.g. `{parts[0]} Not My Cup Of Legs` to suggest "
            f"a chat topic, or `{parts[0]} -` to clear"
        )
        return

    # Get just the requested title
    if parts[1].strip().lower() in ["-delete", "-", '""']:
        requested = ""
    else:
        requested = parts[1].strip()

    # Unrestricted chat, or admin
    try:
        chat_user_status = update.effective_chat.get_member(
            update.effective_user.id
        ).status
    except telegram.error.BadRequest as e:
        logging.warning(
            "Failed to get chat user %s (%s): %s",
            update.effective_user.name,
            update.effective_user.id,
            e,
        )
        chat_user_status = None
    if (
        update.effective_chat.id in allow_topics
        and allow_topics[update.effective_chat.id] is None
    ) or chat_user_status in ["administrator", "creator"]:
        logging.info(
            "%s: %s: %s",
            update.effective_chat.title,
            update.effective_user.username,
            update.message.text,
        )
        # If silent change
        if parts[0] == "/stopic":
            try:
                update.message.delete()
            except telegram.error.BadRequest as e:
                logging.warning(
                    "stopic message delete failed in %s: %s",
                    update.effective_chat.id,
                    e,
                )
        topic_set(context.bot, update.effective_chat, requested)
        return

    if update.effective_chat.id in allow_topics:

        mention = update.message.from_user.mention_markdown()
        link = update.message.link
        callback_data = ",{},{},{},{}".format(
            update.effective_chat.id,
            update.effective_user.id,
            update.message.message_id,
            requested,
        )
        if len(callback_data) + 2 > 64:
            update.message.reply_text("Sorry, that's too long.")
            return
        context.bot.send_message(
            allow_topics[update.effective_chat.id],
            (
                f'{mention} [proposed]({link}) topic "{requested}"\n'
                "Admins can accept, admins or op can reject:"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Accept", callback_data="ta" + callback_data
                        ),
                        InlineKeyboardButton(
                            "Reject", callback_data="tr" + callback_data
                        ),
                    ]
                ]
            ),
            disable_notification=True,
        )
        update.message.reply_text(f'Requested topic "{requested}"')
        return


def button(update: Update, context: CallbackContext) -> None:
    """Bot button callback"""

    data = update.callback_query.data
    # Topic accept/reject buttons
    if data.startswith("t"):
        action, chat_id, user_id, message_id, requested = data.split(",", 4)
        chat_id = int(chat_id)
        target_chat = context.bot.get_chat(chat_id)
        user_id = int(user_id)
        message_id = int(message_id)

        # Get user's perms in /topic'd chat
        try:
            chat_user_status = target_chat.get_member(update.effective_user.id).status
        except telegram.error.BadRequest as e:
            logging.warning(
                "Failed to get topic'd chat user %s (%s): %s",
                update.effective_user.name,
                update.effective_user.id,
                e,
            )
            chat_user_status = None

        # Not authorized
        if not (
            # Topic requester can reject their own
            (update.effective_user.id == user_id and action == "tr")
            # Admins in the /topic'd chat can approve
            or chat_user_status in ["administrator", "creator"]
            # Admin group approval: allow anyone
            or update.effective_chat.id == allow_topics[chat_id]
        ):
            update.callback_query.answer(text="Nice try")
            return
        logging.info(
            "%s: %s: %s bytes: %s",
            update.effective_chat.title,
            update.effective_user.username,
            len(data),
            data,
        )

        # Buttons
        if action == "ta":
            topic_set(context.bot, context.bot.get_chat(chat_id), requested)
            if allow_topics[chat_id] != chat_id:
                context.bot.send_message(
                    chat_id, "Accepted!", reply_to_message_id=message_id
                )
            update.callback_query.answer(text="Accepted")
            update.callback_query.message.edit_text(
                update.callback_query.message.text_markdown
                + f"\nApproved by "
                + update.effective_user.mention_markdown(),
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        elif action == "tr":
            update.callback_query.answer(text="Rejected")
            update.callback_query.message.edit_text(
                update.callback_query.message.text_markdown
                + f"\nRejected by "
                + update.effective_user.mention_markdown(),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    logging.error("Button didn't understand callback: %s", data)


def topic_set(bot: Bot, chat: Chat, requested_topic: str) -> None:
    """Enact a topic change"""

    logging.info(
        '%s: Setting topic "%s"', chat.title, requested_topic,
    )

    sep = " – "
    requested_topic = requested_topic.strip()
    if len(requested_topic) > 0:
        requested_topic = sep + requested_topic
    title = chat.title.split(sep, 1)[0] + requested_topic
    try:
        bot.set_chat_title(chat.id, title)
    except telegram.error.BadRequest as e:
        logging.warning("Title change failed in %s: %s", chat.id, e)


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
dispatcher.add_handler(CommandHandler("newlink", replace_invite_link))
dispatcher.add_handler(CommandHandler("next", nextshow))
dispatcher.add_handler(CommandHandler("report", report))
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("topic", topic))
dispatcher.add_handler(CommandHandler("stopic", topic))
dispatcher.add_handler(CommandHandler("version", version))
dispatcher.add_handler(CallbackQueryHandler(button))

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
