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
        """Load reminders for a guild and schedule any pending reminders."""
        self.guild = guild
        self.reminders_json_path = Path(reminders_json_path)
        self.reminders_json_file = self.reminders_json_path / f"{guild.id}.json"
        self.reminders = self.load_reminders()
        self.loop = client.loop
        self.clean_reminders()
        asyncio.run_coroutine_threadsafe(self.parse_reminders(), self.loop)

    def create_json(self) -> None:
        """Create the reminders directory and guild JSON file when missing."""
        if not self.reminders_json_path.exists():
            self.reminders_json_path.mkdir(parents=True)
            _logger.info("created %s", self.reminders_json_path)
        # creates reminders json file for the group if it doesn't exist
        if not self.reminders_json_file.exists():
            create_json = ReminderFile(name=self.guild.name, id=self.guild.id)
            with self.reminders_json_file.open("w", encoding="utf-8") as reminders_file:
                json.dump(create_json.model_dump(mode="json"), reminders_file)
            _logger.info("%s: created: %s", self.guild.name, self.reminders_json_file)

    def load_reminders(self) -> ReminderFile:
        """Return the guild's reminders JSON data."""
        self.create_json()
        _logger.info("%s: loading: %s", self.guild.name, self.reminders_json_file)
        with self.reminders_json_file.open(encoding="utf-8") as reminders:
            return ReminderFile.model_validate(json.load(reminders))

    def save_reminders(self) -> None:
        """Write the guild's reminders to disk."""
        _logger.info("%s: saving: %s", self.guild.name, self.reminders_json_file)
        with atomic_write(self.reminders_json_file, overwrite=True, encoding="utf-8") as reminders_file:
            reminders_file.write(json.dumps(self.reminders.model_dump(mode="json"), sort_keys=True, indent=2))

    def clean_reminders(self) -> None:
        """Remove expired reminders and persist the cleaned reminder list."""
        _logger.info("%s: cleaning: %s", self.guild.name, self.reminders_json_file)
        self.reminders.reminders = [
            reminder for reminder in self.reminders.reminders if not has_datetime_passed(reminder.execution_time).result
        ]
        self.save_reminders()

    def list_reminders(self) -> str:
        """Return a formatted list of active reminders."""
        self.clean_reminders()
        messages: list[str] = []
        for reminder in self.reminders.reminders:
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
        for reminder in self.reminders.reminders:
            await self.parse_reminder(reminder)

    async def parse_reminder(self, reminder: ReminderRecord) -> None:
        """Schedule a reminder if it has not already expired."""
        _logger.info("%s: parse_reminder()", self.guild.name)
        passed_result = has_datetime_passed(reminder.execution_time)
        if not passed_result.result:
            self.create_timer(
                reminder.message,
                reminder.user_id,
                reminder.name,
                passed_result.seconds_until_execution,
                reminder.channel_id,
            )
        else:
            _logger.info(
                "%s: parse_reminder() - reminder for %s that says %s alredy expired",
                self.guild.name,
                reminder.name,
                reminder.message,
            )

    def create_timer(
        self,
        message: str,
        user_id: int,
        user_name: str,
        delay_seconds: float,
        channel_id: int,
    ) -> None:
        """Create an async timer that sends a reminder message later."""
        asyncio.run_coroutine_threadsafe(self.send_message(message, user_id, delay_seconds, channel_id), self.loop)
        _logger.info(
            "%s: created timer for %s that will execute in %s seconds",
            self.guild.name,
            user_name,
            delay_seconds,
        )

    async def send_message(self, message: str, user_id: int, seconds: float, channel_id: int) -> None:
        """Wait for the requested delay, then send the reminder to a channel."""
        await asyncio.sleep(seconds)
        channel = self.guild.get_channel(channel_id)
        if channel is None:
            _logger.warning("%s: channel %s was not found for reminder", self.guild.name, channel_id)
            return
        if not isinstance(channel, discord.abc.Messageable):
            _logger.warning("%s: channel %s cannot receive reminder messages", self.guild.name, channel_id)
            return
        await channel.send(f"<@{user_id}> reminder: {message}")

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
                return self.list_reminders()
            case _:
                delay_seconds = parse_time(seconds)
        if delay_seconds is None:
            return "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"
        if delay_seconds > MAX_REMINDER_SECONDS:
            return "Why are you using this feature for a reminder that far in the future?"
        reminder_message = " ".join(message)
        created_at = datetime.now(UTC)
        self.reminders.reminders.append(
            ReminderRecord(
                user_id=user_id,
                name=user.name,
                message=reminder_message,
                created_at=created_at.strftime(DATE_FORMAT),
                execution_time=add_time_to_date(created_at, delay_seconds).strftime(DATE_FORMAT),
                timezone="utc",
                guild_id=guild_id,
                channel_id=channel_id,
            )
        )
        self.save_reminders()
        execution_time = (
            (created_at + timedelta(seconds=delay_seconds))
            .astimezone(pytz.timezone("US/Eastern"))
            .strftime(HUMAN_DATE_FORMAT)
        )
        self.create_timer(reminder_message, user_id, user.name, delay_seconds, channel_id)
        return f"Created reminder for {user.name} to go off at {execution_time} that says `{reminder_message}`"
