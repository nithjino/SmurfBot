import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from smurfbot import storage
from smurfbot.core import CommandError, GroupRef
from smurfbot.registry import GuildRegistry
from smurfbot.reminders import Clock, PermanentDeliveryError, RemindCommand, ReminderBook, ReminderSender
from smurfbot.reminders.book import (
    BASE_RETRY_SECONDS,
    MAX_DELIVERY_ATTEMPTS,
    MAX_REMINDERS_PER_GUILD,
    MAX_RETRY_SECONDS,
    MAX_SLEEP_SECONDS,
    retry_delay,
)
from smurfbot.reminders.commands import HELP_TEXT, MAX_REMINDER_SECONDS, parse_delay
from smurfbot.storage import StateFileError
from smurfbot.text import DISCORD_MESSAGE_LIMIT, TRUNCATION_SUFFIX
from tests.support import GUILD, NOW, FakeClock, RecordingSender, context, read_json, run, until

GUILD_FILE = f"{GUILD.id}.json"


def record(*, execution_time: str = "2026-06-04T12:05:00") -> dict[str, object]:
    return {
        "user_id": 1,
        "name": "Alice",
        "message": "check laundry",
        "created_at": "2026-06-04T12:00:00",
        "execution_time": execution_time,
        "timezone": "utc",
        "guild_id": 202,
        "channel_id": 303,
    }


def seed(directory: Path, *reminders: dict[str, object]) -> None:
    path = directory / GUILD_FILE
    path.write_text(json.dumps({"name": "Guild", "id": 202, "reminders": list(reminders)}), encoding="utf-8")


def pending(directory: Path) -> list[dict[str, Any]]:
    return read_json(directory / GUILD_FILE)["reminders"]


async def remind(command: RemindCommand, *args: str, author_name: str = "Alice") -> str:
    """Run a command the way the router would, showing refusals as replies."""
    try:
        return await command(context(*args, author_id=1, author_name=author_name))
    except CommandError as error:
        return error.message


class Harness:
    def __init__(self, directory: Path, send: ReminderSender | None = None, clock: FakeClock | None = None) -> None:
        self.directory = directory
        self.send = send or RecordingSender()
        self.fake = clock or FakeClock()
        self.registry = GuildRegistry(self._open, closer=ReminderBook.close)
        self.command = RemindCommand(self.registry)

    async def _open(self, guild: GroupRef) -> ReminderBook:
        return await ReminderBook.open(self.directory, guild, self.send, self.fake.clock)

    async def open(self) -> ReminderBook:
        return await self.registry.get(GUILD)

    async def close(self) -> None:
        await self.registry.close()


def test_first_use_creates_the_exact_file(tmp_path: Path) -> None:
    async def exercise() -> None:
        harness = Harness(tmp_path)
        await harness.open()
        assert read_json(tmp_path / GUILD_FILE) == {"name": "Guild", "id": 202, "reminders": []}
        await harness.close()

    run(exercise())


@pytest.mark.parametrize("args", [(), ("help",), ("5m",), ("help", "ignored")])
def test_help(tmp_path: Path, args: tuple[str, ...]) -> None:
    async def exercise() -> None:
        harness = Harness(tmp_path)
        assert await remind(harness.command, *args) == HELP_TEXT
        await harness.close()

    run(exercise())


@pytest.mark.parametrize(
    ("duration", "response"),
    [
        ("2w", "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"),
        ("soon", "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"),
        ("xs", "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"),
        ("s", "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"),
        (f"{MAX_REMINDER_SECONDS + 1}s", "Why are you using this feature for a reminder that far in the future?"),
        ("0s", "Why are you trying to set a reminder for the past?"),
        ("-1h", "Why are you trying to set a reminder for the past?"),
    ],
)
def test_invalid_durations_do_not_write_or_schedule(tmp_path: Path, duration: str, response: str) -> None:
    async def exercise() -> None:
        harness = Harness(tmp_path)
        await harness.open()
        before = (tmp_path / GUILD_FILE).stat()
        assert await remind(harness.command, duration, "stretch") == response
        assert (tmp_path / GUILD_FILE).stat() == before
        await asyncio.sleep(0)
        assert harness.fake.sleeps == []
        await harness.close()

    run(exercise())


