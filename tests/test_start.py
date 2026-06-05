"""Tests for top-level command helpers."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import start
from models import CommandParameters
from start import (
    DATA_DIR_ENV_VAR,
    DEFAULT_DATA_DIR,
    GENERIC_COMMAND_ERROR,
    build_command_parameters,
    build_discord_intents,
    client,
    get_state_paths,
    initialize_guild_handlers,
    mock,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path

    import pytest


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
                author_name="Alice",
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
        author=SimpleNamespace(id=123, name="Alice"),
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789),
        attachments=[attachment],
    )

    parameters = build_command_parameters(message, "tag", ["create", "name", "content"])

    assert parameters.command == "tag"
    assert parameters.message == ["create", "name", "content"]
    assert parameters.created_at == datetime(2026, 6, 4, tzinfo=UTC)
    assert parameters.author_id == 123
    assert parameters.author_name == "Alice"
    assert parameters.guild_id == 456
    assert parameters.channel_id == 789
    assert parameters.attachment == "https://example.test/image.png"
    assert parameters.fetch_user_func is None


def test_build_command_parameters_sets_fetch_user_only_for_tag_owner() -> None:
    message = SimpleNamespace(
        created_at=datetime(2026, 6, 4, tzinfo=UTC),
        author=SimpleNamespace(id=123, name="Alice"),
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789),
        attachments=[],
    )

    reminder_parameters = build_command_parameters(message, "remind", ["5m", "tea"])
    owner_parameters = build_command_parameters(message, "tag", ["owner", "tea"])

    assert reminder_parameters.fetch_user_func is None
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


def test_get_state_paths_defaults_to_repo_data_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV_VAR, raising=False)

    assert get_state_paths() == (DEFAULT_DATA_DIR / "tags", DEFAULT_DATA_DIR / "reminders")


def test_get_state_paths_uses_configured_data_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))

    assert get_state_paths() == (tmp_path / "tags", tmp_path / "reminders")


def test_initialize_guild_handlers_skips_bad_tag_file_without_replacing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    tag_path = tmp_path / "tags"
    reminders_path = tmp_path / "reminders"
    tag_path.mkdir()
    original_tag_json = "{bad json"
    (tag_path / "101.json").write_text(original_tag_json, encoding="utf-8")

    class RecordingReminders:
        @classmethod
        async def create(cls, guild: object, reminders_json_path: Path, discord_client: object) -> SimpleNamespace:
            return SimpleNamespace(guild=guild, reminders_json_path=reminders_json_path, discord_client=discord_client)

    monkeypatch.setattr(start, "Reminders", RecordingReminders)
    start.tags.clear()
    start.reminders.clear()

    with caplog.at_level(logging.ERROR):
        run(initialize_guild_handlers(SimpleNamespace(id=101, name="Guild"), tag_path, reminders_path))

    assert 101 not in start.tags
    assert 101 in start.reminders
    assert (tag_path / "101.json").read_text(encoding="utf-8") == original_tag_json
    assert "Skipping tag initialization for guild Guild (101)" in caplog.text


def test_initialize_guild_handlers_skips_bad_reminder_file_without_replacing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    tag_path = tmp_path / "tags"
    reminders_path = tmp_path / "reminders"
    reminders_path.mkdir()
    original_reminders_json = "{bad json"
    (reminders_path / "202.json").write_text(original_reminders_json, encoding="utf-8")

    class RecordingTags:
        @classmethod
        async def create(cls, guild: object, tags_json_path: Path) -> SimpleNamespace:
            return SimpleNamespace(guild=guild, tags_json_path=tags_json_path)

    monkeypatch.setattr(start, "Tags", RecordingTags)
    start.tags.clear()
    start.reminders.clear()

    with caplog.at_level(logging.ERROR):
        run(initialize_guild_handlers(SimpleNamespace(id=202, name="Guild"), tag_path, reminders_path))

    assert 202 in start.tags
    assert 202 not in start.reminders
    assert (reminders_path / "202.json").read_text(encoding="utf-8") == original_reminders_json
    assert "Skipping reminder initialization for guild Guild (202)" in caplog.text


def test_on_guild_join_initializes_joined_guild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, Path, Path]] = []
    configured_data_dir = tmp_path / "state"

    async def record_initialize_guild_handlers(guild: object, tag_json_path: Path, reminders_json_path: Path) -> None:
        calls.append((guild, tag_json_path, reminders_json_path))

    guild = SimpleNamespace(id=303, name="Joined Guild")
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(configured_data_dir))
    monkeypatch.setattr(start, "initialize_guild_handlers", record_initialize_guild_handlers)

    run(start.on_guild_join(guild))

    assert calls == [(guild, configured_data_dir / "tags", configured_data_dir / "reminders")]


def test_on_message_sends_generic_response_when_command_handler_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class RecordingChannel:
        id = 789

        def __init__(self) -> None:
            self.sent_messages: list[str] = []

        async def send(self, content: str, **_kwargs: object) -> None:
            self.sent_messages.append(content)

    async def failing_command(_parameters: CommandParameters) -> str:
        msg = "handler failed"
        raise RuntimeError(msg)

    channel = RecordingChannel()
    message = SimpleNamespace(
        content="$explode now",
        author=SimpleNamespace(id=123, name="Alice"),
        guild=SimpleNamespace(id=456),
        channel=channel,
        created_at=datetime(2026, 6, 4, tzinfo=UTC),
        attachments=[],
    )
    monkeypatch.setitem(start.valid_commands, "explode", failing_command)

    with caplog.at_level(logging.ERROR):
        run(start.on_message(message))

    assert channel.sent_messages == [GENERIC_COMMAND_ERROR]
    assert "Failed to handle command explode in guild 456 channel 789 author 123" in caplog.text


def test_on_message_logs_when_generic_error_response_cannot_be_sent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class FailingChannel:
        id = 789

        async def send(self, _content: str, **_kwargs: object) -> None:
            msg = "send failed"
            raise RuntimeError(msg)

    async def failing_command(_parameters: CommandParameters) -> str:
        msg = "handler failed"
        raise RuntimeError(msg)

    message = SimpleNamespace(
        content="$explode now",
        author=SimpleNamespace(id=123, name="Alice"),
        guild=SimpleNamespace(id=456),
        channel=FailingChannel(),
        created_at=datetime(2026, 6, 4, tzinfo=UTC),
        attachments=[],
    )
    monkeypatch.setitem(start.valid_commands, "explode", failing_command)

    with caplog.at_level(logging.ERROR):
        run(start.on_message(message))

    assert "Failed to handle command explode in guild 456 channel 789 author 123" in caplog.text
    assert "Failed to send response for command explode in guild 456 channel 789 author 123" in caplog.text
