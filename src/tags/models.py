"""Pydantic models used by tag command handlers and persistence."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PersistedTagModel(BaseModel):
    """Base configuration for validated tag state."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TagContent(BaseModel):
    """Incoming content for a new tag."""

    message: str
    attachment: str | None


class TagRecord(PersistedTagModel):
    """Persisted tag data."""

    owner: int
    content: str


class TagFile(PersistedTagModel):
    """Persisted guild tag file."""

    name: str
    id: int
    tags: dict[str, TagRecord] = Field(default_factory=dict)
