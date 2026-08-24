"""Discord adapter and process entrypoint."""

from __future__ import annotations

import logging

import discord

from bot_runtime import BotRuntime
from bot_state import get_data_dir
from settings import load_settings


def build_discord_intents() -> discord.Intents:
    """Return the minimal Discord intents needed for command message handling."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.dm_messages = True
    intents.message_content = True
    return intents


def configure_logging() -> None:
    """Configure application logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
    )


def main() -> None:
    """Construct the Discord adapter and start the client."""
    settings = load_settings()
    configure_logging()
    client = discord.Client(intents=build_discord_intents())
    runtime = BotRuntime(
        client,
        command_delimiter=settings.delim,
        data_dir=get_data_dir(),
    )
    client.event(runtime.on_ready)
    client.event(runtime.on_guild_join)
    client.event(runtime.on_guild_remove)
    client.event(runtime.on_message)
    client.run(settings.discord_token.get_secret_value())


if __name__ == "__main__":
    main()
