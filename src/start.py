#!/usr/bin/env python3
"""Discord bot entrypoint and command dispatch."""

from __future__ import annotations

import argparse
import configparser
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from constants import BOT_PATH, DELIM, MIN_REMIND_ARGUMENTS
from models import CommandParameters
from reminders import Reminders
from tags import Tags

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_logger = logging.getLogger(__name__)
client = discord.Client(intents=discord.Intents.all())


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
    if fetch_user_func is None:
        return "unable to create reminder"
    created_at = None  # can't convert the time given from discord to EST so I will just use datetime.now()
    user = parameters.author_id
    if len(command_message) < MIN_REMIND_ARGUMENTS:
        time = "help"
    return await r.create_reminder(time, message, user, created_at, fetch_user_func, guild_id, channel_id)


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


valid_commands: dict[str, Callable[[CommandParameters], Awaitable[str]]] = {
    "ping": ping,
    "tag": parse_tag_commands,
    "git": git,
    "mock": mock,
    "remind": parse_remind_commands,
    "help": post_help,
}


@client.event
async def on_ready() -> None:
    """Initialize per-guild tag and reminder handlers after Discord login."""
    _logger.info("We have logged in as %s", client.user)
    _logger.info("server - server id - channel - channel id\n=========================================")
    for channel in client.get_all_channels():
        if str(channel.category) == "Text Channels":
            _logger.info("Text Channel: %s - %s - %s - %s", channel.guild, channel.guild.id, channel, channel.id)
            tag_json_path = BOT_PATH / "tags" / "files"
            reminders_json_path = BOT_PATH / "reminders" / "files"
            if channel.guild.id not in tags:
                tags[channel.guild.id] = await Tags.create(channel.guild, tag_json_path)
            if channel.guild.id not in reminders:
                reminders[channel.guild.id] = Reminders(channel.guild, reminders_json_path, client)
    _logger.info("Initializing Done")


@client.event
async def on_message(message: discord.Message) -> None:
    """Parse incoming Discord messages and dispatch supported bot commands."""
    if message.author == client.user:
        return

    if message.content.startswith(DELIM):
        command_parts = message.content[1:].split(" ")
        _logger.info("command: %s", command_parts)
        user_command = command_parts[0]
        if user_command in valid_commands:
            parameters = CommandParameters(
                command=user_command,
                message=command_parts[1:],
                created_at=message.created_at,
                author_id=message.author.id,
                guild_id=message.channel.guild.id if message.channel.guild else None,
                channel_id=message.channel.id,
            )
            if message.attachments:
                parameters.attachment = message.attachments[0].url
            is_owner_lookup = (
                parameters.command == "tag" and bool(parameters.message) and parameters.message[0] == "owner"
            )
            if parameters.command == "remind" or is_owner_lookup:
                parameters.fetch_user_func = client.fetch_user

            result = await valid_commands[user_command](parameters)
            await message.channel.send(result)
        else:
            invalid_command_message = (
                f"{user_command} is not a valid command. Here are the commands {await post_help()}"
            )
            await message.channel.send(invalid_command_message)


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
