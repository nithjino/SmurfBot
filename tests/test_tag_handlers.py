"""Tests for tag subcommand handler helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from models import CommandParameters
from tags.handlers import handle_gift, parse_discord_user_id

if TYPE_CHECKING:
    from collections.abc import Coroutine


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def test_parse_discord_user_id_accepts_raw_ids_and_mentions() -> None:
    assert parse_discord_user_id("12345") == 12345
    assert parse_discord_user_id("<@12345>") == 12345
    assert parse_discord_user_id("<@!12345>") == 12345


def test_parse_discord_user_id_rejects_non_numeric_values() -> None:
    assert parse_discord_user_id("alice") is None
    assert parse_discord_user_id("<@alice>") is None
    assert parse_discord_user_id("") is None


def test_handle_gift_validates_new_owner_before_changing_state() -> None:
    class FakeTags:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, int]] = []

        async def gift_tag(self, name: str, owner: int, new_owner: int) -> str:
            self.calls.append((name, owner, new_owner))
            return "gifted"

    tags = FakeTags()
    parameters = CommandParameters(
        command="tag",
        message=["gift", "launch"],
        created_at=datetime.now(UTC),
        author_id=7,
        author_name="Alice",
        guild_id=1,
        channel_id=2,
    )

    invalid_result = run(handle_gift(tags, parameters, ["launch", "not-a-user"]))
    valid_result = run(handle_gift(tags, parameters, ["launch", "<@!42>"]))

    assert invalid_result == "The new owner must be a Discord user mention or user id"
    assert valid_result == "gifted"
    assert tags.calls == [("launch", 7, 42)]
