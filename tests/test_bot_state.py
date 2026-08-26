"""Group activation interface and data-directory configuration tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from bot_state import DATA_DIR_ENV_VAR, DEFAULT_DATA_DIR, GroupActivation, get_state_paths

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def message(
    *, guild_id: int | None = 101, name: str = "Guild", administrator: bool = True, manage_guild: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(
        author=SimpleNamespace(
            guild_permissions=SimpleNamespace(administrator=administrator, manage_guild=manage_guild)
        ),
        guild=SimpleNamespace(id=guild_id, name=name) if guild_id is not None else None,
        channel=SimpleNamespace(id=303, recipient=SimpleNamespace(name="Alice")),
    )


def records(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_synchronize_creates_exact_defaults_and_private_names(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "groups.json"
    activation = GroupActivation(path)
    run(
        activation.synchronize(
            [SimpleNamespace(id=101, name="Guild One"), SimpleNamespace(id=202, name="Guild Two")],
            [
                SimpleNamespace(id=303, recipient=SimpleNamespace(name="Alice")),
                SimpleNamespace(id=404, name="Group Chat"),
                SimpleNamespace(id=505, recipients=[SimpleNamespace(name="Bob"), SimpleNamespace(name="Carol")]),
                SimpleNamespace(id=606),
            ],
        )
    )
    assert records(path) == {
        "Guild One": {"enabled": True, "id": "101"},
        "Guild Two": {"enabled": True, "id": "202"},
        "DM: Alice": {"enabled": True, "id": "303"},
        "DM: Group Chat": {"enabled": True, "id": "404"},
        "DM: Bob, Carol": {"enabled": True, "id": "505"},
        "DM: 606": {"enabled": True, "id": "606"},
    }


def test_synchronize_preserves_renamed_and_invisible_groups(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"
    activation = GroupActivation(path)
    run(activation.set_enabled(message(name="Old Guild"), enabled=False))
    run(activation.set_enabled(message(guild_id=None), enabled=False))
    run(activation.record_join(SimpleNamespace(id=999, name="Invisible")))
    run(
        activation.synchronize(
            [SimpleNamespace(id=101, name="New Guild")],
            [SimpleNamespace(id=303, recipient=SimpleNamespace(name="New Alice"))],
        )
    )
    assert records(path) == {
        "New Guild": {"enabled": False, "id": "101"},
        "DM: New Alice": {"enabled": False, "id": "303"},
        "Invisible": {"enabled": True, "id": "999"},
    }


def test_duplicate_names_stay_distinct_across_rename(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"
    activation = GroupActivation(path)
    run(activation.set_enabled(message(name="Same"), enabled=False))
    run(activation.synchronize([SimpleNamespace(id=202, name="Same")], []))
    assert records(path) == {"Same": {"enabled": False, "id": "101"}, "Same (202)": {"enabled": True, "id": "202"}}
    run(activation.record_join(SimpleNamespace(id=202, name="New")))
    assert records(path) == {"Same": {"enabled": False, "id": "101"}, "New": {"enabled": True, "id": "202"}}


def test_synchronize_empty_creates_file_and_unchanged_operations_do_not_write(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"
    activation = GroupActivation(path)
    run(activation.synchronize([], []))
    assert records(path) == {}
    before = path.stat()
    run(activation.synchronize([], []))
    assert path.stat() == before
    guild = SimpleNamespace(id=101, name="Guild")
    run(activation.record_join(guild))
    before = path.stat()
    run(activation.record_join(guild))
    run(activation.synchronize([guild], []))
    assert run(activation.is_enabled(message())) is True
    assert path.stat() == before


def test_record_join_preserves_disabled_state_across_rename(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"
    activation = GroupActivation(path)
    run(activation.set_enabled(message(), enabled=False))
    run(activation.record_join(SimpleNamespace(id=101, name="Renamed")))
    assert records(path) == {"Renamed": {"enabled": False, "id": "101"}}
    before = path.stat()
    run(activation.record_join(SimpleNamespace(id=101, name="Renamed")))
    assert path.stat() == before


@pytest.mark.parametrize(("guild_id", "name"), [(101, "Guild"), (None, "DM: Alice")])
def test_enabled_check_creates_and_persists_default(tmp_path: Path, guild_id: int | None, name: str) -> None:
    path = tmp_path / "groups.json"
    assert run(GroupActivation(path).is_enabled(message(guild_id=guild_id))) is True
    assert records(path) == {name: {"enabled": True, "id": str(guild_id or 303)}}


@pytest.mark.parametrize(("administrator", "manage_guild"), [(True, False), (False, True), (True, True)])
def test_guild_permissions_allow_activation_changes(tmp_path: Path, *, administrator: bool, manage_guild: bool) -> None:
    path = tmp_path / "groups.json"
    activation = GroupActivation(path)
    context = message(administrator=administrator, manage_guild=manage_guild)
    assert run(activation.set_enabled(context, enabled=False)) == "Guild has been deactived."
    assert records(path) == {"Guild": {"enabled": False, "id": "101"}}
    assert run(activation.is_enabled(context)) is False
    assert run(activation.set_enabled(context, enabled=True)) == "Guild has been activated."
    assert records(path) == {"Guild": {"enabled": True, "id": "101"}}


@pytest.mark.parametrize("has_permissions", [False, True])
def test_guild_permission_rejection_does_not_read_or_write(tmp_path: Path, *, has_permissions: bool) -> None:
    path = tmp_path / "groups.json"
    activation = GroupActivation(path)
    context = message(administrator=False)
    if not has_permissions:
        context.author = object()
    assert (
        run(activation.set_enabled(context, enabled=False)) == "Only server admins can activate or deactive this bot."
    )
    assert not path.exists()
    path.write_text("malformed", encoding="utf-8")
    assert run(activation.set_enabled(context, enabled=True)) == "Only server admins can activate or deactive this bot."
    assert path.read_text(encoding="utf-8") == "malformed"


def test_private_conversations_allow_activation_changes(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"
    activation = GroupActivation(path)
    context = message(guild_id=None, administrator=False)
    assert run(activation.set_enabled(context, enabled=False)) == "DM: Alice has been deactived."
    assert run(activation.is_enabled(context)) is False
    assert records(path) == {"DM: Alice": {"enabled": False, "id": "303"}}


def test_explicit_path_ignores_environment_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "owned" / "groups.json"
    activation = GroupActivation(path)
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path / "elsewhere"))
    run(activation.synchronize([], []))
    run(activation.record_join(SimpleNamespace(id=101, name="Guild")))
    run(activation.set_enabled(message(), enabled=False))
    assert run(activation.is_enabled(message())) is False
    assert records(path) == {"Guild": {"enabled": False, "id": "101"}}
    assert not (tmp_path / "elsewhere").exists()


def test_normalization_keeps_current_json_rules(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"
    path.write_text(
        json.dumps({"Guild": {"id": 101}, "Disabled": {"id": 202, "enabled": 0, "extra": "ignored"}}), encoding="utf-8"
    )
    activation = GroupActivation(path)
    run(activation.synchronize([SimpleNamespace(id=101, name="Guild"), SimpleNamespace(id=202, name="Disabled")], []))
    assert records(path) == {"Guild": {"id": "101", "enabled": True}, "Disabled": {"id": "202", "enabled": False}}


def test_concurrent_transactions_preserve_all_records_and_activation(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"

    async def exercise() -> None:
        activation = GroupActivation(path)
        await asyncio.gather(
            *(
                activation.set_enabled(message(guild_id=index, name=f"Guild {index}"), enabled=False)
                for index in range(20)
            )
        )
        await asyncio.gather(
            activation.synchronize([SimpleNamespace(id=index, name=f"Renamed {index}") for index in range(20)], []),
            activation.record_join(SimpleNamespace(id=99, name="Joined")),
            activation.is_enabled(message(guild_id=None)),
        )

    run(exercise())
    assert records(path) == {
        **{f"Renamed {index}": {"id": str(index), "enabled": False} for index in range(20)},
        "Joined": {"id": "99", "enabled": True},
        "DM: Alice": {"id": "303", "enabled": True},
    }


@pytest.mark.parametrize("operation", ["synchronize", "record_join", "is_enabled", "set_enabled"])
@pytest.mark.parametrize(("source", "error"), [("{bad json", json.JSONDecodeError), ("[]", TypeError)])
def test_invalid_file_errors_propagate_unchanged(
    tmp_path: Path, operation: str, source: str, error: type[Exception]
) -> None:
    path = tmp_path / "groups.json"
    path.write_text(source, encoding="utf-8")
    activation = GroupActivation(path)
    if operation == "synchronize":
        pending = activation.synchronize([], [])
    elif operation == "record_join":
        pending = activation.record_join(SimpleNamespace(id=101, name="Guild"))
    elif operation == "is_enabled":
        pending = activation.is_enabled(message())
    else:
        pending = activation.set_enabled(message(), enabled=False)
    with pytest.raises(error):
        run(pending)
    assert path.read_text(encoding="utf-8") == source


def test_filesystem_failure_propagates(tmp_path: Path) -> None:
    path = tmp_path / "not-a-directory"
    path.write_text("untouched", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run(GroupActivation(path / "groups.json").synchronize([], []))
    assert path.read_text(encoding="utf-8") == "untouched"


def test_cancelled_operation_preserves_file_and_future_operations(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"

    async def exercise() -> None:
        activation = GroupActivation(path)
        await activation.set_enabled(message(), enabled=False)
        before = path.read_bytes()
        task = asyncio.create_task(activation.set_enabled(message(), enabled=True))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert path.read_bytes() == before
        assert await activation.is_enabled(message()) is False
        assert await activation.set_enabled(message(), enabled=True) == "Guild has been activated."

    run(exercise())


def test_get_state_paths_defaults_to_repo_data_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_DIR_ENV_VAR, raising=False)
    assert get_state_paths() == (DEFAULT_DATA_DIR / "tags", DEFAULT_DATA_DIR / "reminders")


def test_get_state_paths_uses_configured_data_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path))
    assert get_state_paths() == (tmp_path / "tags", tmp_path / "reminders")


def test_cancellation_during_synchronization_discards_partial_changes(tmp_path: Path) -> None:
    class CancellingGuild:
        id = 202

        @property
        def name(self) -> str:
            raise asyncio.CancelledError

    async def exercise() -> None:
        path = tmp_path / "groups.json"
        activation = GroupActivation(path)
        await activation.set_enabled(message(), enabled=False)
        before = path.read_bytes()
        guilds = [SimpleNamespace(id=101, name="Renamed"), CancellingGuild()]
        with pytest.raises(asyncio.CancelledError):
            await activation.synchronize(guilds, [])
        assert path.read_bytes() == before
        assert await activation.is_enabled(message()) is False
        await activation.record_join(SimpleNamespace(id=303, name="Joined"))
        assert records(path) == {"Guild": {"enabled": False, "id": "101"}, "Joined": {"enabled": True, "id": "303"}}

    run(exercise())
