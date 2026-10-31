"""The persisted reminder file format."""

from datetime import UTC, datetime
from typing import Annotated, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer, TypeAdapter

TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%S"


def _parse_timestamp(value: str | datetime) -> datetime:
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC) if isinstance(value, str) else value


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


type UtcTimestamp = Annotated[
    datetime,
    BeforeValidator(_parse_timestamp),
    PlainSerializer(_format_timestamp, return_type=str),
]


class Reminder(BaseModel):
    """One pending reminder."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: int
    name: str
    message: str
    created_at: UtcTimestamp
    execution_time: UtcTimestamp
    timezone: str = "utc"
    guild_id: int
    channel_id: int


class ReminderFile(BaseModel):
    """All of a guild's pending reminders."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    id: int
    reminders: tuple[Reminder, ...] = ()


REMINDER_FILE: Final = TypeAdapter(ReminderFile)
