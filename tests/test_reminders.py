"""Reminder behavior through construction, commands, delivery adapters, and shutdown."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import discord
import pytest
from pydantic import ValidationError

from message_limits import DISCORD_MESSAGE_LIMIT, TRUNCATION_SUFFIX
from models import CommandParameters
from reminders.constants import MAX_REMINDER_SECONDS, MAX_REMINDERS_PER_GUILD
from reminders.reminders import Reminders, _ReminderDependencies

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path

NOW = datetime(2026, 6, 4, 12, tzinfo=UTC)
HELP = (
    "Format: $remind [amount of time] [message]. Example: $remind 1h check laundry\nSupport units: "
    "s (seconds), m (minutes), h (hours), or d (days)."
)


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def command(*message: str, author_name: str = "Alice") -> CommandParameters:
    return CommandParameters(
        command="remind",
        message=list(message),
        created_at=NOW,
        author_id=1,
        author_name=author_name,
        guild_id=202,
        channel_id=303,
    )


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


def persisted(path: Path) -> dict[str, object]:
    return json.loads((path / "202.json").read_text(encoding="utf-8"))


class ControlledSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []
        self.waiting: asyncio.Queue[asyncio.Future[None]] = asyncio.Queue()
        self.cancelled = 0

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        future = asyncio.get_running_loop().create_future()
        self.waiting.put_nowait(future)
        try:
            await future
        except asyncio.CancelledError:
            self.cancelled += 1
            raise

    async def advance(self) -> None:
        future = await asyncio.wait_for(self.waiting.get(), timeout=1)
        future.set_result(None)
        await asyncio.sleep(0)


class RecordingChannel(discord.abc.Messageable):
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, object]]] = []

    async def send(self, content: str, **kwargs: object) -> None:
        self.sent.append((content, kwargs))


class FakeGuild:
    id = 202
    name = "Guild"

    def __init__(self, channel: object) -> None:
        self.channel = channel
        self.lookups: list[int] = []

    def get_channel(self, channel_id: int) -> object:
        self.lookups.append(channel_id)
        return self.channel


def test_construction_creates_exact_file(tmp_path: Path) -> None:
    async def exercise() -> None:
        reminders = await Reminders.create(FakeGuild(None), tmp_path)
        assert persisted(tmp_path) == {"name": "Guild", "id": 202, "reminders": []}
        await reminders.close()
        await reminders.close()

    run(exercise())


@pytest.mark.parametrize("message", [(), ("help",), ("list",), ("5m",), ("help", "ignored")])
def test_help_and_argument_count_quirk(tmp_path: Path, message: tuple[str, ...]) -> None:
    async def exercise() -> None:
        reminders = await Reminders.create(FakeGuild(None), tmp_path)
        before = (tmp_path / "202.json").read_bytes()
        assert await reminders.handle(command(*message)) == HELP
        assert (tmp_path / "202.json").read_bytes() == before
        await reminders.close()

    run(exercise())


@pytest.mark.parametrize(
    ("duration", "response"),
    [
        ("2w", "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"),
        ("soon", "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"),
        ("xs", "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"),
        (f"{MAX_REMINDER_SECONDS + 1}s", "Why are you using this feature for a reminder that far in the future?"),
        ("0s", "Why are you trying to set a reminder for the past?"),
        ("-1h", "Why are you trying to set a reminder for the past?"),
    ],
)
def test_invalid_duration_does_not_write_or_schedule(tmp_path: Path, duration: str, response: str) -> None:
    async def exercise() -> None:
        sleep = ControlledSleep()
        reminders = await Reminders.create(FakeGuild(None), tmp_path, _dependencies=_ReminderDependencies(sleep=sleep))
        before = (tmp_path / "202.json").stat()
        assert await reminders.handle(command(duration, "stretch")) == response
        assert (tmp_path / "202.json").stat() == before
        await asyncio.sleep(0)
        assert sleep.calls == []
        await reminders.close()

    run(exercise())


@pytest.mark.parametrize(("duration", "delay"), [("30s", 30), ("2M", 120), ("3h", 10800), ("4d", 345600)])
def test_supported_durations_schedule(tmp_path: Path, duration: str, delay: int) -> None:
    async def exercise() -> None:
        sleep = ControlledSleep()
        reminders = await Reminders.create(
            FakeGuild(None), tmp_path, _dependencies=_ReminderDependencies(now=lambda: NOW, sleep=sleep)
        )
        await reminders.handle(command(duration, "stretch"))
        await asyncio.sleep(0)
        assert sleep.calls == [delay]
        await reminders.close()

    run(exercise())


@pytest.mark.parametrize("author_name", ["Alice", ""])
def test_create_persists_before_scheduling_and_formats_dates(tmp_path: Path, author_name: str) -> None:
    async def exercise() -> None:
        sleep = ControlledSleep()
        snapshots: list[dict[str, object]] = []

        async def observe_sleep(seconds: float) -> None:
            snapshots.append(persisted(tmp_path))
            await sleep(seconds)

        reminders = await Reminders.create(
            FakeGuild(None), tmp_path, _dependencies=_ReminderDependencies(now=lambda: NOW, sleep=observe_sleep)
        )
        name = author_name or "1"
        assert await reminders.handle(command("5m", "check", "laundry", author_name=author_name)) == (
            f"Created reminder for {name} to go off at 06/04/2026 @ 08:05AM that says `check laundry`"
        )
        expected = {"name": "Guild", "id": 202, "reminders": [{**record(), "name": name}]}
        assert persisted(tmp_path) == expected
        await asyncio.sleep(0)
        assert snapshots == [expected]
        assert sleep.calls == [300]
        assert await reminders.handle(command("list", "ignored")) == (
            f"\nCreated by: {name}\nReminder date: 06/04/2026 @ 08:05AM\nReminder message: check laundry\n"
        )
        await reminders.close()

    run(exercise())


def test_load_cleans_expired_and_schedules_active_records(tmp_path: Path) -> None:
    source = {"name": "Guild", "id": 202, "reminders": [record(execution_time="2026-06-04T12:00:00"), record()]}
    (tmp_path / "202.json").write_text(json.dumps(source), encoding="utf-8")

    async def exercise() -> None:
        sleep = ControlledSleep()
        reminders = await Reminders.create(
            FakeGuild(None), tmp_path, _dependencies=_ReminderDependencies(now=lambda: NOW, sleep=sleep)
        )
        assert persisted(tmp_path) == {**source, "reminders": [record()]}
        await asyncio.sleep(0)
        assert sleep.calls == [300]
        await reminders.close()

    run(exercise())


@pytest.mark.parametrize(
    ("source", "error"),
    [("{bad json", json.JSONDecodeError), ('{"id": 202, "name": "Guild", "reminders": [{}]}', ValidationError)],
)
def test_malformed_source_unchanged(tmp_path: Path, source: str, error: type[Exception]) -> None:
    path = tmp_path / "202.json"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(error):
        run(Reminders.create(FakeGuild(None), tmp_path))
    assert path.read_text(encoding="utf-8") == source


def test_list_cleanup_and_empty_response(tmp_path: Path) -> None:
    async def exercise() -> None:
        now = NOW
        reminders = await Reminders.create(
            FakeGuild(None), tmp_path, _dependencies=_ReminderDependencies(now=lambda: now, sleep=ControlledSleep())
        )
        assert await reminders.handle(command("list", "ignored")) == "No active reminds were found"
        await reminders.handle(command("5m", "stretch"))
        now = datetime(2026, 6, 5, tzinfo=UTC)
        assert await reminders.handle(command("list", "ignored")) == "No active reminds were found"
        assert persisted(tmp_path)["reminders"] == []
        await reminders.close()

    run(exercise())


def test_truncation_and_guild_limit(tmp_path: Path) -> None:
    async def exercise() -> None:
        reminders = await Reminders.create(
            FakeGuild(None), tmp_path, _dependencies=_ReminderDependencies(now=lambda: NOW, sleep=ControlledSleep())
        )
        result = await reminders.handle(command("5m", "a" * (DISCORD_MESSAGE_LIMIT + 50)))
        assert len(result) == DISCORD_MESSAGE_LIMIT
        assert result.endswith(TRUNCATION_SUFFIX)
        saved = persisted(tmp_path)["reminders"][0]["message"]
        assert len(saved) == DISCORD_MESSAGE_LIMIT
        assert saved.endswith(TRUNCATION_SUFFIX)
        for _ in range(MAX_REMINDERS_PER_GUILD - 1):
            await reminders.handle(command("5m", "stretch"))
        before = (tmp_path / "202.json").read_bytes()
        assert await reminders.handle(command("5m", "stretch")) == (
            "Reminder limit reached. Delete or wait for a reminder to complete before creating a new one. Limit: 100"
        )
        assert (tmp_path / "202.json").read_bytes() == before
        result = await reminders.handle(command("list", "ignored"))
        assert len(result) == DISCORD_MESSAGE_LIMIT
        assert result.endswith(TRUNCATION_SUFFIX)
        await reminders.close()

    run(exercise())


@pytest.mark.parametrize("failures", [0, 2, 3])
def test_delivery_retries_then_persists_removal(
    tmp_path: Path, failures: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="reminders.reminders")

    async def exercise() -> None:
        sleep = ControlledSleep()
        channel = RecordingChannel()
        guild = FakeGuild(channel)
        attempts: list[tuple[object, int, str]] = []

        async def send(target: discord.abc.Messageable, user_id: int, content: str) -> None:
            attempts.append((target, user_id, content))
            if len(attempts) <= failures:
                raise discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "send failed")

        reminders = await Reminders.create(
            guild, tmp_path, _dependencies=_ReminderDependencies(now=lambda: NOW, sleep=sleep, send_reminder=send)
        )
        await reminders.handle(command("5m", "check", "laundry"))
        for _ in range(min(failures + 1, 3)):
            await sleep.advance()
        assert attempts == [(channel, 1, "check laundry")] * min(failures + 1, 3)
        assert guild.lookups == [303] * len(attempts)
        assert sleep.calls == [300] + [60] * (len(attempts) - 1)
        assert persisted(tmp_path)["reminders"] == []
        before = (tmp_path / "202.json").stat()
        await reminders.close()
        assert (tmp_path / "202.json").stat() == before

    run(exercise())
    assert sum(": saving:" in record.message for record in caplog.records) == 2
    if failures == 3:
        assert "failed to send reminder after 3 attempts" in caplog.text


@pytest.mark.parametrize("channel", [None, object()])
def test_unavailable_channel_retries_then_removes(tmp_path: Path, channel: object) -> None:
    async def exercise() -> None:
        sleep = ControlledSleep()
        guild = FakeGuild(channel)
        reminders = await Reminders.create(
            guild, tmp_path, _dependencies=_ReminderDependencies(now=lambda: NOW, sleep=sleep)
        )
        await reminders.handle(command("5m", "stretch"))
        for _ in range(3):
            await sleep.advance()
        assert guild.lookups == [303, 303, 303]
        assert sleep.calls == [300, 60, 60]
        assert persisted(tmp_path)["reminders"] == []
        await reminders.close()

    run(exercise())


def test_default_sender_preserves_mention_policy(tmp_path: Path) -> None:
    async def exercise() -> None:
        sleep = ControlledSleep()
        channel = RecordingChannel()
        reminders = await Reminders.create(
            FakeGuild(channel), tmp_path, _dependencies=_ReminderDependencies(now=lambda: NOW, sleep=sleep)
        )
        await reminders.handle(command("5m", "check", "laundry"))
        await sleep.advance()
        content, kwargs = channel.sent[0]
        assert content == "<@1> reminder: check laundry"
        assert kwargs["allowed_mentions"].to_dict() == {"users": [1], "parse": []}
        assert persisted(tmp_path)["reminders"] == []
        await reminders.close()

    run(exercise())


def test_close_awaits_cancellation_and_retains_pending_records(tmp_path: Path) -> None:
    async def exercise() -> None:
        sleep = ControlledSleep()
        guild = FakeGuild(RecordingChannel())
        reminders = await Reminders.create(
            guild, tmp_path, _dependencies=_ReminderDependencies(now=lambda: NOW, sleep=sleep)
        )
        await reminders.handle(command("5m", "stretch"))
        await reminders.handle(command("1h", "rest"))
        await asyncio.sleep(0)
        before = (tmp_path / "202.json").read_bytes()
        await reminders.close()
        assert sleep.cancelled == 2
        assert guild.lookups == []
        assert (tmp_path / "202.json").read_bytes() == before
        await reminders.close()

    run(exercise())


def test_cancelled_delivery_does_not_remove_or_retry(tmp_path: Path) -> None:
    async def exercise() -> None:
        sleep = ControlledSleep()
        guild = FakeGuild(RecordingChannel())

        async def send(_channel: discord.abc.Messageable, _user_id: int, _content: str) -> None:
            raise asyncio.CancelledError

        reminders = await Reminders.create(
            guild, tmp_path, _dependencies=_ReminderDependencies(now=lambda: NOW, sleep=sleep, send_reminder=send)
        )
        await reminders.handle(command("5m", "stretch"))
        before = (tmp_path / "202.json").read_bytes()
        await sleep.advance()
        await reminders.close()
        assert sleep.calls == [300]
        assert guild.lookups == [303]
        assert (tmp_path / "202.json").read_bytes() == before

    run(exercise())


def test_command_cancellation_propagates(tmp_path: Path) -> None:
    def cancelled_now() -> datetime:
        raise asyncio.CancelledError

    async def exercise() -> None:
        reminders = await Reminders.create(
            FakeGuild(None), tmp_path, _dependencies=_ReminderDependencies(now=cancelled_now)
        )
        with pytest.raises(asyncio.CancelledError):
            await reminders.handle(command("5m", "stretch"))
        assert persisted(tmp_path)["reminders"] == []
        await reminders.close()

    run(exercise())
