"""Discord bot entrypoint and event dispatch."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

import discord

from bot_state import (
    BOT_PATH,
    DATA_DIR_ENV_VAR,
    DEFAULT_DATA_DIR,
    GROUPS_FILENAME,
    can_manage_group_state,
    get_data_dir,
    get_groups_path,
    get_message_group_name_and_id,
    get_private_channel_name,
    get_state_paths,
    is_message_group_enabled,
    load_group_records,
    load_group_records_sync,
    save_group_records,
    save_group_records_sync,
    set_message_group_enabled,
    sync_group_records,
    unique_group_name,
    upsert_group_record,
)
from command_handlers import (
    GENERIC_COMMAND_ERROR,
    git,
    mock,
    parse_remind_commands,
    parse_tag_commands,
    ping,
    post_help,
    reminders,
    tags,
    valid_commands,
)
from command_handlers import build_command_parameters as _build_command_parameters
from discord_messages import send_command_response
from guild_handlers import (
    initialize_guild_handlers as _initialize_guild_handlers,
)
from guild_handlers import (
    remove_guild_handlers as _remove_guild_handlers,
)
from reminders import Reminders
from settings import Settings, load_settings
from tags import Tags

if TYPE_CHECKING:
    from pathlib import Path

    from models import CommandParameters

_logger = logging.getLogger(__name__)
DEFAULT_DELIM: Final[str] = "$"
ACTIVATE_COMMAND: Final[str] = "activate"
DEACTIVE_COMMAND: Final[str] = "deactive"
DEACTIVATE_COMMAND: Final[str] = "deactivate"
runtime_settings: Settings | None = None
__all__ = [
    "ACTIVATE_COMMAND",
    "BOT_PATH",
    "DATA_DIR_ENV_VAR",
    "DEACTIVATE_COMMAND",
    "DEACTIVE_COMMAND",
    "DEFAULT_DATA_DIR",
    "DEFAULT_DELIM",
    "GENERIC_COMMAND_ERROR",
    "GROUPS_FILENAME",
    "build_command_parameters",
    "build_discord_intents",
    "can_manage_group_state",
    "client",
    "configure_logging",
    "dispatch_command",
    "get_command_delimiter",
    "get_data_dir",
    "get_groups_path",
    "get_message_group_name_and_id",
    "get_private_channel_name",
    "get_state_paths",
    "git",
    "handle_command_message",
    "initialize_guild_handlers",
    "is_message_group_enabled",
    "load_group_records",
    "load_group_records_sync",
    "main",
    "mock",
    "on_guild_join",
    "on_guild_remove",
    "on_message",
    "on_ready",
    "parse_remind_commands",
    "parse_tag_commands",
    "ping",
    "post_help",
    "reminders",
    "runtime_settings",
    "save_group_records",
    "save_group_records_sync",
    "send_command_response_or_log",
    "set_message_group_enabled",
    "set_runtime_settings",
    "sync_group_records",
    "tags",
    "unique_group_name",
    "upsert_group_record",
    "valid_commands",
]


def build_discord_intents() -> discord.Intents:
    """Return the minimal Discord intents needed for command message handling."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.dm_messages = True
    intents.message_content = True
    return intents


client = discord.Client(intents=build_discord_intents())


def build_command_parameters(
    message: discord.Message,
    user_command: str,
    command_message: list[str],
) -> CommandParameters:
    """Build command context from an incoming Discord message."""
    return _build_command_parameters(message, user_command, command_message, client.fetch_user)


def set_runtime_settings(settings: Settings) -> None:
    """Store settings loaded during startup for event handlers."""
    global runtime_settings  # noqa: PLW0603
    runtime_settings = settings


def get_command_delimiter() -> str:
    """Return the configured command delimiter."""
    if runtime_settings is None:
        return DEFAULT_DELIM
    return runtime_settings.delim


async def initialize_guild_handlers(
    guild: discord.Guild,
    tag_json_path: Path,
    reminders_json_path: Path,
) -> None:
    """Initialize tag and reminder handlers for one guild without overwriting bad data files."""
    await _initialize_guild_handlers(
        guild,
        tag_json_path,
        reminders_json_path,
        tags,
        reminders,
        Tags,
        Reminders,
    )


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


@client.event
async def on_ready() -> None:
    """Initialize per-guild tag and reminder handlers after Discord login."""
    _logger.info("We have logged in as %s", client.user)
    await sync_group_records(client.guilds, client.private_channels)
    tag_json_path, reminders_json_path = get_state_paths()
    for guild in client.guilds:
        await initialize_guild_handlers(guild, tag_json_path, reminders_json_path)
    _logger.info("Initializing Done")


@client.event
async def on_guild_join(guild: discord.Guild) -> None:
    """Initialize tag and reminder handlers when the bot joins a new guild."""
    groups_path = get_groups_path()
    records = await load_group_records(groups_path)
    if upsert_group_record(records, guild.name, guild.id):
        await save_group_records(records, groups_path)
    tag_json_path, reminders_json_path = get_state_paths()
    await initialize_guild_handlers(guild, tag_json_path, reminders_json_path)


@client.event
async def on_guild_remove(guild: discord.Guild) -> None:
    """Discard guild state and cancel its pending in-process reminder tasks."""
    await _remove_guild_handlers(guild.id, tags, reminders)


@client.event
async def on_message(message: discord.Message) -> None:
    """Parse incoming Discord messages and dispatch supported bot commands."""
    if message.author == client.user or getattr(message.author, "bot", False):
        return

    command_delimiter = get_command_delimiter()
    if not message.content.startswith(command_delimiter):
        return

    command_parts = message.content[len(command_delimiter) :].split()
    await handle_command_message(message, command_parts)


async def handle_command_message(message: discord.Message, command_parts: list[str]) -> None:
    """Dispatch a parsed bot command message."""
    if not command_parts:
        if not await is_message_group_enabled(message):
            return
        await send_command_response_or_log(message, await post_help(), "help")
        return

    _logger.info("command: %s", command_parts)
    user_command = command_parts[0].lower()
    if user_command == ACTIVATE_COMMAND:
        result = await set_message_group_enabled(message, enabled=True)
        await send_command_response_or_log(message, result, user_command)
    elif not await is_message_group_enabled(message):
        return
    elif user_command in {DEACTIVE_COMMAND, DEACTIVATE_COMMAND}:
        result = await set_message_group_enabled(message, enabled=False)
        await send_command_response_or_log(message, result, user_command)
    elif user_command in valid_commands:
        result = await dispatch_command(message, user_command, command_parts[1:])
        await send_command_response_or_log(message, result, user_command)
    else:
        _logger.info("Ignoring unknown command: %s", user_command)


def configure_logging() -> None:
    """Configure application logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
    )


def main() -> None:
    """Parse CLI arguments and start the Discord client."""
    settings = load_settings()
    set_runtime_settings(settings)
    configure_logging()

    client.run(settings.discord_token.get_secret_value())


if __name__ == "__main__":
    main()
