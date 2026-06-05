"""Shared Pydantic models used by bot command handlers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CommandParameters(BaseModel):
    """Command context passed to bot command handlers."""

    command: str
    message: list[str]
    attachment: str | None = None
    fetch_user_func: Any = None
    created_at: Any
    author_id: int
    author_name: str
    guild_id: int | None
    channel_id: int
