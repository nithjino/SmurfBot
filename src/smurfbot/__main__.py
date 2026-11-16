"""Process entrypoint: settings, logging, the data directory lock, and the client."""

import logging
import os
import sys

import discord

from smurfbot.bot import SmurfBot
from smurfbot.config import ConfigError, load_settings
from smurfbot.store import STATE_FILE, DataDirLockedError, StateFileError, Store, data_dir_lock


def main() -> None:
    """Run the bot until it is stopped."""
    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        sys.exit(str(exc))
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        with data_dir_lock(settings.data_dir):
            store = Store(settings.data_dir / STATE_FILE)
            SmurfBot(store, settings.prefix).run(settings.token, log_handler=None)
    except (DataDirLockedError, StateFileError) as exc:
        sys.exit(str(exc))
    except discord.LoginFailure:
        sys.exit("Discord rejected the token in SMURFBOT_DISCORD_TOKEN")


if __name__ == "__main__":
    main()
