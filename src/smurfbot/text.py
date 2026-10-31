"""Helpers for keeping text within Discord's message limit."""

from typing import Final

DISCORD_MESSAGE_LIMIT: Final = 2_000
TRUNCATION_SUFFIX: Final = "..."


def truncate(text: str, max_length: int = DISCORD_MESSAGE_LIMIT) -> str:
    """Return text shortened to max_length, marking any cut with a suffix."""
    if len(text) <= max_length:
        return text
    if max_length <= len(TRUNCATION_SUFFIX):
        return TRUNCATION_SUFFIX[:max_length]
    return f"{text[: max_length - len(TRUNCATION_SUFFIX)]}{TRUNCATION_SUFFIX}"
