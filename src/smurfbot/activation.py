"""Group activation: whether SmurfBot responds to commands in a given group."""

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Annotated, Final
lazy from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, PlainSerializer, TypeAdapter

lazy from smurfbot.core import GroupRef
lazy from smurfbot.storage import JsonStore

_logger = logging.getLogger(__name__)


class GroupRecord(BaseModel):
    """One group's persisted entry, keyed by display name in the file."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: Annotated[int, PlainSerializer(str, return_type=str)]
    enabled: bool = True


GROUP_FILE: Final = TypeAdapter(dict[str, GroupRecord])


@dataclass(frozen=True, slots=True)
class _Group:
    name: str
    enabled: bool = True


def _encode(groups: dict[int, _Group]) -> dict[str, GroupRecord]:
    """Build the name-keyed file format, keeping groups that share a name distinct."""
    records: dict[str, GroupRecord] = {}
    for group_id, group in groups.items():
        key = f"{group.name} ({group_id})" if group.name in records else group.name
        records[key] = GroupRecord(id=group_id, enabled=group.enabled)
    return records


class GroupActivation:
    """Own the activation state in memory, persisting every change before applying it."""

    def __init__(self, store: JsonStore[dict[str, GroupRecord]]) -> None:
        """Start empty; `load` must complete before the first command."""
        self._store = store
        self._groups: dict[int, _Group] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Read persisted choices, creating the file on first run."""
        records = await self._store.load()
        if records is None:
            await self._store.save({})
            return
        self._groups = {record.id: _Group(name, record.enabled) for name, record in records.items()}

    async def observe(self, groups: Iterable[GroupRef]) -> None:
        """Record new groups and renames, preserving activation and unseen groups."""
        async with self._lock:
            updated = dict(self._groups)
            for ref in groups:
                known = updated.get(ref.id)
                if known is None:
                    updated[ref.id] = _Group(ref.name)
                elif known.name != ref.name:
                    updated[ref.id] = replace(known, name=ref.name)
            if updated != self._groups:
                await self._commit(updated)

    async def is_enabled(self, group: GroupRef) -> bool:
        """Return a group's activation; failing to record it must not block the command."""
        try:
            await self.observe([group])
        except OSError:
            _logger.exception("Could not record group %s", group.id)
        known = self._groups.get(group.id)
        return known is None or known.enabled

    async def set_enabled(self, group: GroupRef, *, enabled: bool) -> None:
        """Persist a group's activation choice."""
        async with self._lock:
            await self._commit({**self._groups, group.id: _Group(group.name, enabled)})

    async def _commit(self, groups: dict[int, _Group]) -> None:
        await self._store.save(_encode(groups))
        self._groups = groups
