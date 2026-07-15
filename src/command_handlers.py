"""Built-in bot command handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from models import CommandParameters
from reminders.constants import MIN_REMIND_ARGUMENTS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import discord

    from models import FetchUser
    from reminders import Reminders
    from tags import Tags

_logger = logging.getLogger(__name__)
GENERIC_COMMAND_ERROR: Final[str] = "Sorry, something went wrong while handling that command."

tags: dict[int, Tags] = {}
reminders: dict[int, Reminders] = {}


async def post_help(_parameters: CommandParameters | None = None) -> str:
    """:return: a string containing what commands the bot has"""
    return "The commands are: tag, git, remind, activate, and deactive. Tag and remind have their own help commands."


async def ping(parameters: CommandParameters) -> str:
    """Respond to the ping command."""
    _logger.info("ping parameters: %s", parameters)
    return "pong"


async def parse_tag_commands(parameters: CommandParameters) -> str:
    """Route tag command parameters to the guild's tag handler."""
    _logger.info("parse_tag_commands parameters: %s", parameters)
    guild_id = parameters.guild_id
    if guild_id is None:
        return "unable to get tag. guild_id is None"
    tag = tags.get(guild_id)
    if tag is None:
        return "unable to get tag. tag function parameter is None"
    return await tag.parse_commands(parameters)


async def parse_remind_commands(parameters: CommandParameters) -> str:
    """Route reminder command parameters to the guild's reminder handler."""
    _logger.info("parse_remind_commands parameters: %s", parameters)
    channel_id = parameters.channel_id
    guild_id = parameters.guild_id
    if guild_id is None or channel_id is None:
        return f"guild_id ({guild_id}) or channel_id {channel_id} is None."
    reminder_handler = reminders.get(guild_id)
    if reminder_handler is None:
        return ""
    command_message = parameters.message
    time = command_message[0] if command_message else "help"
    message = command_message[1:]
    user = parameters.author_id
    if len(command_message) < MIN_REMIND_ARGUMENTS:
        time = "help"
    return await reminder_handler.create_reminder(
        time,
        message,
        user,
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

    result: list[str] = []
    uppercase = True
    for character in message:
        if character.isspace():
            result.append(character)
            continue

        result.append(character.upper() if uppercase else character)
        uppercase = not uppercase

    return "".join(result)


async def git(_parameters: CommandParameters | None = None) -> str:
    """:return: a url of the git repo of the source code"""
    git_url = "https://github.com/nithjino/SmurfBot"
    return f"Here is the source code: {git_url}"


def build_command_parameters(
    message: discord.Message,
    user_command: str,
    command_message: list[str],
    fetch_user_func: FetchUser | None = None,
) -> CommandParameters:
    """Build command context from an incoming Discord message."""
    is_owner_lookup = user_command == "tag" and bool(command_message) and command_message[0] == "owner"
    return CommandParameters(
        command=user_command,
        message=command_message,
        attachment=message.attachments[0].url if message.attachments else None,
        fetch_user_func=fetch_user_func if is_owner_lookup else None,
        created_at=message.created_at,
        author_id=message.author.id,
        author_name=message.author.name,
        guild_id=message.guild.id if message.guild else None,
        channel_id=message.channel.id,
    )


valid_commands: Final[dict[str, Callable[[CommandParameters], Awaitable[str]]]] = {
    "ping": ping,
    "tag": parse_tag_commands,
    "git": git,
    "mock": mock,
    "remind": parse_remind_commands,
    "help": post_help,
}
