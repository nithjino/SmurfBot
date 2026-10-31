"""The tags of one guild: rules, ownership, and persistence."""

import asyncio
from typing import Final, Self
lazy from pathlib import Path

from smurfbot.core import CommandError, GroupRef
from smurfbot.storage import JsonStore
from smurfbot.tags.models import TAG_FILE, TagFile, TagRecord
from smurfbot.text import truncate

MAX_TAGS_PER_GUILD: Final = 200
RESERVED_NAMES: Final = frozenset({"create", "delete", "edit", "filter", "gift", "help", "list", "owner", "rename"})


class TagBook:
    """Enforce tag rules, persisting each change before it becomes visible."""

    def __init__(self, store: JsonStore[TagFile], state: TagFile) -> None:
        """Use `open` outside of tests; it creates a missing file."""
        self._store = store
        self._state = state
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, directory: Path, guild: GroupRef) -> Self:
        """Load a guild's tags, creating an empty file on first use."""
        store = JsonStore(directory / f"{guild.id}.json", TAG_FILE)
        state = await store.load()
        if state is None:
            state = TagFile(name=guild.name, id=guild.id)
            await store.save(state)
        return cls(store, state)

    @property
    def names(self) -> tuple[str, ...]:
        """Return every tag name."""
        return tuple(self._state.tags)

    def get(self, name: str) -> TagRecord:
        """Return a tag, refusing when it does not exist."""
        record = self._state.tags.get(name)
        if record is None:
            raise CommandError(f'The tag "{name}" does not exist')
        return record

    async def create(self, name: str, *, owner: int, content: str) -> None:
        """Add a tag owned by its creator."""
        async with self._lock:
            self._require_available(name)
            if len(self._state.tags) >= MAX_TAGS_PER_GUILD:
                raise CommandError(
                    f"Tag limit reached. Delete a tag before creating a new one. Limit: {MAX_TAGS_PER_GUILD}"
                )
            if not content:
                raise CommandError("A tag needs either text content or an attachment")
            record = TagRecord(owner=owner, content=truncate(content))
            await self._commit({**self._state.tags, name: record})

    async def delete(self, name: str, *, actor: int) -> None:
        """Remove a tag on its owner's behalf."""
        async with self._lock:
            self._require_owned(name, actor)
            await self._commit({key: record for key, record in self._state.tags.items() if key != name})

    async def edit(self, name: str, *, actor: int, content: str) -> None:
        """Replace a tag's content on its owner's behalf."""
        async with self._lock:
            record = self._require_owned(name, actor)
            await self._commit({**self._state.tags, name: record.model_copy(update={"content": truncate(content)})})

    async def rename(self, name: str, new_name: str, *, actor: int) -> None:
        """Rename a tag on its owner's behalf."""
        async with self._lock:
            self._require_existing(name)
            self._require_available(new_name)
            record = self._require_owned(name, actor)
            remaining = {key: value for key, value in self._state.tags.items() if key != name}
            await self._commit({**remaining, new_name: record})

    async def gift(self, name: str, *, actor: int, new_owner: int) -> None:
        """Transfer a tag to a new owner on its current owner's behalf."""
        async with self._lock:
            record = self._require_owned(name, actor)
            await self._commit({**self._state.tags, name: record.model_copy(update={"owner": new_owner})})

    def _require_available(self, name: str) -> None:
        if name in RESERVED_NAMES:
            raise CommandError(f'The tag name "{name}" is reserved for a tag command')
        if name in self._state.tags:
            raise CommandError(f'The tag "{name}" already exists')

    def _require_existing(self, name: str) -> TagRecord:
        if not self._state.tags:
            raise CommandError("There are no tags")
        return self.get(name)

    def _require_owned(self, name: str, actor: int) -> TagRecord:
        record = self._require_existing(name)
        if record.owner != actor:
            raise CommandError(f'You are not the owner of the tag "{name}"')
        return record

    async def _commit(self, tags: dict[str, TagRecord]) -> None:
        state = self._state.model_copy(update={"tags": tags})
        await self._store.save(state)
        self._state = state
