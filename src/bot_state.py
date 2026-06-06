"""Persistence helpers for bot activation state."""

from __future__ import annotations

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

    id: int
    name: str


class MessageChannel(Protocol):
    """Minimal Discord message channel identity stored in group records."""

    id: int


class MessageContext(Protocol):
    """Minimal incoming message shape needed for activation state."""

    author: object
    channel: MessageChannel
    guild: GroupIdentity | None


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
    """Load group activation records."""
    return load_group_records_sync(groups_path or get_groups_path())


def save_group_records_sync(groups_path: Path, records: dict[str, dict[str, Any]]) -> None:
    """Persist group activation records."""
    groups_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_write(groups_path, overwrite=True, encoding="utf-8") as groups_file:
        groups_file.write(json.dumps(records, sort_keys=True, indent=2))


async def save_group_records(records: dict[str, dict[str, Any]], groups_path: Path | None = None) -> None:
    """Save group activation records."""
    save_group_records_sync(groups_path or get_groups_path(), records)


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


def get_message_group_name_and_id(message: MessageContext) -> tuple[str, int]:
    """Return the group state identity for an incoming Discord message."""
    if message.guild is not None:
        return message.guild.name, message.guild.id
    return get_private_channel_name(message.channel), message.channel.id


async def sync_group_records(
    guilds: Sequence[GroupIdentity],
    private_channels: Sequence[MessageChannel],
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


async def is_message_group_enabled(message: MessageContext) -> bool:
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


def can_manage_group_state(message: MessageContext) -> bool:
    """Return whether the command author can change the current group state."""
    if message.guild is None:
        return True
    permissions = getattr(message.author, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False) or getattr(permissions, "manage_guild", False))


async def set_message_group_enabled(message: MessageContext, *, enabled: bool) -> str:
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
