"""Pydantic models used by bot command handlers and persistence."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from pydantic import BaseModel, Field


class CommandParameters(BaseModel):
    """Command context passed to bot command handlers."""

    command: str
    message: list[str]
    attachment: str | None = None
    fetch_user_func: Any = None
    created_at: Any
    author_id: int
    guild_id: int | None
    channel_id: int


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


class TagContent(BaseModel):
    """Incoming content for a new tag."""

    message: str
    attachment: str | None


class TagRecord(BaseModel):
    """Persisted tag data."""

    owner: int
    content: str


class TagFile(BaseModel):
    """Persisted guild tag file."""

    name: str
    id: int
    tags: dict[str, TagRecord] = Field(default_factory=dict)


class TagCommandParameters(BaseModel):
    """Command context needed by tag operations."""

    message: list[str]
    attachment: str | None = None
    author_id: int
    fetch_user_func: Any = None
