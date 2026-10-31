"""The pending reminders of one guild: persistence, timers, and delivery.

Delivery is at-least-once: a reminder leaves the file only after its send succeeded or was
abandoned, so a crash between the two re-sends it on the next start rather than losing it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Self
from zoneinfo import ZoneInfo
lazy from pathlib import Path

from smurfbot.core import CommandError, GroupRef
from smurfbot.reminders.models import REMINDER_FILE, Reminder, ReminderFile
from smurfbot.storage import JsonStore
from smurfbot.text import truncate

DISPLAY_TIMEZONE: Final = ZoneInfo("America/New_York")
DISPLAY_FORMAT: Final = "%m/%d/%Y @ %I:%M%p"
MAX_REMINDERS_PER_GUILD: Final = 100
MAX_DELIVERY_ATTEMPTS: Final = 10
BASE_RETRY_SECONDS: Final = 60
MAX_RETRY_SECONDS: Final = 3_600
MAX_SLEEP_SECONDS: Final = 3_600
LATE_AFTER: Final = timedelta(minutes=1)

_logger = logging.getLogger(__name__)

type ReminderSender = Callable[[Reminder, str], Awaitable[None]]


class PermanentDeliveryError(Exception):
    """Retrying cannot help, for example because the channel is gone."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Clock:
    """The time source and the delay primitive, replaceable in tests."""

    now: Callable[[], datetime] = _utc_now
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


def retry_delay(attempt: int) -> int:
    """Return the seconds to wait after a failed attempt, doubling up to a ceiling."""
    return min(BASE_RETRY_SECONDS * 2 ** (attempt - 1), MAX_RETRY_SECONDS)


def format_time(moment: datetime) -> str:
    """Render a moment for people to read."""
    return moment.astimezone(DISPLAY_TIMEZONE).strftime(DISPLAY_FORMAT)


class ReminderBook:
    """Hold a guild's pending reminders and one delivery task for each."""

    def __init__(self, store: JsonStore[ReminderFile], state: ReminderFile, send: ReminderSender, clock: Clock) -> None:
        """Use `open` outside of tests; it creates a missing file and starts the timers."""
        self._store = store
        self._state = state
        self._send = send
        self._clock = clock
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    async def open(cls, directory: Path, guild: GroupRef, send: ReminderSender, clock: Clock | None = None) -> Self:
        """Load a guild's reminders and schedule them, including any that came due while offline."""
        store = JsonStore(directory / f"{guild.id}.json", REMINDER_FILE)
        state = await store.load()
        if state is None:
            state = ReminderFile(name=guild.name, id=guild.id)
            await store.save(state)
        book = cls(store, state, send, clock or Clock())
        for reminder in state.reminders:
            book._schedule(reminder)
        return book

    @property
    def pending(self) -> tuple[Reminder, ...]:
        """Return every reminder that has not been delivered yet."""
        return self._state.reminders

    async def add(self, *, user_id: int, name: str, message: str, channel_id: int, delay_seconds: int) -> Reminder:
        """Persist a new reminder, then start its timer."""
        created_at = self._clock.now()
        reminder = Reminder(
            user_id=user_id,
            name=name,
            message=truncate(message),
            created_at=created_at,
            execution_time=created_at + timedelta(seconds=delay_seconds),
            guild_id=self._state.id,
            channel_id=channel_id,
        )
        async with self._lock:
            if len(self._state.reminders) >= MAX_REMINDERS_PER_GUILD:
                raise CommandError(
                    "Reminder limit reached. Delete or wait for a reminder to complete before creating a new one. "
                    f"Limit: {MAX_REMINDERS_PER_GUILD}"
                )
            state = self._state.model_copy(update={"reminders": (*self._state.reminders, reminder)})
            await self._store.save(state)
            self._state = state
        self._schedule(reminder)
        return reminder

    async def close(self) -> None:
        """Cancel the timers and wait for them; pending reminders stay on disk."""
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _schedule(self, reminder: Reminder) -> None:
        task = asyncio.create_task(self._run(reminder))
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            _logger.error("Guild %s: reminder task failed", self._state.id, exc_info=error)

    async def _run(self, reminder: Reminder) -> None:
        await self._sleep_until(reminder.execution_time)
        await self._deliver(reminder)
        await self._forget(reminder)

    async def _sleep_until(self, moment: datetime) -> None:
        """Sleep in bounded steps, so a jump in the wall clock can't delay a reminder for long."""
        while (remaining := (moment - self._clock.now()).total_seconds()) > 0:
            await self._clock.sleep(min(remaining, MAX_SLEEP_SECONDS))

    async def _deliver(self, reminder: Reminder) -> None:
        """Attempt delivery until it succeeds, can never succeed, or runs out of attempts."""
        for attempt in range(1, MAX_DELIVERY_ATTEMPTS + 1):
            try:
                await self._send(reminder, self._render(reminder))
            except PermanentDeliveryError:
                _logger.warning("Guild %s: abandoning undeliverable reminder", reminder.guild_id, exc_info=True)
                return
            except Exception:
                _logger.warning(
                    "Guild %s: reminder delivery to channel %s failed on attempt %s",
                    reminder.guild_id,
                    reminder.channel_id,
                    attempt,
                    exc_info=True,
                )
                if attempt < MAX_DELIVERY_ATTEMPTS:
                    await self._clock.sleep(retry_delay(attempt))
            else:
                return
        _logger.error(
            "Guild %s: failed to send reminder to channel %s after %s attempts",
            reminder.guild_id,
            reminder.channel_id,
            MAX_DELIVERY_ATTEMPTS,
        )

    def _render(self, reminder: Reminder) -> str:
        late = self._clock.now() - reminder.execution_time >= LATE_AFTER
        note = f" (was due {format_time(reminder.execution_time)})" if late else ""
        return truncate(f"<@{reminder.user_id}> reminder{note}: {reminder.message}")

    async def _forget(self, reminder: Reminder) -> None:
        """Drop a finished reminder; a failed save only risks one duplicate after a restart."""
        async with self._lock:
            remaining = list(self._state.reminders)
            remaining.remove(reminder)
            self._state = self._state.model_copy(update={"reminders": tuple(remaining)})
            try:
                await self._store.save(self._state)
            except OSError:
                _logger.exception("Guild %s: could not persist reminder removal", reminder.guild_id)
