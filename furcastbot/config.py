from __future__ import annotations

from datetime import timedelta
import logging
import os
from typing import Dict, List, Optional

from tomlkit.toml_file import TOMLFile


class Config:
    _config_file: str
    """The config file that was loaded."""

    config: dict
    """The data read from the config file.

    Do not attempt to write this data back to the file!
    This structure is modified during loading.
    """

    chat_map: Dict[str, dict]
    """A map of chat IDs to chat object."""

    managed_chats: Dict[str, List[str]]
    """A map of admin chat IDs to a list of chat names they manage."""

    timezones: Dict[str, str]
    """A map of timezone alias to canonical timezone name."""

    join_rate_limit_delay: Dict[str, timedelta]
    """A map of chat IDs to join delays."""

    _instance: Config = None
    """The singleton instance"""

    @classmethod
    def get_config(cls, config_file: Optional[str] = None):
        """Return the singleton, after creating it as required."""
        if cls._instance is None:
            cls._instance = cls(config_file)
        return cls._instance

    def __init__(self, config_file: Optional[str] = None):
        if config_file is None:
            if "CONFIG" in os.environ:
                config_file = os.environ["CONFIG"]
            else:
                config_file = os.path.join(os.getcwd(), "config.toml")

        if not os.path.exists(config_file):
            logging.critical("Config file does not exist: %r", config_file)
            raise Exception("Could not read configuration")

        self._config_file = config_file
        self.load()

    def load(self):
        logging.info("Loading config from %r...", self._config_file)
        new_config = TOMLFile(self._config_file).read()

        # show slugs and copy show references to alias names
        for slug, show in new_config["shows"].items():
            show["slug"] = slug
            alias_dict = {alias: show for alias in show.get("aliases", [])}
            alias_dict.update(new_config["shows"])
            new_config["shows"] = alias_dict

        # chat slugs
        for slug, chat in new_config["chats"].items():
            chat["slug"] = slug

        # chat ID -> chat object
        new_chat_map = {chat["id"]: chat for chat in new_config["chats"].values()}

        # admin chat ID -> [managed chat names]
        new_managed_chats = {}
        for name, chat in new_config["chats"].items():
            if "admin_chat" not in chat:
                continue
            admin_chat_id = new_config["chats"][chat["admin_chat"]]["id"]
            if admin_chat_id not in new_managed_chats:
                new_managed_chats[admin_chat_id] = []
            new_managed_chats[admin_chat_id].append(name)

        # timezone alias -> canonical timezone name
        new_timezones = {}
        for canonical, aliases in new_config["timezones"].items():
            new_timezones.update({alias: canonical for alias in aliases})

        new_join_delay = {
            chat["id"]: timedelta(minutes=chat.get("rate_limit_delay_minutes", 0))
            for chat in new_config["chats"].values()
        }

        (
            self.config,
            self.chat_map,
            self.managed_chats,
            self.timezones,
            self.join_rate_limit_delay,
        ) = (
            new_config,
            new_chat_map,
            new_managed_chats,
            new_timezones,
            new_join_delay,
        )

    @property
    def chats(self):
        return self.config["chats"]

    @property
    def shows(self):
        return self.config["shows"]
