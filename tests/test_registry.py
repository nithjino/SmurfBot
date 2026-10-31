import asyncio

import pytest

from smurfbot.core import GroupRef
from smurfbot.registry import GuildRegistry
from tests.support import run

GUILD = GroupRef(1, "Guild")


class Tracker:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.opened: list[int] = []
        self.closed: list[str] = []

    async def open(self, guild: GroupRef) -> str:
        self.opened.append(guild.id)
        await asyncio.sleep(0)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("open failed")
        return f"item-{guild.id}"

    async def close(self, item: str) -> None:
        self.closed.append(item)


def test_concurrent_gets_open_once() -> None:
    tracker = Tracker()
    registry = GuildRegistry(tracker.open, closer=tracker.close)

    async def exercise() -> list[str]:
        return list(await asyncio.gather(registry.get(GUILD), registry.get(GUILD)))

    assert run(exercise()) == ["item-1", "item-1"]
    assert tracker.opened == [1]


def test_failed_open_is_retried_on_next_use() -> None:
    tracker = Tracker(failures=1)
    registry = GuildRegistry(tracker.open)

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="open failed"):
            await registry.get(GUILD)
        assert await registry.get(GUILD) == "item-1"

    run(exercise())


def test_discard_closes_and_allows_reopening() -> None:
    tracker = Tracker()
    registry = GuildRegistry(tracker.open, closer=tracker.close)

    async def exercise() -> None:
        await registry.get(GUILD)
        await registry.discard(GUILD.id)
        await registry.discard(GUILD.id)
        await registry.get(GUILD)

    run(exercise())

    assert tracker.closed == ["item-1"]
    assert tracker.opened == [1, 1]


def test_close_discards_every_guild() -> None:
    tracker = Tracker()
    registry = GuildRegistry(tracker.open, closer=tracker.close)

    async def exercise() -> None:
        await registry.get(GUILD)
        await registry.get(GroupRef(2, "Other"))
        await registry.close()
        await registry.close()

    run(exercise())

    assert sorted(tracker.closed) == ["item-1", "item-2"]


def test_instance_is_removed_before_a_slow_close_finishes() -> None:
    async def exercise() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        opened: list[int] = []

        async def open_item(guild: GroupRef) -> str:
            opened.append(guild.id)
            return "item"

        async def close_item(_item: str) -> None:
            entered.set()
            await release.wait()

        registry = GuildRegistry(open_item, closer=close_item)
        await registry.get(GUILD)
        closing = asyncio.create_task(registry.discard(GUILD.id))
        await asyncio.wait_for(entered.wait(), timeout=1)
        await registry.get(GUILD)
        assert opened == [1, 1]
        release.set()
        await closing

    run(exercise())