@pytest.mark.parametrize(("duration", "seconds"), [("30s", 30), ("2M", 120), ("3h", 10800), ("4d", 345600)])
def test_supported_durations(duration: str, seconds: int) -> None:
    assert parse_delay(duration) == seconds


@pytest.mark.parametrize("author_name", ["Alice", ""])
def test_create_persists_first_and_formats_dates(tmp_path: Path, author_name: str) -> None:
    async def exercise() -> None:
        snapshots: list[list[dict[str, Any]]] = []
        fake = FakeClock()

        async def observing_sleep(seconds: float) -> None:
            snapshots.append(pending(tmp_path))
            await original_sleep(seconds)

        original_sleep = fake.clock.sleep
        fake.clock = Clock(now=fake.clock.now, sleep=observing_sleep)
        harness = Harness(tmp_path, clock=fake)
        name = author_name or "1"
        assert await remind(harness.command, "5m", "check", "laundry", author_name=author_name) == (
            f"Created reminder for {name} to go off at 06/04/2026 @ 08:05AM that says `check laundry`"
        )
        expected = [{**record(), "name": name}]
        assert pending(tmp_path) == expected
        await asyncio.sleep(0)
        assert snapshots == [expected]
        assert fake.sleeps == [300]
        assert await remind(harness.command, "list", "ignored") == (
            f"\nCreated by: {name}\nReminder date: 06/04/2026 @ 08:05AM\nReminder message: check laundry\n"
        )
        await harness.close()

    run(exercise())


def test_bare_list_lists_instead_of_showing_help(tmp_path: Path) -> None:
    async def exercise() -> None:
        harness = Harness(tmp_path)
        assert await remind(harness.command, "list") == "No active reminders were found"
        await remind(harness.command, "5m", "stretch")
        assert "Reminder message: stretch" in await remind(harness.command, "list")
        await harness.close()

    run(exercise())


def test_delivery_sends_then_removes(tmp_path: Path) -> None:
    async def exercise() -> None:
        send = RecordingSender()
        harness = Harness(tmp_path, send)
        await remind(harness.command, "5m", "check", "laundry")
        await until(lambda: harness.fake.waiting == 1)
        await harness.fake.advance(299)
        await until(lambda: harness.fake.waiting == 1)
        assert send.attempts == []
        await harness.fake.advance(1)
        await until(lambda: pending(tmp_path) == [])
        assert [content for _, content in send.attempts] == ["<@1> reminder: check laundry"]
        await harness.close()

    run(exercise())


def test_reminders_due_while_offline_are_delivered_late_not_dropped(tmp_path: Path) -> None:
    seed(tmp_path, record(execution_time="2026-06-04T11:00:00"))

    async def exercise() -> None:
        send = RecordingSender()
        harness = Harness(tmp_path, send)
        await harness.open()
        await until(lambda: pending(tmp_path) == [])
        assert [content for _, content in send.attempts] == [
            "<@1> reminder (was due 06/04/2026 @ 07:00AM): check laundry"
        ]
        await harness.close()

    run(exercise())


def test_future_reminders_resume_with_the_remaining_delay(tmp_path: Path) -> None:
    seed(tmp_path, record())

    async def exercise() -> None:
        harness = Harness(tmp_path)
        await harness.open()
        await until(lambda: harness.fake.sleeps == [300])
        assert pending(tmp_path) == [record()]
        await harness.close()

    run(exercise())


def test_list_shows_overdue_reminders_still_being_retried(tmp_path: Path) -> None:
    async def exercise() -> None:
        send = RecordingSender(failures=1)
        harness = Harness(tmp_path, send)
        await remind(harness.command, "5m", "stretch")
        await until(lambda: harness.fake.waiting == 1)
        await harness.fake.advance(300)
        await until(lambda: len(send.attempts) == 1 and harness.fake.waiting == 1)
        assert "Reminder message: stretch" in await remind(harness.command, "list")
        assert len(pending(tmp_path)) == 1
        await harness.close()

    run(exercise())


def test_long_waits_recheck_the_wall_clock(tmp_path: Path) -> None:
    async def exercise() -> None:
        send = RecordingSender()
        harness = Harness(tmp_path, send)
        await remind(harness.command, "2h", "stretch")
        await until(lambda: harness.fake.sleeps == [MAX_SLEEP_SECONDS])
        harness.fake.current += timedelta(hours=1, minutes=59)
        await harness.fake.advance(MAX_SLEEP_SECONDS)
        await until(lambda: pending(tmp_path) == [])
        assert len(send.attempts) == 1
        await harness.close()

    run(exercise())


