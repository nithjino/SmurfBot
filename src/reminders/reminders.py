"""Reminder command parsing, persistence, and timer scheduling."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from atomicwrites import atomic_write

from discord_messages import send_reminder_response
from message_limits import DISCORD_MESSAGE_LIMIT, truncate_discord_message, truncate_text

from .constants import (
    DATE_FORMAT,
    DURATION_UNIT_SECONDS,
    HUMAN_DATE_FORMAT,
    MAX_REMINDER_SECONDS,
    MAX_REMINDERS_PER_GUILD,
    MIN_REMIND_ARGUMENTS,
    REMINDER_DELIVERY_ATTEMPTS,
    REMINDER_DELIVERY_RETRY_DELAY_SECONDS,
)
from .models import DatetimePassedResult, ReminderFile, ReminderRecord

if TYPE_CHECKING:
    from models import CommandParameters

_logger = logging.getLogger(__name__)
DISPLAY_TIMEZONE = ZoneInfo("America/New_York")

Now = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]
ReminderSender = Callable[[discord.abc.Messageable, int, str], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _ReminderDependencies:
    """Private clock, delay, and delivery adapters for reminder tests."""

    now: Now = _utc_now
    sleep: Sleep = asyncio.sleep
    send_reminder: ReminderSender = send_reminder_response


def _parse_time(time: str) -> int | None:
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


def _add_time_to_date(date: datetime | str, seconds: int) -> datetime:
    """Add seconds to a UTC datetime or serialized datetime string."""
    if isinstance(date, str):
        date = datetime.strptime(date, DATE_FORMAT).replace(tzinfo=UTC)
    return date + timedelta(seconds=seconds)


def _has_datetime_passed(planned_execution_date: datetime | str, current_datetime: datetime) -> DatetimePassedResult:
    """Report whether a planned UTC execution datetime has passed."""
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


def _parse_reminder_delay(seconds: str) -> tuple[int | None, str | None]:
    """Return a valid reminder delay or the user-facing validation error."""
    delay_seconds = _parse_time(seconds)
    if delay_seconds is None:
        return None, "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"
    if delay_seconds > MAX_REMINDER_SECONDS:
        return None, "Why are you using this feature for a reminder that far in the future?"
    if delay_seconds <= 0:
        return None, "Why are you trying to set a reminder for the past?"
    return delay_seconds, None


class Reminders:
    """Manage persisted reminders and scheduled reminder messages for a guild."""

    def __init__(
        self,
        guild: discord.Guild,
        reminders_json_path: str | Path,
        *,
        _dependencies: _ReminderDependencies | None = None,
    ) -> None:
        """Initialize paths and default reminder state for a guild."""
        self._dependencies = replace(_dependencies) if _dependencies is not None else _ReminderDependencies()
        self._guild = guild
        self._reminders_json_path = Path(reminders_json_path)
        self._reminders_json_file = self._reminders_json_path / f"{guild.id}.json"
        self._reminders = ReminderFile(name=guild.name, id=guild.id)
        self._timer_tasks: set[asyncio.Task[None]] = set()
        self._reminder_lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        guild: discord.Guild,
        reminders_json_path: str | Path,
        *,
        _dependencies: _ReminderDependencies | None = None,
    ) -> Reminders:
        """Create a reminder handler and schedule any pending reminders."""
        reminder_handler = cls(guild, reminders_json_path, _dependencies=_dependencies)
        reminder_handler._reminders = await reminder_handler._load_reminders()
        await reminder_handler._clean_reminders()
        await reminder_handler._parse_reminders()
        return reminder_handler

    async def _create_json(self) -> None:
        """Create the reminders directory and guild JSON file when missing."""

        def create_json_sync() -> None:
            self._reminders_json_path.mkdir(parents=True, exist_ok=True)
            initial_state = ReminderFile(name=self._guild.name, id=self._guild.id)
            try:
                with self._reminders_json_file.open("x", encoding="utf-8") as reminders_file:
                    json.dump(initial_state.model_dump(mode="json"), reminders_file)
            except FileExistsError:
                return
            _logger.info("%s: created: %s", self._guild.name, self._reminders_json_file)

        await asyncio.to_thread(create_json_sync)

    async def _load_reminders(self) -> ReminderFile:
        """Return the guild's reminders JSON data."""
        await self._create_json()
        _logger.info("%s: loading: %s", self._guild.name, self._reminders_json_file)
        with self._reminders_json_file.open(encoding="utf-8") as reminders:
            return ReminderFile.model_validate(json.load(reminders))

    async def _save_reminders_unlocked(self) -> None:
        """Write reminders to disk while the caller holds the reminder lock."""
        _logger.info("%s: saving: %s", self._guild.name, self._reminders_json_file)
        with atomic_write(self._reminders_json_file, overwrite=True, encoding="utf-8") as reminders_file:
            reminders_file.write(json.dumps(self._reminders.model_dump(mode="json"), sort_keys=True, indent=2))

    async def _clean_reminders(self) -> None:
        """Remove expired reminders and persist the cleaned reminder list."""
        _logger.info("%s: cleaning: %s", self._guild.name, self._reminders_json_file)
        async with self._reminder_lock:
            active_reminders = [
                reminder
                for reminder in self._reminders.reminders
                if not _has_datetime_passed(reminder.execution_time, self._dependencies.now()).result
            ]
            if len(active_reminders) != len(self._reminders.reminders):
                self._reminders.reminders = active_reminders
                await self._save_reminders_unlocked()

    async def _list_reminders(self) -> str:
        """Return a formatted list of active reminders."""
        await self._clean_reminders()
        async with self._reminder_lock:
            reminders = list(self._reminders.reminders)
        messages: list[str] = []
        for reminder in reminders:
            reminder_date = datetime.strptime(reminder.execution_time, DATE_FORMAT).replace(tzinfo=UTC)
            display_date = reminder_date.astimezone(DISPLAY_TIMEZONE).strftime(HUMAN_DATE_FORMAT)
            messages.append(
                f"\nCreated by: {reminder.name}\nReminder date: {display_date}\nReminder message: {reminder.message}\n"
            )
        if not messages:
            return "No active reminds were found"
        return truncate_discord_message("".join(messages))

    async def _parse_reminders(self) -> None:
        """Schedule all currently persisted reminders."""
        _logger.info("%s: parse_reminders()", self._guild.name)
        _logger.info("%s: %s", self._guild.name, self._reminders)
        async with self._reminder_lock:
            reminders = list(self._reminders.reminders)
        for reminder in reminders:
            await self._parse_reminder(reminder)

    async def _parse_reminder(self, reminder: ReminderRecord) -> None:
        """Schedule a reminder if it has not already expired."""
        _logger.info("%s: parse_reminder()", self._guild.name)
        passed_result = _has_datetime_passed(reminder.execution_time, self._dependencies.now())
        if not passed_result.result:
            self._create_timer(reminder, passed_result.seconds_until_execution)
        else:
            _logger.info(
                "%s: parse_reminder() - reminder for %s that says %s alredy expired",
                self._guild.name,
                reminder.name,
                reminder.message,
            )

    def _create_timer(
        self,
        reminder: ReminderRecord,
        delay_seconds: float,
    ) -> None:
        """Create an async timer that sends a reminder message later."""
        task = asyncio.create_task(self._send_message(reminder, delay_seconds))
        self._timer_tasks.add(task)
        task.add_done_callback(self._timer_finished)
        _logger.info(
            "%s: created timer for %s that will execute in %s seconds",
            self._guild.name,
            reminder.name,
            delay_seconds,
        )

    def _timer_finished(self, task: asyncio.Task[None]) -> None:
        """Remove a finished timer task and report unexpected failures."""
        self._timer_tasks.discard(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            _logger.error("%s: reminder timer failed", self._guild.name, exc_info=exception)

    async def close(self) -> None:
        """Cancel and await all scheduled timers owned by this guild handler."""
        tasks = tuple(self._timer_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _remove_reminder(self, reminder: ReminderRecord) -> None:
        """Remove a completed reminder from memory and disk."""
        async with self._reminder_lock:
            try:
                self._reminders.reminders.remove(reminder)
            except ValueError:
                _logger.info("%s: reminder was already removed: %s", self._guild.name, reminder)
                return
            await self._save_reminders_unlocked()

    async def _send_message(self, reminder: ReminderRecord, seconds: float) -> None:
        """Wait for the requested delay, then send the reminder to a channel."""
        await self._dependencies.sleep(seconds)
        for attempt in range(1, REMINDER_DELIVERY_ATTEMPTS + 1):
            if await self._try_send_reminder(reminder, attempt):
                await self._remove_reminder(reminder)
                return
            if attempt < REMINDER_DELIVERY_ATTEMPTS:
                await self._dependencies.sleep(REMINDER_DELIVERY_RETRY_DELAY_SECONDS)

        _logger.error(
            "%s: failed to send reminder after %s attempts: %s",
            self._guild.name,
            REMINDER_DELIVERY_ATTEMPTS,
            reminder,
        )
        await self._remove_reminder(reminder)

    async def _try_send_reminder(self, reminder: ReminderRecord, attempt: int) -> bool:
        """Try to send a reminder once."""
        channel = self._guild.get_channel(reminder.channel_id)
        if channel is None:
            _logger.warning(
                "%s: channel %s was not found for reminder send attempt %s",
                self._guild.name,
                reminder.channel_id,
                attempt,
            )
            return False
        if not isinstance(channel, discord.abc.Messageable):
            _logger.warning(
                "%s: channel %s cannot receive reminder messages on attempt %s",
                self._guild.name,
                reminder.channel_id,
                attempt,
            )
            return False
        try:
            await self._dependencies.send_reminder(channel, reminder.user_id, reminder.message)
        except discord.HTTPException:
            _logger.exception(
                "%s: failed to send reminder to channel %s on attempt %s",
                self._guild.name,
                reminder.channel_id,
                attempt,
            )
            return False
        return True

    async def handle(self, parameters: CommandParameters) -> str:
        """Interpret a command and own validation, persistence, and scheduling."""
        command_message = parameters.message
        seconds = command_message[0] if command_message else "help"
        message = command_message[1:]
        if len(command_message) < MIN_REMIND_ARGUMENTS:
            seconds = "help"
        match seconds:
            case "help":
                return (
                    "Format: $remind [amount of time] [message]. Example: $remind 1h check laundry\nSupport units: "
                    "s (seconds), m (minutes), h (hours), or d (days)."
                )
            case "list":
                return await self._list_reminders()
            case _:
                delay_seconds, error_message = _parse_reminder_delay(seconds)
        if delay_seconds is None:
            if error_message is None:
                msg = "reminder delay validation failed without an error message"
                raise ValueError(msg)
            return error_message
        reminder_message = truncate_text(" ".join(message), DISCORD_MESSAGE_LIMIT)
        created_at = self._dependencies.now()
        reminder_user_name = parameters.author_name or str(parameters.author_id)
        reminder = ReminderRecord(
            user_id=parameters.author_id,
            name=reminder_user_name,
            message=reminder_message,
            created_at=created_at.strftime(DATE_FORMAT),
            execution_time=_add_time_to_date(created_at, delay_seconds).strftime(DATE_FORMAT),
            timezone="utc",
            guild_id=parameters.guild_id if parameters.guild_id is not None else self._guild.id,
            channel_id=parameters.channel_id,
        )
        async with self._reminder_lock:
            if len(self._reminders.reminders) >= MAX_REMINDERS_PER_GUILD:
                return (
                    "Reminder limit reached. Delete or wait for a reminder to complete before creating a new one. "
                    f"Limit: {MAX_REMINDERS_PER_GUILD}"
                )
            self._reminders.reminders.append(reminder)
            await self._save_reminders_unlocked()
        execution_time = (
            (created_at + timedelta(seconds=delay_seconds)).astimezone(DISPLAY_TIMEZONE).strftime(HUMAN_DATE_FORMAT)
        )
        self._create_timer(reminder, delay_seconds)
        return truncate_discord_message(
            f"Created reminder for {reminder_user_name} to go off at {execution_time} that says `{reminder_message}`"
        )
