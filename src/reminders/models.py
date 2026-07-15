"""Pydantic models used by reminder command handlers and persistence."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class DatetimePassedResult:
    """Result of comparing a planned execution time with now."""

    result: bool
    seconds_until_execution: float


class PersistedReminderModel(BaseModel):
    """Base configuration for validated reminder state."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ReminderRecord(PersistedReminderModel):
    """Persisted reminder data."""

    user_id: int
    name: str
    message: str
    created_at: str
    execution_time: str
    timezone: str
    guild_id: int
    channel_id: int


class ReminderFile(PersistedReminderModel):
    """Persisted guild reminder file."""

    name: str
    id: int
    reminders: list[ReminderRecord] = Field(default_factory=list)
