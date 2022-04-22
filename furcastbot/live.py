from __future__ import annotations

import logging

from flask import make_response, Response
from telegram import (
    Bot,
    ParseMode,
)
import telegram.constants
import telegram.error

from .config import Config

config = Config.get_config()


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


def post_np_group(bot: Bot, group_id: int, text: str, oneshot: bool = False) -> None:
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
            logging.warning("post_np_group pin failed in %s: %s", chat, e)
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


def post_np(bot: Bot, title: str, show_slug: str) -> None:
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
            post_np_group(bot, group_id, text)
        except Exception as e:
            logging.error("post_np failed: %s: %s", show_slug, e)
            raise e

        # context.bot.unpin_chat_message(chat.id)

    return make_response({"status": "OK"}, 200)
