"""The persisted tag file format."""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class TagRecord(BaseModel):
    """One tag's owner and content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner: int
    content: str


class TagFile(BaseModel):
    """All of a guild's tags, keyed by name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    id: int
    tags: dict[str, TagRecord] = Field(default_factory=dict)


TAG_FILE: Final = TypeAdapter(TagFile)