@pytest.mark.parametrize(
    "error",
    [ConnectionError("network"), TimeoutError(), RuntimeError("anything else")],
    ids=["network", "timeout", "unexpected"],
)
def test_any_failure_is_retried_until_delivered(tmp_path: Path, error: Exception) -> None:
    """A dropped connection used to kill the timer, stranding the reminder until a restart."""

    async def exercise() -> None:
        send = RecordingSender(failures=2, error=error)
        harness = Harness(tmp_path, send)
        await remind(harness.command, "5m", "stretch")
        await until(lambda: harness.fake.waiting == 1)
        await harness.fake.advance(300)
        for attempt in (1, 2):
            await until(lambda: len(send.attempts) == attempt and harness.fake.waiting == 1)  # noqa: B023
            assert len(pending(tmp_path)) == 1
            await harness.fake.advance(retry_delay(attempt))
        await until(lambda: pending(tmp_path) == [])
        assert len(send.attempts) == 3
        assert harness.fake.sleeps == [300, 60, 120]
        await harness.close()

    run(exercise())


def test_retry_delay_doubles_up_to_the_ceiling() -> None:
    assert retry_delay(1) == BASE_RETRY_SECONDS
    assert retry_delay(2) == BASE_RETRY_SECONDS * 2
    assert retry_delay(MAX_DELIVERY_ATTEMPTS) == MAX_RETRY_SECONDS


