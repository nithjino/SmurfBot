"""Pydantic models used by reminder command handlers and persistence."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from pydantic import BaseModel, Field


class DatetimePassedResult(BaseModel):
    """Result of comparing a planned execution time with now."""

    result: bool
    seconds_until_execution: float


class ReminderRecord(BaseModel):
    """Persisted reminder data."""

    user_id: int
    name: str
    message: str
    created_at: str
    execution_time: str | datetime
    timezone: str
    guild_id: int | None
    channel_id: int


class ReminderFile(BaseModel):
    """Persisted guild reminder file."""

    name: str
    id: int
    reminders: list[ReminderRecord] = Field(default_factory=list)
