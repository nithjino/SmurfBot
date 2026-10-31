import asyncio
import json
from pathlib import Path

import pytest

from smurfbot import storage
from smurfbot.activation import GROUP_FILE, GroupActivation
from smurfbot.core import GroupRef
from smurfbot.storage import JsonStore, StateFileError
from tests.support import read_json, run

GUILD = GroupRef(101, "Guild")


def make(path: Path) -> GroupActivation:
    return GroupActivation(JsonStore(path, GROUP_FILE))


async def loaded(path: Path) -> GroupActivation:
    activation = make(path)
    await activation.load()
    return activation


def test_load_creates_an_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "groups.json"

    run(loaded(path))

    assert read_json(path) == {}


def test_new_groups_default_to_enabled_with_the_legacy_shape(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"

    async def exercise() -> None:
        activation = await loaded(path)
        await activation.observe([GroupRef(101, "One"), GroupRef(202, "DM: Alice")])

    run(exercise())

    assert read_json(path) == {"One": {"enabled": True, "id": "101"}, "DM: Alice": {"enabled": True, "id": "202"}}


def test_renames_and_unseen_groups_keep_their_choice(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"

    async def exercise() -> None:
        activation = await loaded(path)
        await activation.set_enabled(GroupRef(101, "Old"), enabled=False)
        await activation.observe([GroupRef(999, "Invisible")])
        await activation.observe([GroupRef(101, "New")])

    run(exercise())

    assert read_json(path) == {"New": {"enabled": False, "id": "101"}, "Invisible": {"enabled": True, "id": "999"}}


def test_groups_sharing_a_name_stay_distinct(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"

    async def exercise() -> None:
        activation = await loaded(path)
        await activation.set_enabled(GroupRef(101, "Same"), enabled=False)
        await activation.observe([GroupRef(202, "Same")])
        assert read_json(path) == {
            "Same": {"enabled": False, "id": "101"},
            "Same (202)": {"enabled": True, "id": "202"},
        }
        await activation.observe([GroupRef(202, "New")])

    run(exercise())

    assert read_json(path) == {"Same": {"enabled": False, "id": "101"}, "New": {"enabled": True, "id": "202"}}


def test_state_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"

    async def exercise() -> None:
        await (await loaded(path)).set_enabled(GUILD, enabled=False)
        assert await (await loaded(path)).is_enabled(GUILD) is False

    run(exercise())


def test_is_enabled_records_unknown_groups(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"

    async def exercise() -> None:
        assert await (await loaded(path)).is_enabled(GUILD) is True

    run(exercise())

    assert read_json(path) == {"Guild": {"enabled": True, "id": "101"}}


def test_unchanged_groups_do_not_touch_the_disk(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"

    async def exercise() -> None:
        activation = await loaded(path)
        await activation.observe([GUILD])
        before = path.stat()
        await activation.observe([GUILD])
        assert await activation.is_enabled(GUILD) is True
        assert path.stat() == before

    run(exercise())


def test_commands_never_read_the_file_after_startup(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"

    async def exercise() -> None:
        activation = await loaded(path)
        await activation.observe([GUILD])
        path.write_text("{bad json", encoding="utf-8")
        assert await activation.is_enabled(GUILD) is True

    run(exercise())


def test_legacy_values_are_understood(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"
    path.write_text(
        json.dumps({"Guild": {"id": 101}, "Disabled": {"id": "202", "enabled": 0, "extra": "ignored"}}),
        encoding="utf-8",
    )

    async def exercise() -> None:
        activation = await loaded(path)
        assert await activation.is_enabled(GroupRef(101, "Guild")) is True
        assert await activation.is_enabled(GroupRef(202, "Disabled")) is False
        await activation.set_enabled(GroupRef(101, "Guild"), enabled=False)

    run(exercise())

    assert read_json(path) == {"Guild": {"id": "101", "enabled": False}, "Disabled": {"id": "202", "enabled": False}}


def test_concurrent_changes_keep_every_record(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"

    async def exercise() -> None:
        activation = await loaded(path)
        await asyncio.gather(*(activation.set_enabled(GroupRef(i, f"G{i}"), enabled=False) for i in range(20)))
        await asyncio.gather(
            activation.observe([GroupRef(i, f"R{i}") for i in range(20)]),
            activation.observe([GroupRef(99, "Joined")]),
            activation.is_enabled(GroupRef(303, "DM: Alice")),
        )

    run(exercise())

    assert read_json(path) == {
        **{f"R{i}": {"id": str(i), "enabled": False} for i in range(20)},
        "Joined": {"id": "99", "enabled": True},
        "DM: Alice": {"id": "303", "enabled": True},
    }


@pytest.mark.parametrize("source", ["{bad json", "[]", '{"Guild": {"enabled": true}}'])
def test_corrupt_file_refuses_to_load_and_stays_untouched(tmp_path: Path, source: str) -> None:
    path = tmp_path / "groups.json"
    path.write_text(source, encoding="utf-8")

    with pytest.raises(StateFileError):
        run(make(path).load())

    assert path.read_text(encoding="utf-8") == source


def test_failed_save_leaves_memory_matching_the_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "groups.json"

    def fail(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    async def exercise() -> None:
        activation = await loaded(path)
        await activation.observe([GUILD])
        monkeypatch.setattr(storage, "write_atomic", fail)
        with pytest.raises(OSError, match="disk full"):
            await activation.set_enabled(GUILD, enabled=False)
        assert await activation.is_enabled(GUILD) is True

    run(exercise())


def test_failure_to_record_a_group_does_not_block_its_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    async def exercise() -> None:
        activation = await loaded(tmp_path / "groups.json")
        monkeypatch.setattr(storage, "write_atomic", fail)
        assert await activation.is_enabled(GUILD) is True

    run(exercise())
