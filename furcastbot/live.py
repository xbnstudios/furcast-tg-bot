from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from flask import make_response, Response
from telegram import Bot
from telegram.constants import ParseMode
import telegram.error

from .config import Config

if TYPE_CHECKING:
    from flask import Request

config = Config.get_config()


def webhook(request: Request):
    return asyncio.run(webhook_real(request))


# Have GCF call this directly once it supports flask async
async def webhook_real(request: Request):
    bot = Bot(token=config.config["telegram_token"])
    logging.info("access_route: %s", ",".join(request.access_route))
    logging.info("args: %s", request.args)
    logging.info("data: %s", request.data)
    logging.info("form: %s", request.form)
    if "api_key" not in config.config or (
        request.args.get("apikey") != config.config["api_key"]
        and request.form.get("apikey") != config.config["api_key"]
    ):
        logging.error("Incorrect apikey")
        return make_response("", 404)
    if "title" in request.form:
        return await post_np(bot, request.form["title"], request.form.get("show"))
    if request.form.get("group") in config.config["announce"]:
        pin = request.form.get("pin")
        if pin in ["true", "1"]:
            pin = True
        elif pin in ["false", "0"]:
            pin = False
        notify = True if request.form.get("notify") in ["true", "1"] else False
        forward = True if request.form.get("forward") in ["true", "1"] else False
        return await post_pin(
            bot,
            request.form["group"],
            request.form.get("message"),
            pin,
            notify,
            forward,
        )


async def post_pin(
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
        root_message = await bot.send_message(
            announce_list[0], message, disable_notification=not notify
        )
        sent_messages = {announce_list[0]: root_message}

        if forward:
            for target_chat_id in list(announce_list)[1:]:
                sent_messages[target_chat_id] = await bot.forward_message(
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
                    await bot.pin_chat_message(
                        chat_id, message.message_id, disable_notification=True
                    )
                except telegram.error.BadRequest as e:
                    # Usually "Not enough rights to pin a message"
                    logging.warning("Pin failed in %s: %s", chat_id, e)

    if pin is False:
        for chat_id in announce_list:
            try:
                await bot.unpin_chat_message(chat_id)
            except telegram.error.BadRequest as e:
                # Usually "Not enough rights to unpin a message"
                logging.warning("Unpin failed in %s: %s", chat_id, e)
    return make_response({"status": "OK"}, 200)


async def post_np_group(
    bot: Bot, group_id: int, text: str, oneshot: bool = False
) -> None:
    chat = await bot.get_chat(group_id)
    pin_id = getattr(chat.pinned_message, "message_id", None)

    if pin_id is None:
        pin = await chat.send_message(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        try:
            await bot.pin_chat_message(
                chat.id, pin.message_id, disable_notification=True
            )
        except telegram.error.BadRequest as e:
            # Usually "Not enough rights to pin a message"
            logging.warning("post_np_group pin failed in %s: %s", chat, e)
    else:
        try:
            await bot.edit_message_text(
                text,
                group_id,
                pin_id,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except telegram.error.BadRequest as e:
            if "can't be edited" in e.message:
                if not oneshot:  # Try once to unpin/post
                    await bot.unpin_chat_message(chat.id)
                    return await post_np_group(group_id, text, oneshot=True)
                return make_response({"status": "Error", "error": "Not my pin"}, 200)
            if "exactly the same" not in e.message:
                raise e


async def post_np(bot: Bot, title: str, show_slug: str) -> None:
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
    for group_long_slug in groups:
        group_id = config.config["chats"][group_long_slug]["id"]
        try:
            await post_np_group(bot, group_id, text)
        except Exception as e:
            logging.error("post_np failed: %s: %s", show_slug, e)
            raise e

        # await context.bot.unpin_chat_message(chat.id)

    return make_response({"status": "OK"}, 200)