def test_gives_up_after_the_last_attempt(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        send = RecordingSender(failures=MAX_DELIVERY_ATTEMPTS)
        harness = Harness(tmp_path, send)
        await remind(harness.command, "5m", "stretch")
        await until(lambda: harness.fake.waiting == 1)
        await harness.fake.advance(300)
        for attempt in range(1, MAX_DELIVERY_ATTEMPTS):
            await until(lambda: len(send.attempts) == attempt and harness.fake.waiting == 1)  # noqa: B023
            await harness.fake.advance(retry_delay(attempt))
        await until(lambda: pending(tmp_path) == [])
        assert len(send.attempts) == MAX_DELIVERY_ATTEMPTS
        await harness.close()

    with caplog.at_level(logging.ERROR):
        run(exercise())

    assert f"after {MAX_DELIVERY_ATTEMPTS} attempts" in caplog.text


def test_permanent_failures_are_not_retried(tmp_path: Path) -> None:
    async def exercise() -> None:
        send = RecordingSender(failures=1, error=PermanentDeliveryError("channel deleted"))
        harness = Harness(tmp_path, send)
        await remind(harness.command, "5m", "stretch")
        await until(lambda: harness.fake.waiting == 1)
        await harness.fake.advance(300)
        await until(lambda: pending(tmp_path) == [])
        assert len(send.attempts) == 1
        assert harness.fake.sleeps == [300]
        await harness.close()

    run(exercise())


def test_duplicate_reminders_each_deliver_once(tmp_path: Path) -> None:
    seed(tmp_path, record(), record())

    async def exercise() -> None:
        send = RecordingSender()
        harness = Harness(tmp_path, send)
        await harness.open()
        await until(lambda: harness.fake.waiting == 2)
        await harness.fake.advance(300)
        await until(lambda: pending(tmp_path) == [])
        assert len(send.attempts) == 2
        await harness.close()

    run(exercise())


def test_close_cancels_timers_and_keeps_pending_reminders(tmp_path: Path) -> None:
    async def exercise() -> None:
        send = RecordingSender()
        harness = Harness(tmp_path, send)
        await remind(harness.command, "5m", "stretch")
        await remind(harness.command, "1h", "rest")
        await until(lambda: harness.fake.waiting == 2)
        before = (tmp_path / GUILD_FILE).read_bytes()
        await harness.close()
        assert send.attempts == []
        assert (tmp_path / GUILD_FILE).read_bytes() == before
        await harness.close()

    run(exercise())


def test_cancelled_send_keeps_the_reminder_for_the_next_start(tmp_path: Path) -> None:
    async def exercise() -> None:
        started = asyncio.Event()

        async def send(_reminder: object, _content: str) -> None:
            started.set()
            await asyncio.Event().wait()

        harness = Harness(tmp_path, send)
        await remind(harness.command, "5m", "stretch")
        await until(lambda: harness.fake.waiting == 1)
        await harness.fake.advance(300)
        await asyncio.wait_for(started.wait(), timeout=1)
        await harness.close()
        assert len(pending(tmp_path)) == 1

    run(exercise())


def test_failed_removal_is_logged_and_only_risks_a_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    async def exercise() -> None:
        harness = Harness(tmp_path)
        book = await harness.open()
        await remind(harness.command, "5m", "stretch")
        await until(lambda: harness.fake.waiting == 1)
        monkeypatch.setattr(storage, "write_atomic", fail)
        await harness.fake.advance(300)
        await until(lambda: "could not persist reminder removal" in caplog.text)
        assert book.pending == ()
        assert len(pending(tmp_path)) == 1
        await harness.close()

    run(exercise())


def test_failed_save_does_not_schedule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    async def exercise() -> None:
        harness = Harness(tmp_path)
        book = await harness.open()
        monkeypatch.setattr(storage, "write_atomic", fail)
        with pytest.raises(OSError, match="disk full"):
            await remind(harness.command, "5m", "stretch")
        await asyncio.sleep(0)
        assert book.pending == ()
        assert harness.fake.sleeps == []
        await harness.close()

    run(exercise())


def test_truncation_and_guild_limit(tmp_path: Path) -> None:
    async def exercise() -> None:
        harness = Harness(tmp_path)
        await remind(harness.command, "5m", "a" * (DISCORD_MESSAGE_LIMIT + 50))
        saved = pending(tmp_path)[0]["message"]
        assert len(saved) == DISCORD_MESSAGE_LIMIT
        assert saved.endswith(TRUNCATION_SUFFIX)
        for _ in range(MAX_REMINDERS_PER_GUILD - 1):
            await remind(harness.command, "5m", "stretch")
        before = (tmp_path / GUILD_FILE).read_bytes()
        assert await remind(harness.command, "5m", "stretch") == (
            "Reminder limit reached. Delete or wait for a reminder to complete before creating a new one. Limit: 100"
        )
        assert (tmp_path / GUILD_FILE).read_bytes() == before
        await harness.close()

    run(exercise())


def test_delivered_text_fits_a_discord_message(tmp_path: Path) -> None:
    async def exercise() -> None:
        send = RecordingSender()
        harness = Harness(tmp_path, send)
        await remind(harness.command, "5m", "a" * DISCORD_MESSAGE_LIMIT)
        await until(lambda: harness.fake.waiting == 1)
        await harness.fake.advance(300)
        await until(lambda: pending(tmp_path) == [])
        assert len(send.attempts[0][1]) == DISCORD_MESSAGE_LIMIT
        await harness.close()

    run(exercise())


@pytest.mark.parametrize(
    "source",
    [
        "{bad json",
        '{"id": 202, "name": "Guild", "reminders": [{}]}',
        json.dumps({"id": 202, "name": "Guild", "reminders": [record(execution_time="tomorrow")]}),
    ],
)
def test_corrupt_files_are_refused_and_stay_untouched(tmp_path: Path, source: str) -> None:
    path = tmp_path / GUILD_FILE
    path.write_text(source, encoding="utf-8")

    async def exercise() -> None:
        harness = Harness(tmp_path)
        with pytest.raises(StateFileError):
            await harness.open()

    run(exercise())

    assert path.read_text(encoding="utf-8") == source


def test_saved_timestamps_are_utc_whatever_the_clock_zone(tmp_path: Path) -> None:
    eastern_noon = datetime(2026, 6, 4, 8, tzinfo=NOW.tzinfo) + timedelta(hours=4)
    assert eastern_noon.astimezone(UTC) == NOW

    async def exercise() -> None:
        harness = Harness(tmp_path, clock=FakeClock(eastern_noon))
        await remind(harness.command, "5m", "stretch")
        assert pending(tmp_path)[0]["created_at"] == "2026-06-04T12:00:00"
        await harness.close()

    run(exercise())


def test_reminders_need_a_guild(tmp_path: Path) -> None:
    async def exercise() -> None:
        harness = Harness(tmp_path)
        assert await harness.command(context(guild=None)) == HELP_TEXT
        for args in (("list",), ("5m", "stretch")):
            with pytest.raises(CommandError, match="only work in a server"):
                await harness.command(context(*args, guild=None))

    run(exercise())
