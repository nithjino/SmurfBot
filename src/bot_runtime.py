"""Instance-owned Discord bot lifecycle and command dispatch."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Final, Protocol
from weakref import WeakValueDictionary

import discord

from bot_state import (
    GROUPS_FILENAME,
    is_message_group_enabled,
    load_group_records,
    save_group_records,
    set_message_group_enabled,
    sync_group_records,
    upsert_group_record,
)
from discord_messages import send_command_response
from models import CommandParameters
from reminders import Reminders
from tags import Tags

CommandHandler = Callable[[CommandParameters], Awaitable[str]]
TagFactory = Callable[[discord.Guild, Path], Awaitable[Tags]]
ReminderFactory = Callable[[discord.Guild, Path], Awaitable[Reminders]]

GENERIC_COMMAND_ERROR: Final[str] = "Sorry, something went wrong while handling that command."
ACTIVATE_COMMAND: Final[str] = "activate"
DEACTIVE_COMMAND: Final[str] = "deactive"
DEACTIVATE_COMMAND: Final[str] = "deactivate"

_logger = logging.getLogger(__name__)
_runtime_owners_lock = Lock()
_runtime_owners: WeakValueDictionary[Path, BotRuntime] = WeakValueDictionary()


class DiscordClient(Protocol):
    """Discord client state and lookup operations used by the runtime."""

    @property
    def user(self) -> discord.ClientUser | None:
        """Return the current Discord user after login."""
        ...

    @property
    def guilds(self) -> Sequence[discord.Guild]:
        """Return guilds visible to the client."""
        ...

    @property
    def private_channels(self) -> Sequence[discord.abc.PrivateChannel]:
        """Return cached private channels visible to the client."""
        ...

    async def fetch_user(self, user_id: int, /) -> discord.User:
        """Fetch a Discord user by ID."""
        ...


@dataclass(frozen=True, slots=True)
class _RuntimeDependencies:
    """Private dependency replacements used by runtime tests."""

    tag_factory: TagFactory = Tags.create
    reminder_factory: ReminderFactory = Reminders.create
    command_substitutions: Mapping[str, CommandHandler] = field(default_factory=dict)


class BotRuntime:
    """Own one bot client's mutable guild state and event workflows."""

    def __init__(
        self,
        client: DiscordClient,
        *,
        command_delimiter: str,
        data_dir: Path,
        _dependencies: _RuntimeDependencies | None = None,
    ) -> None:
        """Create a runtime and claim exclusive process-local ownership of its data directory."""
        if not command_delimiter:
            msg = "command_delimiter must not be empty"
            raise ValueError(msg)

        canonical_data_dir = data_dir.resolve(strict=False)
        dependencies = _dependencies or _RuntimeDependencies()

        self._client = client
        self._command_delimiter = command_delimiter
        self._groups_path = canonical_data_dir / GROUPS_FILENAME
        self._tag_path = canonical_data_dir / "tags"
        self._reminder_path = canonical_data_dir / "reminders"
        self._tag_factory = dependencies.tag_factory
        self._reminder_factory = dependencies.reminder_factory
        self._tags: dict[int, Tags] = {}
        self._reminders: dict[int, Reminders] = {}
        self._lifecycle_lock = asyncio.Lock()
        substitutions = dict(dependencies.command_substitutions)
        self._commands: dict[str, CommandHandler] = {
            "ping": self._ping,
            "tag": self._parse_tag_commands,
            "git": self._git,
            "mock": self._mock,
            "remind": self._parse_remind_commands,
            "help": self._post_help,
            **substitutions,
        }

        with _runtime_owners_lock:
            owner = _runtime_owners.get(canonical_data_dir)
            if owner is not None:
                msg = f"A BotRuntime already owns data directory {canonical_data_dir}"
                raise RuntimeError(msg)
            _runtime_owners[canonical_data_dir] = self

    async def on_ready(self) -> None:
        """Synchronize activation state, then initialize every visible guild."""
        _logger.info("We have logged in as %s", self._client.user)
        await sync_group_records(self._client.guilds, self._client.private_channels, self._groups_path)
        for guild in self._client.guilds:
            await self._initialize_guild(guild)
        _logger.info("Initializing Done")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Preserve activation state across guild renames, then initialize the guild."""
        records = await load_group_records(self._groups_path)
        if upsert_group_record(records, guild.name, guild.id):
            await save_group_records(records, self._groups_path)
        await self._initialize_guild(guild)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Discard a guild's handlers and close its pending reminder tasks."""
        async with self._lifecycle_lock:
            self._tags.pop(guild.id, None)
            reminder_handler = self._reminders.pop(guild.id, None)
            if reminder_handler is not None:
                await reminder_handler.close()

    async def on_message(self, message: discord.Message) -> None:
        """Parse one Discord message and dispatch a supported bot command."""
        if message.author == self._client.user or getattr(message.author, "bot", False):
            return
        if not message.content.startswith(self._command_delimiter):
            return

        command_parts = message.content[len(self._command_delimiter) :].split()
        await self._handle_command_message(message, command_parts)

    async def _initialize_guild(self, guild: discord.Guild) -> None:
        """Initialize missing guild registries independently under the lifecycle lock."""
        _logger.info("Initializing guild: %s - %s", guild.name, guild.id)
        async with self._lifecycle_lock:
            if guild.id not in self._tags:
                try:
                    self._tags[guild.id] = await self._tag_factory(guild, self._tag_path)
                except Exception:
                    _logger.exception(
                        "Skipping tag initialization for guild %s (%s). Existing tag file was left unchanged.",
                        guild.name,
                        guild.id,
                    )

            if guild.id not in self._reminders:
                try:
                    self._reminders[guild.id] = await self._reminder_factory(guild, self._reminder_path)
                except Exception:
                    _logger.exception(
                        "Skipping reminder initialization for guild %s (%s). "
                        "Existing reminder file was left unchanged.",
                        guild.name,
                        guild.id,
                    )

    async def _handle_command_message(self, message: discord.Message, command_parts: list[str]) -> None:
        """Apply activation rules and dispatch parsed command parts."""
        if not command_parts:
            if not await is_message_group_enabled(message, self._groups_path):
                return
            await self._send_command_response_or_log(message, await self._post_help(), "help")
            return

        _logger.info("command: %s", command_parts)
        user_command = command_parts[0].lower()
        if user_command == ACTIVATE_COMMAND:
            result = await set_message_group_enabled(message, enabled=True, groups_path=self._groups_path)
            await self._send_command_response_or_log(message, result, user_command)
        elif not await is_message_group_enabled(message, self._groups_path):
            return
        elif user_command in {DEACTIVE_COMMAND, DEACTIVATE_COMMAND}:
            result = await set_message_group_enabled(message, enabled=False, groups_path=self._groups_path)
            await self._send_command_response_or_log(message, result, user_command)
        elif user_command in self._commands:
            result = await self._dispatch_command(message, user_command, command_parts[1:])
            await self._send_command_response_or_log(message, result, user_command)
        else:
            _logger.info("Ignoring unknown command: %s", user_command)

    async def _dispatch_command(
        self,
        message: discord.Message,
        user_command: str,
        command_message: list[str],
    ) -> str:
        """Run a command and translate construction or handler failures."""
        try:
            parameters = self._build_command_parameters(message, user_command, command_message)
            return await self._commands[user_command](parameters)
        except Exception:
            _logger.exception(
                "Failed to handle command %s in guild %s channel %s author %s",
                user_command,
                message.guild.id if message.guild else None,
                message.channel.id,
                message.author.id,
            )
            return GENERIC_COMMAND_ERROR

    def _build_command_parameters(
        self,
        message: discord.Message,
        user_command: str,
        command_message: list[str],
    ) -> CommandParameters:
        """Build command context from an incoming Discord message."""
        is_owner_lookup = user_command == "tag" and bool(command_message) and command_message[0] == "owner"
        return CommandParameters(
            command=user_command,
            message=command_message,
            attachment=message.attachments[0].url if message.attachments else None,
            fetch_user_func=self._client.fetch_user if is_owner_lookup else None,
            created_at=message.created_at,
            author_id=message.author.id,
            author_name=message.author.name,
            guild_id=message.guild.id if message.guild else None,
            channel_id=message.channel.id,
        )

    async def _send_command_response_or_log(
        self,
        message: discord.Message,
        content: str,
        user_command: str,
    ) -> None:
        """Send a command response, logging and swallowing send failures."""
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

    async def _post_help(self, _parameters: CommandParameters | None = None) -> str:
        """Return the bot's top-level command help."""
        return (
            "The commands are: tag, git, remind, activate, and deactive. Tag and remind have their own help commands."
        )

    async def _ping(self, parameters: CommandParameters) -> str:
        """Respond to the ping command."""
        _logger.info("ping parameters: %s", parameters)
        return "pong"

    async def _parse_tag_commands(self, parameters: CommandParameters) -> str:
        """Route tag parameters to the guild's tag registry entry."""
        _logger.info("parse_tag_commands parameters: %s", parameters)
        guild_id = parameters.guild_id
        if guild_id is None:
            return "unable to get tag. guild_id is None"
        tag_handler = self._tags.get(guild_id)
        if tag_handler is None:
            return "unable to get tag. tag function parameter is None"
        return await tag_handler.parse_commands(parameters)

    async def _parse_remind_commands(self, parameters: CommandParameters) -> str:
        """Route reminder parameters to the guild's reminder registry entry."""
        _logger.info("parse_remind_commands parameters: %s", parameters)
        channel_id = parameters.channel_id
        guild_id = parameters.guild_id
        if guild_id is None or channel_id is None:
            return f"guild_id ({guild_id}) or channel_id {channel_id} is None."
        reminder_handler = self._reminders.get(guild_id)
        if reminder_handler is None:
            return ""
        return await reminder_handler.handle(parameters)

    async def _mock(self, parameters: CommandParameters) -> str:
        """Return the command message in alternating case."""
        _logger.info("mock parameters: %s", parameters)
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

    async def _git(self, _parameters: CommandParameters | None = None) -> str:
        """Return the source repository URL."""
        return "Here is the source code: https://github.com/nithjino/SmurfBot"
