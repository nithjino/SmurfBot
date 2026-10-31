import asyncio
import threading
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter

from smurfbot import storage
from smurfbot.storage import DataDirLockedError, JsonStore, StateFileError, exclusive_data_dir, write_atomic
from tests.support import read_json, run


class Sample(BaseModel):
    value: int


ADAPTER = TypeAdapter(Sample)


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    assert run(JsonStore(tmp_path / "a.json", ADAPTER).load()) is None


def test_save_creates_parents_and_round_trips(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "nested" / "a.json", ADAPTER)

    run(store.save(Sample(value=1)))

    assert run(store.load()) == Sample(value=1)


def test_files_are_sorted_indented_json(tmp_path: Path) -> None:
    path = tmp_path / "a.json"

    run(JsonStore(path, TypeAdapter(dict[str, int])).save({"b": 1, "a": 2}))

    assert path.read_text(encoding="utf-8") == '{\n  "a": 2,\n  "b": 1\n}'


def test_saved_files_are_private_to_the_bot_user(tmp_path: Path) -> None:
    path = tmp_path / "a.json"

    write_atomic(path, "{}")

    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("source", ["{bad json", '{"value": "not a number"}', "[]", "\xff"])
def test_unreadable_files_raise_and_stay_untouched(tmp_path: Path, source: str) -> None:
    path = tmp_path / "a.json"
    path.write_bytes(source.encode("latin-1"))

    with pytest.raises(StateFileError, match=r"a\.json"):
        run(JsonStore(path, ADAPTER).load())

    assert path.read_bytes() == source.encode("latin-1")


def test_failed_write_keeps_the_old_file_and_leaves_no_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "a.json"
    write_atomic(path, "old")

    def fail(_descriptor: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(storage.os, "fsync", fail)

    with pytest.raises(OSError, match="disk full"):
        write_atomic(path, "new")

    assert path.read_text(encoding="utf-8") == "old"
    assert [entry.name for entry in tmp_path.iterdir()] == ["a.json"]


def test_writes_do_not_run_on_the_event_loop_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    threads: list[int] = []
    original = storage.write_atomic

    def record(path: Path, text: str) -> None:
        threads.append(threading.get_ident())
        original(path, text)

    monkeypatch.setattr(storage, "write_atomic", record)

    async def exercise() -> int:
        await JsonStore(tmp_path / "a.json", ADAPTER).save(Sample(value=1))
        return threading.get_ident()

    assert run(exercise()) not in threads


def test_concurrent_saves_never_expose_partial_files(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    store = JsonStore(path, ADAPTER)

    async def exercise() -> None:
        await store.save(Sample(value=0))
        for value in range(1, 25):
            write = asyncio.create_task(store.save(Sample(value=value)))
            assert read_json(path).keys() == {"value"}
            await write

    run(exercise())


def test_data_dir_can_only_be_held_once(tmp_path: Path) -> None:
    with (
        exclusive_data_dir(tmp_path),
        pytest.raises(DataDirLockedError, match="already using"),
        exclusive_data_dir(tmp_path),
    ):
        pytest.fail("the second holder should not get in")


def test_data_dir_lock_is_released_on_exit(tmp_path: Path) -> None:
    with exclusive_data_dir(tmp_path / "state"):
        pass
    with exclusive_data_dir(tmp_path / "state"):
        pass
