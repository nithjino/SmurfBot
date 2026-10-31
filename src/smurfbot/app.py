"""The composition root: wires the bot's services together, independent of Discord."""

import logging
from typing import Final
lazy from collections.abc import Iterable
lazy from pathlib import Path

from smurfbot.activation import GROUP_FILE, GroupActivation
from smurfbot.commands import BASIC_COMMANDS
from smurfbot.registry import GuildRegistry
from smurfbot.reminders import Clock, RemindCommand, ReminderBook, ReminderSender
from smurfbot.router import CommandRouter
from smurfbot.storage import JsonStore
from smurfbot.tags import TagBook, TagCommand
lazy from smurfbot.core import GroupRef
lazy from smurfbot.tags.commands import FetchUserName

GROUPS_FILENAME: Final = "groups.json"

_logger = logging.getLogger(__name__)


class Application:
    """Own the bot's state for one process, driven by events from a transport."""

    def __init__(
        self,
        *,
        data_dir: Path,
        prefix: str,
        send_reminder: ReminderSender,
        fetch_user_name: FetchUserName,
        clock: Clock | None = None,
    ) -> None:
        """Wire the services; nothing touches the disk until `start`."""
        tag_dir = data_dir / "tags"
        reminder_dir = data_dir / "reminders"

        async def open_tags(guild: GroupRef) -> TagBook:
            return await TagBook.open(tag_dir, guild)

        async def open_reminders(guild: GroupRef) -> ReminderBook:
            return await ReminderBook.open(reminder_dir, guild, send_reminder, clock)

        self._activation = GroupActivation(JsonStore(data_dir / GROUPS_FILENAME, GROUP_FILE))
        self._tags = GuildRegistry(open_tags)
        self._reminders = GuildRegistry(open_reminders, closer=ReminderBook.close)
        self.router = CommandRouter(
            prefix,
            self._activation,
            {
                **BASIC_COMMANDS,
                "tag": TagCommand(self._tags, fetch_user_name),
                "remind": RemindCommand(self._reminders),
            },
        )

    async def start(self) -> None:
        """Load activation state; a corrupt file aborts startup rather than reactivating groups."""
        await self._activation.load()

    async def sync(self, guilds: Iterable[GroupRef], private_channels: Iterable[GroupRef]) -> None:
        """Reconcile with what Discord reports after connecting; safe to repeat."""
        guilds = tuple(guilds)
        await self._activation.observe((*guilds, *private_channels))
        for guild in guilds:
            await self._open_guild(guild)

    async def join(self, guild: GroupRef) -> None:
        """Start serving a guild that joined or became available."""
        await self._activation.observe([guild])
        await self._open_guild(guild)

    async def leave(self, guild_id: int) -> None:
        """Stop serving a guild, keeping its files for a possible return."""
        await self._tags.discard(guild_id)
        await self._reminders.discard(guild_id)

    async def stop(self) -> None:
        """Stop every timer; pending reminders resume on the next start."""
        await self._tags.close()
        await self._reminders.close()

    async def _open_guild(self, guild: GroupRef) -> None:
        """Open each service independently, so a corrupt file only disables that service."""
        for service, registry in (("tags", self._tags), ("reminders", self._reminders)):
            try:
                await registry.get(guild)
            except Exception:
                _logger.exception("Skipping %s for guild %s; its file was left unchanged", service, guild.id)
