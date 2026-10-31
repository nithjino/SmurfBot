"""Shared test doubles for the transport-neutral core."""

import asyncio
import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from smurfbot.core import CommandContext, GroupRef, InboundMessage
from smurfbot.reminders import Clock
from smurfbot.reminders.models import Reminder

NOW = datetime(2026, 6, 4, 12, tzinfo=UTC)
GUILD = GroupRef(202, "Guild")


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


async def until(condition: Callable[[], bool]) -> None:
    """Wait for work that crosses a thread hop, failing instead of hanging."""
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.001)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeClock:
    """A clock that only moves when a test advances it."""

    def __init__(self, now: datetime = NOW) -> None:
        self.current = now
        self.sleeps: list[float] = []
        self._sleepers: list[tuple[datetime, asyncio.Future[None]]] = []
        self.clock = Clock(now=lambda: self.current, sleep=self._sleep)

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        future = asyncio.get_running_loop().create_future()
        self._sleepers.append((self.current + timedelta(seconds=seconds), future))
        await future

    @property
    def waiting(self) -> int:
        return sum(not future.done() for _, future in self._sleepers)

    async def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
        for deadline, future in self._sleepers:
            if deadline <= self.current and not future.done():
                future.set_result(None)
        await asyncio.sleep(0)


@dataclass
class RecordingSender:
    """A reminder transport that fails its first `failures` sends with `error`."""

    failures: int = 0
    error: Exception = field(default_factory=lambda: ConnectionError("network down"))
    attempts: list[tuple[Reminder, str]] = field(default_factory=list)

    async def __call__(self, reminder: Reminder, content: str) -> None:
        self.attempts.append((reminder, content))
        if len(self.attempts) <= self.failures:
            raise self.error


@dataclass
class Chat:
    """Builds inbound messages and records what the bot replied."""

    group: GroupRef = GUILD
    is_guild: bool = True
    can_manage_group: bool = True
    author_id: int = 7
    author_name: str = "Alice"
    channel_id: int = 303
    replies: list[str] = field(default_factory=list)
    reply_error: Exception | None = None

    def message(
        self, content: str, *, author_is_bot: bool = False, attachment_url: str | None = None
    ) -> InboundMessage:
        return InboundMessage(
            content=content,
            author_id=self.author_id,
            author_name=self.author_name,
            author_is_bot=author_is_bot,
            group=self.group,
            is_guild=self.is_guild,
            can_manage_group=self.can_manage_group,
            channel_id=self.channel_id,
            attachment_url=attachment_url,
            reply=self._reply,
        )

    async def _reply(self, content: str) -> None:
        if self.reply_error is not None:
            raise self.reply_error
        self.replies.append(content)


def context(
    *args: str,
    author_id: int = 7,
    author_name: str = "Alice",
    guild: GroupRef | None = GUILD,
    attachment_url: str | None = None,
) -> CommandContext:
    return CommandContext(
        args=args,
        author_id=author_id,
        author_name=author_name,
        guild=guild,
        channel_id=303,
        attachment_url=attachment_url,
    )
