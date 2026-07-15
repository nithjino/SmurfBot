"""Shared Pydantic models used by bot command handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime  # noqa: TC003

import discord
from pydantic import BaseModel, ConfigDict

FetchUser = Callable[[int], Awaitable[discord.User]]


class CommandParameters(BaseModel):
    """Command context passed to bot command handlers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str
    message: list[str]
    attachment: str | None = None
    fetch_user_func: FetchUser | None = None
    created_at: datetime
    author_id: int
    author_name: str
    guild_id: int | None
    channel_id: int
