"""Shared application constants."""

from __future__ import annotations

from pathlib import Path
from typing import Final

BOT_PATH: Final[Path] = Path(__file__).resolve().parent
DATE_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S"
DELIM: Final[str] = "$"
DURATION_UNIT_SECONDS: Final[dict[str, int]] = {"s": 1, "m": 60, "h": 60 * 60, "d": 60 * 60 * 24}
HUMAN_DATE_FORMAT: Final[str] = "%m/%d/%Y @ %I:%M%p"
MAX_REMINDER_SECONDS: Final[int] = 31_557_600
MIN_REMIND_ARGUMENTS: Final[int] = 2
