#!/usr/bin/env python3
"""Discord bot entrypoint and command dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import discord
from atomicwrites import atomic_write

from discord_messages import send_command_response
from models import CommandParameters
from reminders import Reminders
from reminders.constants import MIN_REMIND_ARGUMENTS
from settings import Settings, load_settings
from tags import Tags

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

_logger = logging.getLogger(__name__)
BOT_PATH: Final[Path] = Path(__file__).resolve().parent
DEFAULT_DATA_DIR: Final[Path] = BOT_PATH.parent / "data"
DATA_DIR_ENV_VAR: Final[str] = "SMURFBOT_DATA_DIR"
GROUPS_FILENAME: Final[str] = "groups.json"
DEFAULT_DELIM: Final[str] = "$"
GENERIC_COMMAND_ERROR: Final[str] = "Sorry, something went wrong while handling that command."
ACTIVATE_COMMAND: Final[str] = "activate"
DEACTIVE_COMMAND: Final[str] = "deactive"
DEACTIVATE_COMMAND: Final[str] = "deactivate"
runtime_settings: Settings | None = None


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
    return "The commands are: tag, git, remind, activate, and deactive. Tag and remind have their own help commands."


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


def set_runtime_settings(settings: Settings) -> None:
    """Store settings loaded during startup for event handlers."""
    global runtime_settings  # noqa: PLW0603
    runtime_settings = settings


def get_command_delimiter() -> str:
    """Return the configured command delimiter."""
    if runtime_settings is None:
        return DEFAULT_DELIM
    return runtime_settings.delim


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


def get_groups_path() -> Path:
    """Return the path to the group and DM activation state file."""
    return get_data_dir() / GROUPS_FILENAME


def load_group_records_sync(groups_path: Path) -> dict[str, dict[str, Any]]:
    """Return persisted group activation records."""
    if not groups_path.exists():
        return {}
    with groups_path.open(encoding="utf-8") as groups_file:
        records = json.load(groups_file)
    if not isinstance(records, dict):
        msg = f"{groups_path} must contain a JSON object"
        raise TypeError(msg)
    return records


async def load_group_records(groups_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load group activation records without blocking the event loop."""
    return await asyncio.to_thread(load_group_records_sync, groups_path or get_groups_path())


def save_group_records_sync(groups_path: Path, records: dict[str, dict[str, Any]]) -> None:
    """Persist group activation records."""
    groups_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(groups_path, overwrite=True, encoding="utf-8") as groups_file:
        groups_file.write(json.dumps(records, sort_keys=True, indent=2))


async def save_group_records(records: dict[str, dict[str, Any]], groups_path: Path | None = None) -> None:
    """Save group activation records without blocking the event loop."""
    await asyncio.to_thread(save_group_records_sync, groups_path or get_groups_path(), records)


def unique_group_name(records: dict[str, dict[str, Any]], name: str, group_id: str) -> str:
    """Return a stable name key for a group record."""
    existing_record = records.get(name)
    if existing_record is None or str(existing_record.get("id")) == group_id:
        return name
    return f"{name} ({group_id})"


def upsert_group_record(records: dict[str, dict[str, Any]], name: str, group_id: int | str) -> bool:
    """Add or rename a group record while preserving its enabled value."""
    group_id = str(group_id)
    existing_key = next((key for key, record in records.items() if str(record.get("id")) == group_id), None)
    if existing_key is not None:
        record = records[existing_key]
        enabled = bool(record.get("enabled", True))
        name = unique_group_name(records, name, group_id)
        if existing_key != name:
            del records[existing_key]
            records[name] = {"enabled": enabled, "id": group_id}
            return True
        normalized_record = {"enabled": enabled, "id": group_id}
        if record != normalized_record:
            records[name] = normalized_record
            return True
        return False

    records[unique_group_name(records, name, group_id)] = {"enabled": True, "id": group_id}
    return True


def get_private_channel_name(channel: object) -> str:
    """Return a display name for a Discord DM or group DM channel."""
    recipient = getattr(channel, "recipient", None)
    if recipient is not None and getattr(recipient, "name", None):
        return f"DM: {recipient.name}"
    name = getattr(channel, "name", None)
    if name:
        return f"DM: {name}"
    recipients = getattr(channel, "recipients", None)
    if recipients:
        recipient_names = ", ".join(getattr(recipient, "name", str(recipient)) for recipient in recipients)
        return f"DM: {recipient_names}"
    return f"DM: {getattr(channel, 'id', 'unknown')}"


def get_message_group_name_and_id(message: discord.Message) -> tuple[str, int]:
    """Return the group state identity for an incoming Discord message."""
    if message.guild is not None:
        return message.guild.name, message.guild.id
    return get_private_channel_name(message.channel), message.channel.id


async def sync_group_records(
    guilds: Sequence[discord.Guild],
    private_channels: Sequence[discord.abc.PrivateChannel],
    groups_path: Path | None = None,
) -> None:
    """Write all visible guilds and cached DMs to the group activation file."""
    groups_path = groups_path or get_groups_path()
    records = await load_group_records(groups_path)
    changed = False
    for guild in guilds:
        changed = upsert_group_record(records, guild.name, guild.id) or changed
    for channel in private_channels:
        changed = upsert_group_record(records, get_private_channel_name(channel), channel.id) or changed
    if changed or not groups_path.exists():
        await save_group_records(records, groups_path)


async def is_message_group_enabled(message: discord.Message) -> bool:
    """Return whether the message context is enabled for bot command responses."""
    name, group_id = get_message_group_name_and_id(message)
    groups_path = get_groups_path()
    records = await load_group_records(groups_path)
    changed = upsert_group_record(records, name, group_id)
    enabled = next(
        (bool(record.get("enabled", True)) for record in records.values() if str(record.get("id")) == str(group_id)),
        True,
    )
    if changed:
        await save_group_records(records, groups_path)
    return enabled


def can_manage_group_state(message: discord.Message) -> bool:
    """Return whether the command author can change the current group state."""
    if message.guild is None:
        return True
    permissions = getattr(message.author, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False) or getattr(permissions, "manage_guild", False))


async def set_message_group_enabled(message: discord.Message, *, enabled: bool) -> str:
    """Enable or disable command responses for the current Discord context."""
    if not can_manage_group_state(message):
        return "Only server admins can activate or deactive this bot."

    name, group_id = get_message_group_name_and_id(message)
    groups_path = get_groups_path()
    records = await load_group_records(groups_path)
    upsert_group_record(records, name, group_id)
    for record in records.values():
        if str(record.get("id")) == str(group_id):
            record["enabled"] = enabled
            break
    await save_group_records(records, groups_path)
    state = "activated" if enabled else "deactived"
    return f"{name} has been {state}."


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
async def on_message(message: discord.Message) -> None:
    """Parse incoming Discord messages and dispatch supported bot commands."""
    if message.author == client.user:
        return

    command_delimiter = get_command_delimiter()
    if message.content.startswith(command_delimiter):
        command_parts = message.content[len(command_delimiter) :].split()
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
            return

        if not await is_message_group_enabled(message):
            return

        if user_command in {DEACTIVE_COMMAND, DEACTIVATE_COMMAND}:
            result = await set_message_group_enabled(message, enabled=False)
            await send_command_response_or_log(message, result, user_command)
            return

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
    settings = load_settings()
    set_runtime_settings(settings)
    configure_logging()

    client.run(settings.discord_token.get_secret_value())


if __name__ == "__main__":
    main()
