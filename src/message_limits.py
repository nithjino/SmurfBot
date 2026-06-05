"""Helpers for keeping Discord messages within API limits."""

from __future__ import annotations

from typing import Final

DISCORD_MESSAGE_LIMIT: Final[int] = 2_000
TRUNCATION_SUFFIX: Final[str] = "..."


def truncate_text(text: str, max_length: int = DISCORD_MESSAGE_LIMIT) -> str:
    """Return text shortened to max_length with a truncation suffix when needed."""
    if len(text) <= max_length:
        return text
    if max_length <= len(TRUNCATION_SUFFIX):
        return TRUNCATION_SUFFIX[:max_length]
    return f"{text[: max_length - len(TRUNCATION_SUFFIX)]}{TRUNCATION_SUFFIX}"


def truncate_discord_message(text: str) -> str:
    """Return text shortened to Discord's message limit."""
    return truncate_text(text, DISCORD_MESSAGE_LIMIT)
