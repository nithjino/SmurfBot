"""Reminder command parsing, persistence, and timer scheduling."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import discord
import pytz
from atomicwrites import atomic_write

from constants import DATE_FORMAT, DURATION_UNIT_SECONDS, HUMAN_DATE_FORMAT, MAX_REMINDER_SECONDS

from .models import DatetimePassedResult, ReminderFile, ReminderRecord

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_logger = logging.getLogger(__name__)


def parse_time(time: str) -> int | None:
    """Convert a compact duration string into seconds."""
    if not time:
        return None

    unit = time[-1].lower()
    multiplier = DURATION_UNIT_SECONDS.get(unit)
    if multiplier is None:
        return None

    try:
        amount = int(time[:-1])
    except ValueError:
        _logger.info("Invalid reminder duration: %s", time)
        return None

    return amount * multiplier


def add_time_to_date(date: datetime | str, seconds: int) -> datetime:
    """Add seconds to a UTC datetime or serialized datetime string."""
    if isinstance(date, str):
        date = datetime.strptime(date, DATE_FORMAT).replace(tzinfo=UTC)
    return date + timedelta(seconds=seconds)


def has_datetime_passed(planned_execution_date: datetime | str) -> DatetimePassedResult:
    """Report whether a planned UTC execution datetime has passed."""
    current_datetime = datetime.now(UTC)
    if isinstance(planned_execution_date, str):
        planned_execution_date = datetime.strptime(planned_execution_date, DATE_FORMAT).replace(tzinfo=UTC)
    _logger.info(
        "has_datetime_passed() - current_datetime: (%s) %s %s",
        type(current_datetime),
        current_datetime,
        current_datetime.tzinfo,
    )
    _logger.info(
        "has_datetime_passed() - planned_execution_date: (%s) %s %s",
        type(planned_execution_date),
        planned_execution_date,
        planned_execution_date.tzinfo,
    )
    if planned_execution_date > current_datetime:
        _logger.info("has_datetime_passed() - seconds_until_execution: %s", planned_execution_date - current_datetime)
        _logger.info(
            "has_datetime_passed() - seconds_until_execution.total_seconds(): %s",
            (planned_execution_date - current_datetime).total_seconds(),
        )
        return DatetimePassedResult(
            result=False,
            seconds_until_execution=(planned_execution_date - current_datetime).total_seconds(),
        )
    _logger.info("has_datetime_passed() - %s has passed", planned_execution_date)
    return DatetimePassedResult(result=True, seconds_until_execution=-1)


class Reminders:
    """Manage persisted reminders and scheduled reminder messages for a guild."""

    def __init__(self, guild: discord.Guild, reminders_json_path: str | Path, client: discord.Client) -> None:
        """Initialize paths and default reminder state for a guild."""
        self.guild = guild
        self.reminders_json_path = Path(reminders_json_path)
        self.reminders_json_file = self.reminders_json_path / f"{guild.id}.json"
        self.reminders = ReminderFile(name=guild.name, id=guild.id)
        self.loop = client.loop
        self._timer_tasks: set[asyncio.Task[None]] = set()
        self._reminder_lock = asyncio.Lock()

    @classmethod
    async def create(cls, guild: discord.Guild, reminders_json_path: str | Path, client: discord.Client) -> Reminders:
        """Create a reminder handler and schedule any pending reminders."""
        reminder_handler = cls(guild, reminders_json_path, client)
        reminder_handler.reminders = await reminder_handler.load_reminders()
        await reminder_handler.clean_reminders()
        await reminder_handler.parse_reminders()
        return reminder_handler

    async def create_json(self) -> None:
        """Create the reminders directory and guild JSON file when missing."""

        def create_json_sync() -> None:
            if not self.reminders_json_path.exists():
                self.reminders_json_path.mkdir(parents=True)
                _logger.info("created %s", self.reminders_json_path)
            # creates reminders json file for the group if it doesn't exist
            if not self.reminders_json_file.exists():
                create_json = ReminderFile(name=self.guild.name, id=self.guild.id)
                with self.reminders_json_file.open("w", encoding="utf-8") as reminders_file:
                    json.dump(create_json.model_dump(mode="json"), reminders_file)
                _logger.info("%s: created: %s", self.guild.name, self.reminders_json_file)

        await asyncio.to_thread(create_json_sync)

    async def load_reminders(self) -> ReminderFile:
        """Return the guild's reminders JSON data."""
        await self.create_json()

        def load_reminders_sync() -> ReminderFile:
            _logger.info("%s: loading: %s", self.guild.name, self.reminders_json_file)
            with self.reminders_json_file.open(encoding="utf-8") as reminders:
                return ReminderFile.model_validate(json.load(reminders))

        return await asyncio.to_thread(load_reminders_sync)

    async def save_reminders(self) -> None:
        """Write the guild's reminders to disk."""
        async with self._reminder_lock:
            await self._save_reminders_unlocked()

    async def _save_reminders_unlocked(self) -> None:
        """Write reminders to disk while the caller holds the reminder lock."""

        def save_reminders_sync() -> None:
            _logger.info("%s: saving: %s", self.guild.name, self.reminders_json_file)
            with atomic_write(self.reminders_json_file, overwrite=True, encoding="utf-8") as reminders_file:
                reminders_file.write(json.dumps(self.reminders.model_dump(mode="json"), sort_keys=True, indent=2))

        await asyncio.to_thread(save_reminders_sync)

    async def clean_reminders(self) -> None:
        """Remove expired reminders and persist the cleaned reminder list."""
        _logger.info("%s: cleaning: %s", self.guild.name, self.reminders_json_file)
        async with self._reminder_lock:
            self.reminders.reminders = [
                reminder
                for reminder in self.reminders.reminders
                if not has_datetime_passed(reminder.execution_time).result
            ]
            await self._save_reminders_unlocked()

    async def list_reminders(self) -> str:
        """Return a formatted list of active reminders."""
        await self.clean_reminders()
        async with self._reminder_lock:
            reminders = list(self.reminders.reminders)
        messages: list[str] = []
        for reminder in reminders:
            reminder_date = reminder.execution_time
            if isinstance(reminder_date, str):
                reminder_date = datetime.strptime(reminder_date, DATE_FORMAT).replace(tzinfo=UTC)
            reminder_date = reminder_date.replace(tzinfo=UTC)
            display_date = reminder_date.astimezone(pytz.timezone("US/Eastern")).strftime(HUMAN_DATE_FORMAT)
            messages.append(
                f"\nCreated by: {reminder.name}\nReminder date: {display_date}\nReminder message: {reminder.message}\n"
            )
        if not messages:
            return "No active reminds were found"
        return "".join(messages)

    async def parse_reminders(self) -> None:
        """Schedule all currently persisted reminders."""
        _logger.info("%s: parse_reminders()", self.guild.name)
        _logger.info("%s: %s", self.guild.name, self.reminders)
        async with self._reminder_lock:
            reminders = list(self.reminders.reminders)
        for reminder in reminders:
            await self.parse_reminder(reminder)

    async def parse_reminder(self, reminder: ReminderRecord) -> None:
        """Schedule a reminder if it has not already expired."""
        _logger.info("%s: parse_reminder()", self.guild.name)
        passed_result = has_datetime_passed(reminder.execution_time)
        if not passed_result.result:
            await self.create_timer(reminder, passed_result.seconds_until_execution)
        else:
            _logger.info(
                "%s: parse_reminder() - reminder for %s that says %s alredy expired",
                self.guild.name,
                reminder.name,
                reminder.message,
            )

    async def create_timer(
        self,
        reminder: ReminderRecord,
        delay_seconds: float,
    ) -> None:
        """Create an async timer that sends a reminder message later."""
        task = self.loop.create_task(self.send_message(reminder, delay_seconds))
        self._timer_tasks.add(task)
        task.add_done_callback(self._timer_tasks.discard)
        _logger.info(
            "%s: created timer for %s that will execute in %s seconds",
            self.guild.name,
            reminder.name,
            delay_seconds,
        )

    async def remove_reminder(self, reminder: ReminderRecord) -> None:
        """Remove a completed reminder from memory and disk."""
        async with self._reminder_lock:
            try:
                self.reminders.reminders.remove(reminder)
            except ValueError:
                _logger.info("%s: reminder was already removed: %s", self.guild.name, reminder)
                return
            await self._save_reminders_unlocked()

    async def send_message(self, reminder: ReminderRecord, seconds: float) -> None:
        """Wait for the requested delay, then send the reminder to a channel."""
        await asyncio.sleep(seconds)
        channel = self.guild.get_channel(reminder.channel_id)
        if channel is None:
            _logger.warning("%s: channel %s was not found for reminder", self.guild.name, reminder.channel_id)
            return
        if not isinstance(channel, discord.abc.Messageable):
            _logger.warning("%s: channel %s cannot receive reminder messages", self.guild.name, reminder.channel_id)
            return
        await channel.send(f"<@{reminder.user_id}> reminder: {reminder.message}")
        await self.remove_reminder(reminder)

    async def create_reminder(
        self,
        seconds: str,
        message: list[str],
        user_id: int,
        _created_at: datetime | None,
        fetch_user: Callable[[int], Awaitable[discord.User]],
        guild_id: int | None,
        channel_id: int,
    ) -> str:
        """Create, persist, and schedule a reminder from a user command."""
        user = await fetch_user(user_id)
        match seconds:
            case "help":
                return (
                    "Format: $remind [amount of time] [message]. Example: $remind 1h check laundry\nSupport units: "
                    "s (seconds), m (minutes), h (hours), or d (days)."
                )
            case "list":
                return await self.list_reminders()
            case _:
                delay_seconds = parse_time(seconds)
        if delay_seconds is None:
            return "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"
        if delay_seconds > MAX_REMINDER_SECONDS:
            return "Why are you using this feature for a reminder that far in the future?"
        if delay_seconds <= 0:
            return "Why are you trying to set a reminder for the past?"
        reminder_message = " ".join(message)
        created_at = datetime.now(UTC)
        reminder = ReminderRecord(
            user_id=user_id,
            name=user.name,
            message=reminder_message,
            created_at=created_at.strftime(DATE_FORMAT),
            execution_time=add_time_to_date(created_at, delay_seconds).strftime(DATE_FORMAT),
            timezone="utc",
            guild_id=guild_id,
            channel_id=channel_id,
        )
        async with self._reminder_lock:
            self.reminders.reminders.append(reminder)
            await self._save_reminders_unlocked()
        execution_time = (
            (created_at + timedelta(seconds=delay_seconds))
            .astimezone(pytz.timezone("US/Eastern"))
            .strftime(HUMAN_DATE_FORMAT)
        )
        await self.create_timer(reminder, delay_seconds)
        return f"Created reminder for {user.name} to go off at {execution_time} that says `{reminder_message}`"
