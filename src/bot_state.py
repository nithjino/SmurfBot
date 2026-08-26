"""Owned group activation transactions and data-directory configuration."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol

from atomicwrites import atomic_write

if TYPE_CHECKING:
    from collections.abc import Sequence

BOT_PATH: Final[Path] = Path(__file__).resolve().parent
DEFAULT_DATA_DIR: Final[Path] = BOT_PATH.parent / "data"
DATA_DIR_ENV_VAR: Final[str] = "SMURFBOT_DATA_DIR"
GROUPS_FILENAME: Final[str] = "groups.json"


class GroupIdentity(Protocol):
    """Minimal Discord guild/channel identity stored in group records."""

    @property
    def id(self) -> int:
        """Discord snowflake ID."""
        ...

    @property
    def name(self) -> str:
        """Discord display name."""
        ...


class MessageChannel(Protocol):
    """Minimal Discord message channel identity stored in group records."""

    @property
    def id(self) -> int:
        """Discord snowflake ID."""
        ...


class MessageContext(Protocol):
    """Minimal incoming message shape needed for activation state."""

    @property
    def author(self) -> object:
        """Discord message author."""
        ...

    @property
    def channel(self) -> MessageChannel:
        """Discord message channel."""
        ...

    @property
    def guild(self) -> GroupIdentity | None:
        """Discord guild, when the message was sent in a guild."""
        ...


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


def _load_group_records_sync(groups_path: Path) -> dict[str, dict[str, Any]]:
    """Return persisted group activation records."""
    if not groups_path.exists():
        return {}
    with groups_path.open(encoding="utf-8") as groups_file:
        records = json.load(groups_file)
    if not isinstance(records, dict):
        msg = f"{groups_path} must contain a JSON object"
        raise TypeError(msg)
    return records


def _save_group_records_sync(groups_path: Path, records: dict[str, dict[str, Any]]) -> None:
    """Persist group activation records."""
    groups_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(groups_path, overwrite=True, encoding="utf-8") as groups_file:
        groups_file.write(json.dumps(records, sort_keys=True, indent=2))


def _unique_group_name(records: dict[str, dict[str, Any]], name: str, group_id: str) -> str:
    """Return a stable name key for a group record."""
    existing_record = records.get(name)
    if existing_record is None or str(existing_record.get("id")) == group_id:
        return name
    return f"{name} ({group_id})"


def _upsert_group_record(records: dict[str, dict[str, Any]], name: str, group_id: int | str) -> bool:
    """Add or rename a group record while preserving its enabled value."""
    group_id = str(group_id)
    existing_key = next((key for key, record in records.items() if str(record.get("id")) == group_id), None)
    if existing_key is not None:
        record = records[existing_key]
        enabled = bool(record.get("enabled", True))
        name = _unique_group_name(records, name, group_id)
        if existing_key != name:
            del records[existing_key]
            records[name] = {"enabled": enabled, "id": group_id}
            return True
        normalized_record = {"enabled": enabled, "id": group_id}
        if record != normalized_record:
            records[name] = normalized_record
            return True
        return False

    records[_unique_group_name(records, name, group_id)] = {"enabled": True, "id": group_id}
    return True


def _get_private_channel_name(channel: object) -> str:
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


def _get_message_group_name_and_id(message: MessageContext) -> tuple[str, int]:
    """Return the group state identity for an incoming Discord message."""
    if message.guild is not None:
        return message.guild.name, message.guild.id
    return _get_private_channel_name(message.channel), message.channel.id


def _can_manage_group_state(message: MessageContext) -> bool:
    """Return whether the command author can change the current group state."""
    if message.guild is None:
        return True
    permissions = getattr(message.author, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False) or getattr(permissions, "manage_guild", False))


class GroupActivation:
    """Own identity, permissions, and serialized activation file transactions."""

    def __init__(self, groups_path: Path) -> None:
        """Keep one explicit persistence path and a lock for its transactions."""
        self._groups_path = groups_path
        self._lock = asyncio.Lock()

    async def synchronize(
        self,
        guilds: Sequence[GroupIdentity],
        private_channels: Sequence[MessageChannel],
    ) -> None:
        """Synchronize visible Groups without losing activation or invisible records."""
        async with self._lock:
            records = _load_group_records_sync(self._groups_path)
            changed = False
            for guild in guilds:
                changed = _upsert_group_record(records, guild.name, guild.id) or changed
            for channel in private_channels:
                changed = _upsert_group_record(records, _get_private_channel_name(channel), channel.id) or changed
            if changed or not self._groups_path.exists():
                _save_group_records_sync(self._groups_path, records)

    async def record_join(self, guild: GroupIdentity) -> None:
        """Record a join or rename while preserving the Group's activation choice."""
        async with self._lock:
            records = _load_group_records_sync(self._groups_path)
            if _upsert_group_record(records, guild.name, guild.id):
                _save_group_records_sync(self._groups_path, records)

    async def is_enabled(self, message: MessageContext) -> bool:
        """Return activation, persisting a missing Group with its current default."""
        async with self._lock:
            name, group_id = _get_message_group_name_and_id(message)
            records = _load_group_records_sync(self._groups_path)
            changed = _upsert_group_record(records, name, group_id)
            enabled = next(
                (
                    bool(record.get("enabled", True))
                    for record in records.values()
                    if str(record.get("id")) == str(group_id)
                ),
                True,
            )
            if changed:
                _save_group_records_sync(self._groups_path, records)
            return enabled

    async def set_enabled(self, message: MessageContext, *, enabled: bool) -> str:
        """Authorize, persist, and describe an activation change as one transaction."""
        async with self._lock:
            if not _can_manage_group_state(message):
                return "Only server admins can activate or deactive this bot."
            name, group_id = _get_message_group_name_and_id(message)
            records = _load_group_records_sync(self._groups_path)
            _upsert_group_record(records, name, group_id)
            for record in records.values():
                if str(record.get("id")) == str(group_id):
                    record["enabled"] = enabled
                    break
            _save_group_records_sync(self._groups_path, records)
            state = "activated" if enabled else "deactived"
            return f"{name} has been {state}."
