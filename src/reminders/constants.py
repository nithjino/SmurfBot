"""Reminder command constants."""

from __future__ import annotations

from typing import Final

DATE_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S"
DURATION_UNIT_SECONDS: Final[dict[str, int]] = {"s": 1, "m": 60, "h": 60 * 60, "d": 60 * 60 * 24}
HUMAN_DATE_FORMAT: Final[str] = "%m/%d/%Y @ %I:%M%p"
REMINDER_DELIVERY_ATTEMPTS: Final[int] = 3
REMINDER_DELIVERY_RETRY_DELAY_SECONDS: Final[int] = 60
MAX_REMINDER_SECONDS: Final[int] = 31_557_600
MAX_REMINDERS_PER_GUILD: Final[int] = 100
MIN_REMIND_ARGUMENTS: Final[int] = 2
