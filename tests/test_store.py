import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from smurfbot.store import DataDirLockedError, Reminder, State, StateFileError, Store, Tag, data_dir_lock


def full_state() -> State:
    return State(
        deactivated={303},
        tags={728: {"cooltag": Tag(owner=112, content="this is a cool tag"), "b": Tag(owner=1, content="x")}},
        reminders=[
            Reminder(
                guild_id=728,
                channel_id=729,
                user_id=112,
                message="check the oven",
                due=datetime(2026, 6, 3, 14, 26, 47, tzinfo=UTC),
            )
        ],
    )


def test_missing_file_loads_empty_and_is_not_created(tmp_path: Path):
    store = Store(tmp_path / "state.json")
    assert store.state == State()
    assert not (tmp_path / "state.json").exists()


def test_save_then_load_round_trips(tmp_path: Path):
    path = tmp_path / "state.json"
    store = Store(path)
    store.state = full_state()
    store.save()

    assert Store(path).state == full_state()
    assert list(Store(path).state.tags[728]) == ["cooltag", "b"]
    raw = json.loads(path.read_text())
    assert raw == {
        "deactivated": [303],
        "tags": {
            "728": {"cooltag": {"owner": 112, "content": "this is a cool tag"}, "b": {"owner": 1, "content": "x"}}
        },
        "reminders": [
            {
                "guild_id": 728,
                "channel_id": 729,
                "user_id": 112,
                "message": "check the oven",
                "due": "2026-06-03T14:26:47Z",
            }
        ],
    }
    assert path.read_text().startswith('{\n  "deactivated"')


def test_save_creates_the_directory(tmp_path: Path):
    path = tmp_path / "a" / "b" / "state.json"
    store = Store(path)
    store.save()
    assert Store(path).state == State()


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        b'{"tags": {"1": {"x": {"owner": 1, "content": "\xff"}}}}',
        b'{"tags": []}',
        b'{"reminders": [{"guild_id": 1, "channel_id": 2, "user_id": 3, "message": "m", "due": "2026-06-03T14:26"}]}',
        b'{"deactivated": [], "groups": {}}',
        b'{"tags": {"1": {"x": {"owner": 1, "content": "c", "extra": 1}}}}',
    ],
    ids=["invalid-json", "invalid-utf8", "wrong-shape", "naive-due", "unknown-key", "unknown-tag-key"],
)
def test_bad_file_raises_and_is_left_alone(tmp_path: Path, content: bytes):
    path = tmp_path / "state.json"
    path.write_bytes(content)
    with pytest.raises(StateFileError, match=str(path)) as info:
        Store(path)
    assert "\n" not in str(info.value)
    assert path.read_bytes() == content


@pytest.mark.parametrize("target", ["fsync", "replace"])
def test_failed_write_keeps_old_file_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
):
    path = tmp_path / "state.json"
    store = Store(path)
    store.save()
    before = path.read_bytes()

    def fail(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, target, fail)
    store.state = full_state()
    with pytest.raises(OSError, match="disk full"):
        store.save()
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
    assert store.state == full_state()


def test_lock_refuses_a_second_holder_and_can_be_retaken(tmp_path: Path):
    data_dir = tmp_path / "data"
    with data_dir_lock(data_dir):
        with pytest.raises(DataDirLockedError) as info, data_dir_lock(data_dir):
            pass
        assert str(info.value) == f"Another SmurfBot process is already using {data_dir}"
    with data_dir_lock(data_dir):
        pass
