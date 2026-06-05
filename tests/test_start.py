"""Tests for top-level command helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

from models import CommandParameters
from start import build_command_parameters, build_discord_intents, client, mock

if TYPE_CHECKING:
    from collections.abc import Coroutine


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def test_mock_accepts_raw_strings_and_command_parameters() -> None:
    raw_result = run(mock("  Ship IT  "))
    parameter_result = run(
        mock(
            CommandParameters(
                command="mock",
                message=["Ship", "IT"],
                created_at=datetime.now(UTC),
                author_id=123,
                guild_id=456,
                channel_id=789,
            )
        )
    )

    assert raw_result == "ShIp It"
    assert parameter_result == "ShIp It"


def test_build_command_parameters_preserves_discord_context_and_attachment() -> None:
    attachment = SimpleNamespace(url="https://example.test/image.png")
    message = SimpleNamespace(
        created_at=datetime(2026, 6, 4, tzinfo=UTC),
        author=SimpleNamespace(id=123),
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789),
        attachments=[attachment],
    )

    parameters = build_command_parameters(message, "tag", ["create", "name", "content"])

    assert parameters.command == "tag"
    assert parameters.message == ["create", "name", "content"]
    assert parameters.created_at == datetime(2026, 6, 4, tzinfo=UTC)
    assert parameters.author_id == 123
    assert parameters.guild_id == 456
    assert parameters.channel_id == 789
    assert parameters.attachment == "https://example.test/image.png"
    assert parameters.fetch_user_func is None


def test_build_command_parameters_sets_fetch_user_for_reminders_and_tag_owner() -> None:
    message = SimpleNamespace(
        created_at=datetime(2026, 6, 4, tzinfo=UTC),
        author=SimpleNamespace(id=123),
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789),
        attachments=[],
    )

    reminder_parameters = build_command_parameters(message, "remind", ["5m", "tea"])
    owner_parameters = build_command_parameters(message, "tag", ["owner", "tea"])

    assert reminder_parameters.fetch_user_func.__self__ is client
    assert reminder_parameters.fetch_user_func.__func__ is client.fetch_user.__func__
    assert owner_parameters.fetch_user_func.__self__ is client
    assert owner_parameters.fetch_user_func.__func__ is client.fetch_user.__func__


def test_build_discord_intents_uses_only_required_privileges() -> None:
    intents = build_discord_intents()

    assert intents.guilds is True
    assert intents.guild_messages is True
    assert intents.dm_messages is True
    assert intents.message_content is True
    assert intents.members is False
    assert intents.presences is False
