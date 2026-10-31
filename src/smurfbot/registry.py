"""Per-guild service instances, opened once and closed exactly once."""

import asyncio
lazy from collections.abc import Awaitable, Callable

lazy from smurfbot.core import GroupRef


class GuildRegistry[T]:
    """Open one T per guild on first use; a failed open is retried on the next use."""

    def __init__(
        self,
        opener: Callable[[GroupRef], Awaitable[T]],
        *,
        closer: Callable[[T], Awaitable[None]] | None = None,
    ) -> None:
        """Keep the lifecycle callbacks for the guild-scoped service."""
        self._opener = opener
        self._closer = closer
        self._items: dict[int, T] = {}
        self._lock = asyncio.Lock()

    async def get(self, guild: GroupRef) -> T:
        """Return the guild's instance, opening it if needed."""
        if guild.id in self._items:
            return self._items[guild.id]
        async with self._lock:
            if guild.id not in self._items:
                self._items[guild.id] = await self._opener(guild)
            return self._items[guild.id]

    async def discard(self, guild_id: int) -> None:
        """Forget a guild's instance before closing it, so it can't serve new work."""
        async with self._lock:
            item = self._items.pop(guild_id, None)
        if item is not None and self._closer is not None:
            await self._closer(item)

    async def close(self) -> None:
        """Discard every guild's instance."""
        async with asyncio.TaskGroup() as group:
            for guild_id in tuple(self._items):
                group.create_task(self.discard(guild_id))
