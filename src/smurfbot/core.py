"""Types shared by the command handlers."""

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class Request:
    """One incoming message, reduced to what the commands need."""

    text: str
    author_id: int
    guild_id: int | None
    group_id: int
    group_name: str
    channel_id: int
    is_admin: bool
    attachment_url: str | None = None


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)
