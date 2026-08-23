"""Tests for bot activation persistence helpers."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

from bot_state import (
    DATA_DIR_ENV_VAR,
    DEFAULT_DATA_DIR,
    get_state_paths,
    is_message_group_enabled,
    set_message_group_enabled,
    sync_group_records,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path

    import pytest


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def test_sync_group_records_creates_groups_file_with_enabled_defaults(tmp_path: Path) -> None:
    groups_path = tmp_path / "groups.json"
    guilds = [SimpleNamespace(id=101, name="Guild One"), SimpleNamespace(id=202, name="Guild Two")]
    private_channels = [
        SimpleNamespace(id=303, recipient=SimpleNamespace(name="Alice")),
        SimpleNamespace(id=404, name="Group Chat"),
    ]

    run(sync_group_records(guilds, private_channels, groups_path))

    assert json.loads(groups_path.read_text(encoding="utf-8")) == {
        "DM: Alice": {"enabled": True, "id": "303"},
        "DM: Group Chat": {"enabled": True, "id": "404"},
        "Guild One": {"enabled": True, "id": "101"},
        "Guild Two": {"enabled": True, "id": "202"},
    }


def test_sync_group_records_preserves_enabled_values_and_updates_renamed_groups(tmp_path: Path) -> None:
    groups_path = tmp_path / "groups.json"
    groups_path.write_text(
        json.dumps(
            {
                "Old Guild Name": {"enabled": False, "id": "101"},
                "DM: Old Alice": {"enabled": False, "id": "303"},
                "Manual Server": {"enabled": True, "id": "999"},
            }
        ),
        encoding="utf-8",
    )

    run(
        sync_group_records(
            [SimpleNamespace(id=101, name="New Guild Name")],
            [SimpleNamespace(id=303, recipient=SimpleNamespace(name="Alice"))],
            groups_path,
        )
    )

    assert json.loads(groups_path.read_text(encoding="utf-8")) == {
        "DM: Alice": {"enabled": False, "id": "303"},
        "Manual Server": {"enabled": True, "id": "999"},
        "New Guild Name": {"enabled": False, "id": "101"},
    }


def test_get_state_paths_defaults_to_repo_data_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV_VAR, raising=False)

    assert get_state_paths() == (DEFAULT_DATA_DIR / "tags", DEFAULT_DATA_DIR / "reminders")


def test_get_state_paths_uses_configured_data_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))

    assert get_state_paths() == (tmp_path / "tags", tmp_path / "reminders")


def test_group_helpers_accept_an_explicit_groups_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit_path = tmp_path / "owned" / "groups.json"
    environment_path = tmp_path / "environment"
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(environment_path))
    message = SimpleNamespace(
        author=SimpleNamespace(guild_permissions=SimpleNamespace(administrator=True)),
        guild=SimpleNamespace(id=101, name="Guild"),
        channel=SimpleNamespace(id=202),
    )

    assert run(is_message_group_enabled(message, explicit_path)) is True
    assert run(set_message_group_enabled(message, enabled=False, groups_path=explicit_path)) == (
        "Guild has been deactived."
    )
    assert run(is_message_group_enabled(message, explicit_path)) is False
    assert explicit_path.exists()
    assert not (environment_path / "groups.json").exists()


def test_group_helpers_default_to_the_environment_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))
    message = SimpleNamespace(
        author=SimpleNamespace(guild_permissions=SimpleNamespace(administrator=True)),
        guild=SimpleNamespace(id=303, name="Environment Guild"),
        channel=SimpleNamespace(id=404),
    )

    assert run(set_message_group_enabled(message, enabled=False)) == "Environment Guild has been deactived."
    assert run(is_message_group_enabled(message)) is False
    assert json.loads((tmp_path / "groups.json").read_text(encoding="utf-8")) == {
        "Environment Guild": {"enabled": False, "id": "303"}
    }
