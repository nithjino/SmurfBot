#!/usr/bin/env python3
"""Discord bot entrypoint and command dispatch."""

from __future__ import annotations

import argparse
import configparser
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Final

import discord

from discord_messages import send_command_response
from models import CommandParameters
from reminders import Reminders
from reminders.constants import MIN_REMIND_ARGUMENTS
from tags import Tags

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_logger = logging.getLogger(__name__)
BOT_PATH: Final[Path] = Path(__file__).resolve().parent
DEFAULT_DATA_DIR: Final[Path] = BOT_PATH.parent / "data"
DATA_DIR_ENV_VAR: Final[str] = "SMURFBOT_DATA_DIR"
DELIM: Final[str] = "$"
GENERIC_COMMAND_ERROR: Final[str] = "Sorry, something went wrong while handling that command."


def build_discord_intents() -> discord.Intents:
    """Return the minimal Discord intents needed for command message handling."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.dm_messages = True
    intents.message_content = True
    return intents


client = discord.Client(intents=build_discord_intents())


async def post_help(_parameters: CommandParameters | None = None) -> str:
    """:return: a string containing what commands the bot has"""
    return "The commands are: tag, git, and remind. Each one has their own help command except for git."


tags: dict[int, Tags] = {}
reminders: dict[int, Reminders] = {}


async def ping(parameters: CommandParameters) -> str:
    """Respond to the ping command."""
    _logger.info("ping parameters: %s", parameters)
    return "pong"


async def parse_tag_commands(parameters: CommandParameters) -> str:
    """Route tag command parameters to the guild's tag handler."""
    _logger.info("parse_tag_commands parameters: %s", parameters)
    # getting the Tag obj for that discord space
    guild_id = parameters.guild_id
    if guild_id is None:
        return "unable to get tag. guild_id is None"
    tag = tags.get(guild_id)
    if tag is None:
        return "unable to get tag. tag function parameter is None"
    return await tag.parse_commands(parameters.model_dump())


async def parse_remind_commands(parameters: CommandParameters) -> str:
    """Route reminder command parameters to the guild's reminder handler."""
    _logger.info("parse_remind_commands parameters: %s", parameters)
    channel_id = parameters.channel_id
    guild_id = parameters.guild_id
    if guild_id is None or channel_id is None:
        return f"guild_id ({guild_id}) or channel_id {channel_id} is None."
    # getting the Reminders obj for that discord space
    r = reminders.get(guild_id)
    if r is None:
        return ""
    command_message = parameters.message
    time = command_message[0] if command_message else "help"
    message = command_message[1:]
    fetch_user_func = parameters.fetch_user_func
    created_at = None  # can't convert the time given from discord to EST so I will just use datetime.now()
    user = parameters.author_id
    if len(command_message) < MIN_REMIND_ARGUMENTS:
        time = "help"
    return await r.create_reminder(
        time,
        message,
        user,
        created_at,
        fetch_user_func,
        guild_id,
        channel_id,
        user_name=parameters.author_name,
    )


async def mock(parameters: CommandParameters | str) -> str:
    """Return the supplied message in mocking text format."""
    _logger.info("mock parameters: %s", parameters)
    if isinstance(parameters, str):
        message = parameters.lower().strip()
    else:
        message = " ".join(parameters.message).lower().strip()

    result = ""
    for index, character in enumerate(message):
        if character.isspace():
            result = result + " "
            continue

        previous_character_is_upper = (
            index > 0 and result[-2].isupper() if message[index - 1].isspace() else index > 0 and result[-1].isupper()
        )
        result += character if previous_character_is_upper else character.upper()

    return result


async def git(_parameters: CommandParameters | None = None) -> str:
    """:return: a url of the git repo of the source code"""
    git_url = "https://github.com/nithjino/SmurfBot"
    return f"Here is the source code: {git_url}"


def build_command_parameters(
    message: discord.Message,
    user_command: str,
    command_message: list[str],
) -> CommandParameters:
    """Build command context from an incoming Discord message."""
    parameters = CommandParameters(
        command=user_command,
        message=command_message,
        created_at=message.created_at,
        author_id=message.author.id,
        author_name=message.author.name,
        guild_id=message.guild.id if message.guild else None,
        channel_id=message.channel.id,
    )
    if message.attachments:
        parameters.attachment = message.attachments[0].url

    is_owner_lookup = parameters.command == "tag" and bool(parameters.message) and parameters.message[0] == "owner"
    if is_owner_lookup:
        parameters.fetch_user_func = client.fetch_user

    return parameters


