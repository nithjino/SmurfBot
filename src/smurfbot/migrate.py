"""One-off importer from the 0.2 per-guild files into state.json."""

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, TypeAdapter

from smurfbot.store import STATE_FILE, Reminder, State, Store, Tag, data_dir_lock

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger(__name__)

LEGACY_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


class MigrationError(Exception):
    """The import cannot run or a legacy file cannot be read."""


class _LegacyGroup(BaseModel):
    id: int
    enabled: bool = True


class _LegacyTag(BaseModel):
    owner: int
    content: str


class _LegacyTagFile(BaseModel):
    id: int
    tags: dict[str, _LegacyTag]


class _LegacyReminder(BaseModel):
    user_id: int
    message: str
    execution_time: str
    guild_id: int
    channel_id: int


class _LegacyReminderFile(BaseModel):
    reminders: list[_LegacyReminder]


_GROUPS = TypeAdapter(dict[str, _LegacyGroup])


def _read[T](adapter: TypeAdapter[T], path: Path) -> T:
    try:
        return adapter.validate_json(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError(f"Cannot read legacy file {path}: {type(exc).__name__}") from exc


def _json_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def convert(data_dir: Path) -> State:
    """Read the legacy files under data_dir and return the equivalent state."""
    state = State()
    groups = data_dir / "groups.json"
    if groups.is_file():
        state.deactivated = {group.id for group in _read(_GROUPS, groups).values() if not group.enabled}
    for path in _json_files(data_dir / "tags"):
        tag_file = _read(TypeAdapter(_LegacyTagFile), path)
        state.tags[tag_file.id] = {
            name: Tag(owner=tag.owner, content=tag.content) for name, tag in tag_file.tags.items()
        }
    for path in _json_files(data_dir / "reminders"):
        for old in _read(TypeAdapter(_LegacyReminderFile), path).reminders:
            try:
                due = datetime.strptime(old.execution_time, LEGACY_TIME_FORMAT).replace(tzinfo=UTC)
            except ValueError as exc:
                raise MigrationError(f"Cannot read legacy file {path}: bad execution_time") from exc
            state.reminders.append(
                Reminder(
                    guild_id=old.guild_id, channel_id=old.channel_id, user_id=old.user_id, message=old.message, due=due
                )
            )
    return state


def migrate(data_dir: Path) -> State:
    """Convert the legacy files under data_dir and write state.json."""
    with data_dir_lock(data_dir):
        store = Store(data_dir / STATE_FILE)
        if store.path.exists():
            raise MigrationError(f"{store.path} already exists; refusing to overwrite it")
        store.state = convert(data_dir)
        store.save()
        return store.state


def main(argv: Sequence[str] | None = None) -> None:
    """Run the importer from the command line."""
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 1:
        sys.exit("Usage: python -m smurfbot.migrate <data_dir>")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        state = migrate(Path(args[0]))
    except MigrationError as exc:
        sys.exit(str(exc))
    per_guild = ", ".join(f"{guild_id}: {len(tags)}" for guild_id, tags in state.tags.items()) or "none"
    log.info(
        "Imported %d deactivated groups, tags for %d guilds (%s), and %d reminders",
        len(state.deactivated),
        len(state.tags),
        per_guild,
        len(state.reminders),
    )


if __name__ == "__main__":
    main()
