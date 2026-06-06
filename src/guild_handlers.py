"""Guild-scoped tag and reminder handler initialization."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import discord

from reminders import Reminders
from tags import Tags

_logger = logging.getLogger(__name__)


async def initialize_guild_handlers(
    guild: discord.Guild,
    tag_json_path: Path,
    reminders_json_path: Path,
    discord_client: discord.Client,
    tag_handlers: dict[int, Tags],
    reminder_handlers: dict[int, Reminders],
    tags_class: type[Tags] = Tags,
    reminders_class: type[Reminders] = Reminders,
) -> None:
    """Initialize tag and reminder handlers for one guild without overwriting bad data files."""
    _logger.info("Initializing guild: %s - %s", guild.name, guild.id)
    if guild.id not in tag_handlers:
        try:
            tag_handlers[guild.id] = await tags_class.create(guild, tag_json_path)
        except Exception:
            _logger.exception(
                "Skipping tag initialization for guild %s (%s). Existing tag file was left unchanged.",
                guild.name,
                guild.id,
            )

    if guild.id not in reminder_handlers:
        try:
            reminder_handlers[guild.id] = await reminders_class.create(guild, reminders_json_path, discord_client)
        except Exception:
            _logger.exception(
                "Skipping reminder initialization for guild %s (%s). Existing reminder file was left unchanged.",
                guild.name,
                guild.id,
            )