valid_commands: Final[dict[str, Callable[[CommandParameters], Awaitable[str]]]] = {
    "ping": ping,
    "tag": parse_tag_commands,
    "git": git,
    "mock": mock,
    "remind": parse_remind_commands,
    "help": post_help,
}


def get_data_dir() -> Path:
    """Return the configured directory for mutable bot state."""
    configured_data_dir = os.environ.get(DATA_DIR_ENV_VAR)
    if configured_data_dir:
        return Path(configured_data_dir).expanduser()
    return DEFAULT_DATA_DIR


def get_state_paths() -> tuple[Path, Path]:
    """Return tag and reminder persistence directories."""
    data_dir = get_data_dir()
    return data_dir / "tags", data_dir / "reminders"


async def send_command_response_or_log(message: discord.Message, content: str, user_command: str) -> None:
    """Send a command response and log enough Discord context when sending fails."""
    try:
        await send_command_response(message.channel, content)
    except Exception:
        _logger.exception(
            "Failed to send response for command %s in guild %s channel %s author %s",
            user_command,
            message.guild.id if message.guild else None,
            message.channel.id,
            message.author.id,
        )


async def dispatch_command(message: discord.Message, user_command: str, command_message: list[str]) -> str:
    """Run a command handler and return a generic response when command handling fails."""
    try:
        parameters = build_command_parameters(message, user_command, command_message)
        return await valid_commands[user_command](parameters)
    except Exception:
        _logger.exception(
            "Failed to handle command %s in guild %s channel %s author %s",
            user_command,
            message.guild.id if message.guild else None,
            message.channel.id,
            message.author.id,
        )
        return GENERIC_COMMAND_ERROR


async def initialize_guild_handlers(
    guild: discord.Guild,
    tag_json_path: Path,
    reminders_json_path: Path,
) -> None:
    """Initialize tag and reminder handlers for one guild without overwriting bad data files."""
    _logger.info("Initializing guild: %s - %s", guild.name, guild.id)
    if guild.id not in tags:
        try:
            tags[guild.id] = await Tags.create(guild, tag_json_path)
        except Exception:
            _logger.exception(
                "Skipping tag initialization for guild %s (%s). Existing tag file was left unchanged.",
                guild.name,
                guild.id,
            )

    if guild.id not in reminders:
        try:
            reminders[guild.id] = await Reminders.create(guild, reminders_json_path, client)
        except Exception:
            _logger.exception(
                "Skipping reminder initialization for guild %s (%s). Existing reminder file was left unchanged.",
                guild.name,
                guild.id,
            )


@client.event
async def on_ready() -> None:
    """Initialize per-guild tag and reminder handlers after Discord login."""
    _logger.info("We have logged in as %s", client.user)
    tag_json_path, reminders_json_path = get_state_paths()
    for guild in client.guilds:
        await initialize_guild_handlers(guild, tag_json_path, reminders_json_path)
    _logger.info("Initializing Done")


@client.event
async def on_guild_join(guild: discord.Guild) -> None:
    """Initialize tag and reminder handlers when the bot joins a new guild."""
    tag_json_path, reminders_json_path = get_state_paths()
    await initialize_guild_handlers(guild, tag_json_path, reminders_json_path)


@client.event
async def on_message(message: discord.Message) -> None:
    """Parse incoming Discord messages and dispatch supported bot commands."""
    if message.author == client.user:
        return

    if message.content.startswith(DELIM):
        if message.content.strip() == DELIM:
            await send_command_response_or_log(message, await post_help(), "help")
            return
        command_parts = message.content[1:].split()
        _logger.info("command: %s", command_parts)
        user_command = command_parts[0]
        if user_command in valid_commands:
            result = await dispatch_command(message, user_command, command_parts[1:])
            await send_command_response_or_log(message, result, user_command)
        else:
            invalid_command_message = (
                f"{user_command} is not a valid command. Here are the commands {await post_help()}"
            )
            await send_command_response_or_log(message, invalid_command_message, user_command)


def configure_logging() -> None:
    """Configure application logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
    )


def main() -> None:
    """Parse CLI arguments and start the Discord client."""
    configure_logging()
    config_parser = configparser.ConfigParser()

    parser = argparse.ArgumentParser(description="groupme bot")
    parser.add_argument("-c", "--config", help="ini file containing keys and other bot info", type=str, required=True)
    args = parser.parse_args()

    config_parser.read(Path(args.config).resolve())
    client.run(config_parser["keys"]["discord"])


if __name__ == "__main__":
    main()
