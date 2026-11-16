from datetime import UTC, datetime
from typing import Any

from smurfbot.core import Request

NOW = datetime(2026, 6, 3, 14, 16, 47, tzinfo=UTC)
GUILD = 100
CHANNEL = 200
DM_CHANNEL = 300
AUTHOR = 1


def make_request(text: str = "", **overrides: Any) -> Request:
    fields: dict[str, Any] = {
        "text": text,
        "author_id": AUTHOR,
        "guild_id": GUILD,
        "group_id": GUILD,
        "group_name": "Guild",
        "channel_id": CHANNEL,
        "is_admin": False,
    }
    return Request(**(fields | overrides))


def make_dm(text: str = "", **overrides: Any) -> Request:
    fields: dict[str, Any] = {
        "guild_id": None,
        "group_id": DM_CHANNEL,
        "group_name": "This DM",
        "channel_id": DM_CHANNEL,
        "is_admin": True,
    }
    return make_request(text, **(fields | overrides))
