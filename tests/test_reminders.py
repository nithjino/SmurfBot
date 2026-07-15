"""Tests for reminder parsing and creation behavior."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

import discord
import pytest

from message_limits import DISCORD_MESSAGE_LIMIT, TRUNCATION_SUFFIX
from reminders.constants import (
    DATE_FORMAT,
    MAX_REMINDER_SECONDS,
    MAX_REMINDERS_PER_GUILD,
    REMINDER_DELIVERY_ATTEMPTS,
    REMINDER_DELIVERY_RETRY_DELAY_SECONDS,
)
from reminders.models import ReminderRecord
from reminders.reminders import Reminders, add_time_to_date, has_datetime_passed, parse_time

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


class RecordingReminders(Reminders):
    def __init__(self, reminders_json_path: Path) -> None:
        super().__init__(SimpleNamespace(id=202, name="Guild"), reminders_json_path)
        self.saved_count = 0
        self.created_timers: list[tuple[ReminderRecord, float]] = []

    async def _save_reminders_unlocked(self) -> None:
        self.saved_count += 1

    def create_timer(self, reminder: ReminderRecord, delay_seconds: float) -> None:
        self.created_timers.append((reminder, delay_seconds))


class FakeGuild:
    id = 202
    name = "Guild"

    def __init__(self, channel: object) -> None:
        self.channel = channel
        self.channel_lookup_count = 0

    def get_channel(self, _channel_id: int) -> object:
        self.channel_lookup_count += 1
        return self.channel


class RecordingDeliveryReminders(Reminders):
    def __init__(self, channel: object, reminders_json_path: Path) -> None:
        self.fake_guild = FakeGuild(channel)
        super().__init__(self.fake_guild, reminders_json_path)
        self.saved_count = 0

    async def _save_reminders_unlocked(self) -> None:
        self.saved_count += 1


class FlakyMessageable(discord.abc.Messageable):
    def __init__(self, failures_before_success: int) -> None:
        self.attempts = 0
        self.failures_before_success = failures_before_success
        self.sent_messages: list[str] = []
        self.sent_kwargs: list[dict[str, object]] = []

    async def send(self, *args: object, **kwargs: object) -> None:
        self.attempts += 1
        if self.failures_before_success > 0:
            self.failures_before_success -= 1
            response = SimpleNamespace(status=500, reason="Server Error")
            raise discord.HTTPException(response, "send failed")
        self.sent_messages.append(str(args[0]))
        self.sent_kwargs.append(kwargs)


def make_reminder() -> ReminderRecord:
    return ReminderRecord(
        user_id=1,
        name="Alice",
        message="check laundry",
        created_at=datetime.now(UTC).strftime(DATE_FORMAT),
        execution_time=(datetime.now(UTC) + timedelta(minutes=5)).strftime(DATE_FORMAT),
        timezone="utc",
        guild_id=202,
        channel_id=303,
    )


def patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return sleep_calls


def test_parse_time_accepts_supported_units_and_rejects_bad_values() -> None:
    assert parse_time("30s") == 30
    assert parse_time("2m") == 120
    assert parse_time("3h") == 10_800
    assert parse_time("4d") == 345_600
    assert parse_time("") is None
    assert parse_time("10w") is None
    assert parse_time("soon") is None


def test_add_time_and_has_datetime_passed_support_datetime_strings() -> None:
    start = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)
    serialized_start = start.strftime(DATE_FORMAT)

    assert add_time_to_date(start, 90) == datetime(2026, 6, 4, 12, 1, 30, tzinfo=UTC)
    assert add_time_to_date(serialized_start, 90) == datetime(2026, 6, 4, 12, 1, 30, tzinfo=UTC)

    future = (datetime.now(UTC) + timedelta(seconds=60)).strftime(DATE_FORMAT)
    past = (datetime.now(UTC) - timedelta(seconds=60)).strftime(DATE_FORMAT)

    future_result = has_datetime_passed(future)
    past_result = has_datetime_passed(past)

    assert future_result.result is False
    assert 0 < future_result.seconds_until_execution <= 60
    assert past_result.result is True
    assert past_result.seconds_until_execution == -1


def test_create_reminder_rejects_invalid_durations_without_persisting(tmp_path: Path) -> None:
    reminders = RecordingReminders(tmp_path)

    unsupported = run(reminders.create_reminder("2w", ["stretch"], 1, 202, 303))
    too_far = run(reminders.create_reminder(f"{MAX_REMINDER_SECONDS + 1}s", ["stretch"], 1, 202, 303))
    past = run(reminders.create_reminder("0s", ["stretch"], 1, 202, 303))

    assert unsupported == "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"
    assert too_far == "Why are you using this feature for a reminder that far in the future?"
    assert past == "Why are you trying to set a reminder for the past?"
    assert reminders.saved_count == 0
    assert reminders.created_timers == []
    assert reminders.reminders.reminders == []


def test_create_reminder_persists_record_and_schedules_timer(tmp_path: Path) -> None:
    reminders = RecordingReminders(tmp_path)

    result = run(reminders.create_reminder("5m", ["check", "laundry"], 1, 202, 303, user_name="Alice"))

    assert result.startswith("Created reminder for Alice to go off at ")
    assert result.endswith(" that says `check laundry`")
    assert reminders.saved_count == 1
    assert len(reminders.reminders.reminders) == 1
    assert len(reminders.created_timers) == 1

    reminder = reminders.reminders.reminders[0]
    scheduled_reminder, delay_seconds = reminders.created_timers[0]

    assert scheduled_reminder is reminder
    assert delay_seconds == 300
    assert reminder.user_id == 1
    assert reminder.name == "Alice"
    assert reminder.message == "check laundry"
    assert reminder.guild_id == 202
    assert reminder.channel_id == 303


def test_create_reminder_uses_provided_user_name_without_fetching_user(tmp_path: Path) -> None:
    reminders = RecordingReminders(tmp_path)

    result = run(
        reminders.create_reminder(
            "5m",
            ["check", "laundry"],
            1,
            202,
            303,
            user_name="Alice From Message",
        )
    )

    assert result.startswith("Created reminder for Alice From Message to go off at ")
    assert reminders.reminders.reminders[0].name == "Alice From Message"


def test_create_reminder_falls_back_to_user_id_when_name_is_unavailable(tmp_path: Path) -> None:
    reminders = RecordingReminders(tmp_path)

    result = run(reminders.create_reminder("5m", ["check", "laundry"], 1, 202, 303))

    assert result.startswith("Created reminder for 1 to go off at ")
    assert reminders.reminders.reminders[0].name == "1"


def test_create_reminder_truncates_message_and_response_to_discord_limit(tmp_path: Path) -> None:
    reminders = RecordingReminders(tmp_path)
    long_message = "a" * (DISCORD_MESSAGE_LIMIT + 50)

    result = run(reminders.create_reminder("5m", [long_message], 1, 202, 303, user_name="Alice"))

    assert len(result) == DISCORD_MESSAGE_LIMIT
    assert result.endswith(TRUNCATION_SUFFIX)
    assert len(reminders.reminders.reminders[0].message) == DISCORD_MESSAGE_LIMIT
    assert reminders.reminders.reminders[0].message.endswith(TRUNCATION_SUFFIX)


def test_list_reminders_truncates_to_discord_limit(tmp_path: Path) -> None:
    reminders = RecordingReminders(tmp_path)
    future_execution = (datetime.now(UTC) + timedelta(days=1)).strftime(DATE_FORMAT)
    for index in range(25):
        reminders.reminders.reminders.append(
            ReminderRecord(
                user_id=index,
                name=f"User {index}",
                message="a" * 100,
                created_at=datetime.now(UTC).strftime(DATE_FORMAT),
                execution_time=future_execution,
                timezone="utc",
                guild_id=202,
                channel_id=303,
            )
        )

    result = run(reminders.list_reminders())

    assert len(result) == DISCORD_MESSAGE_LIMIT
    assert result.endswith(TRUNCATION_SUFFIX)


def test_create_reminder_rejects_new_reminders_when_guild_limit_is_reached(tmp_path: Path) -> None:
    reminders = RecordingReminders(tmp_path)
    future_execution = (datetime.now(UTC) + timedelta(days=1)).strftime(DATE_FORMAT)
    for index in range(MAX_REMINDERS_PER_GUILD):
        reminders.reminders.reminders.append(
            ReminderRecord(
                user_id=index,
                name=f"User {index}",
                message="stretch",
                created_at=datetime.now(UTC).strftime(DATE_FORMAT),
                execution_time=future_execution,
                timezone="utc",
                guild_id=202,
                channel_id=303,
            )
        )

    result = run(reminders.create_reminder("5m", ["stretch"], 1, 202, 303))

    assert result == (
        "Reminder limit reached. Delete or wait for a reminder to complete before creating a new one. Limit: "
        f"{MAX_REMINDERS_PER_GUILD}"
    )
    assert reminders.saved_count == 0
    assert reminders.created_timers == []
    assert len(reminders.reminders.reminders) == MAX_REMINDERS_PER_GUILD


def test_send_message_retries_failures_and_removes_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep_calls = patch_sleep(monkeypatch)
    channel = FlakyMessageable(failures_before_success=2)
    reminders = RecordingDeliveryReminders(channel, tmp_path)
    reminder = make_reminder()
    reminders.reminders.reminders.append(reminder)

    run(reminders.send_message(reminder, 0))

    assert channel.attempts == REMINDER_DELIVERY_ATTEMPTS
    assert channel.sent_messages == ["<@1> reminder: check laundry"]
    assert channel.sent_kwargs[0]["allowed_mentions"].to_dict() == {"users": [1], "parse": []}
    assert reminders.reminders.reminders == []
    assert reminders.saved_count == 1
    assert sleep_calls == [0, REMINDER_DELIVERY_RETRY_DELAY_SECONDS, REMINDER_DELIVERY_RETRY_DELAY_SECONDS]


def test_send_message_removes_reminder_after_final_send_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sleep_calls = patch_sleep(monkeypatch)
    channel = FlakyMessageable(failures_before_success=REMINDER_DELIVERY_ATTEMPTS)
    reminders = RecordingDeliveryReminders(channel, tmp_path)
    reminder = make_reminder()
    reminders.reminders.reminders.append(reminder)

    with caplog.at_level(logging.ERROR):
        run(reminders.send_message(reminder, 0))

    assert channel.attempts == REMINDER_DELIVERY_ATTEMPTS
    assert reminders.reminders.reminders == []
    assert reminders.saved_count == 1
    assert sleep_calls == [0, REMINDER_DELIVERY_RETRY_DELAY_SECONDS, REMINDER_DELIVERY_RETRY_DELAY_SECONDS]
    assert "failed to send reminder after 3 attempts" in caplog.text


@pytest.mark.parametrize("channel", [None, object()])
def test_send_message_retries_unavailable_channels_then_removes_reminder(
    channel: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sleep_calls = patch_sleep(monkeypatch)
    reminders = RecordingDeliveryReminders(channel, tmp_path)
    reminder = make_reminder()
    reminders.reminders.reminders.append(reminder)

    with caplog.at_level(logging.ERROR):
        run(reminders.send_message(reminder, 0))

    assert reminders.fake_guild.channel_lookup_count == REMINDER_DELIVERY_ATTEMPTS
    assert reminders.reminders.reminders == []
    assert reminders.saved_count == 1
    assert sleep_calls == [0, REMINDER_DELIVERY_RETRY_DELAY_SECONDS, REMINDER_DELIVERY_RETRY_DELAY_SECONDS]
    assert "failed to send reminder after 3 attempts" in caplog.text


def test_close_cancels_pending_timer_tasks(tmp_path: Path) -> None:
    async def exercise_close() -> tuple[int, int]:
        reminders = Reminders(SimpleNamespace(id=202, name="Guild"), tmp_path)
        reminders.create_timer(make_reminder(), 3_600)
        task_count_before_close = len(reminders._timer_tasks)  # noqa: SLF001
        await reminders.close()
        return task_count_before_close, len(reminders._timer_tasks)  # noqa: SLF001

    assert run(exercise_close()) == (1, 0)
