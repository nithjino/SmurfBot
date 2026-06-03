"""Reminder command parsing, persistence, and timer scheduling."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import pytz
from atomicwrites import atomic_write

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import discord

_logger = logging.getLogger(__name__)

date_format = "%Y-%m-%dT%H:%M:%S"
human_date_format = "%m/%d/%Y @ %I:%M%p"
MAX_REMINDER_SECONDS = 31_557_600


class _DatetimePassedResult(TypedDict):
    result: bool
    seconds_until_execution: float


class _Reminder(TypedDict):
    user_id: int
    name: str
    message: str
    created_at: str
    execution_time: str | datetime
    timezone: str
    guild_id: int | None
    channel_id: int


class _ReminderFile(TypedDict):
    name: str
    id: int
    reminders: list[_Reminder]


def parse_time(time: str) -> int | None:
    """Convert a compact duration string into seconds."""
    try:
        unit = time[-1].lower()
        amount = int(time[:-1])
        match unit:
            case "s":
                pass
            case "m":
                amount = amount * 60
            case "h":
                amount = amount * 60 * 60
            case "d":
                amount = amount * 60 * 60 * 24
            case _:
                amount = None
    except Exception:
        _logger.exception("remind (parse_time) exception")
        amount = None
    return amount


def add_time_to_date(date: datetime | str, seconds: int) -> datetime:
    """Add seconds to a UTC datetime or serialized datetime string."""
    if isinstance(date, str):
        date = datetime.strptime(date, date_format).replace(tzinfo=UTC)
    return date + timedelta(seconds=seconds)


def has_datetime_passed(planned_execution_date: datetime | str) -> _DatetimePassedResult:
    """Report whether a planned UTC execution datetime has passed."""
    current_datetime = datetime.now(UTC)
    if isinstance(planned_execution_date, str):
        planned_execution_date = datetime.strptime(planned_execution_date, date_format).replace(tzinfo=UTC)
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
        return {"result": False, "seconds_until_execution": (planned_execution_date - current_datetime).total_seconds()}
    _logger.info("has_datetime_passed() - %s has passed", planned_execution_date)
    return {"result": True, "seconds_until_execution": -1}


class Remind:
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
            create_json: _ReminderFile = {"name": self.guild.name, "id": self.guild.id, "reminders": []}
            with self.reminders_json_file.open("w", encoding="utf-8") as reminders_file:
                json.dump(create_json, reminders_file)
            _logger.info("%s: created: %s", self.guild.name, self.reminders_json_file)

    def load_reminders(self) -> _ReminderFile:
        """Return the guild's reminders JSON data."""
        self.create_json()
        _logger.info("%s: loading: %s", self.guild.name, self.reminders_json_file)
        with self.reminders_json_file.open(encoding="utf-8") as reminders:
            return json.load(reminders)

    def save_reminders(self) -> None:
        """Write the guild's reminders to disk."""
        _logger.info("%s: saving: %s", self.guild.name, self.reminders_json_file)
        with atomic_write(self.reminders_json_file, overwrite=True, encoding="utf-8") as reminders_file:
            reminders_file.write(json.dumps(self.reminders, sort_keys=True, indent=2))

    def clean_reminders(self) -> None:
        """Remove expired reminders and persist the cleaned reminder list."""
        _logger.info("%s: cleaning: %s", self.guild.name, self.reminders_json_file)
        self.reminders["reminders"] = [
            reminder
            for reminder in self.reminders["reminders"]
            if not has_datetime_passed(reminder["execution_time"])["result"]
        ]
        self.save_reminders()

    def list_reminders(self) -> str:
        """Return a formatted list of active reminders."""
        self.clean_reminders()
        messages: list[str] = []
        for reminder in self.reminders["reminders"]:
            reminder_date = reminder["execution_time"]
            if isinstance(reminder_date, str):
                reminder_date = datetime.strptime(reminder_date, date_format).replace(tzinfo=UTC)
            reminder_date = reminder_date.replace(tzinfo=UTC)
            display_date = reminder_date.astimezone(pytz.timezone("US/Eastern")).strftime(human_date_format)
            messages.append(
                f"\nCreated by: {reminder['name']}\n"
                f"Reminder date: {display_date}\n"
                f"Reminder message: {reminder['message']}\n"
            )
        if not messages:
            return "No active reminds were found"
        return "".join(messages)

    async def parse_reminders(self) -> None:
        """Schedule all currently persisted reminders."""
        _logger.info("%s: parse_reminders()", self.guild.name)
        _logger.info("%s: %s", self.guild.name, self.reminders)
        for reminder in self.reminders["reminders"]:
            await self.parse_reminder(reminder)

    async def parse_reminder(self, reminder: _Reminder) -> None:
        """Schedule a reminder if it has not already expired."""
        _logger.info("%s: parse_reminder()", self.guild.name)
        passed_result = has_datetime_passed(reminder["execution_time"])
        if not passed_result["result"]:
            self.create_timer(
                reminder["message"],
                reminder["user_id"],
                reminder["name"],
                passed_result["seconds_until_execution"],
                reminder["channel_id"],
            )
        else:
            _logger.info(
                "%s: parse_reminder() - reminder for %s that says %s alredy expired",
                self.guild.name,
                reminder["name"],
                reminder["message"],
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
        message = " ".join(message)
        created_at = datetime.now(UTC)
        self.reminders["reminders"].append(
            {
                "user_id": user_id,
                "name": user.name,
                "message": message,
                "created_at": created_at.strftime(date_format),
                "execution_time": add_time_to_date(created_at, delay_seconds).strftime(date_format),
                "timezone": "utc",
                "guild_id": guild_id,
                "channel_id": channel_id,
            }
        )
        self.save_reminders()
        execution_time = (
            (created_at + timedelta(seconds=delay_seconds))
            .astimezone(pytz.timezone("US/Eastern"))
            .strftime(human_date_format)
        )
        self.create_timer(message, user_id, user.name, delay_seconds, channel_id)
        return f"Created reminder for {user.name} to go off at {execution_time} that says `{message}`"
