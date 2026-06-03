"""Pydantic models used by tag command handlers and persistence."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
