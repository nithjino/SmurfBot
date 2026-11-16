import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from smurfbot.migrate import MigrationError, main, migrate
from smurfbot.store import Reminder, State, Store, Tag


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    write(
        tmp_path / "groups.json",
        {
            "Some Guild": {"id": "111", "enabled": True},
            "Quiet Guild": {"id": 222, "enabled": 0, "extra": "ignored"},
            "DM: 333": {"id": "333"},
            "Off DM": {"id": "444", "enabled": False},
        },
    )
    write(tmp_path / "groups_dev.json", {"Dev": {"id": "999", "enabled": False}})
    write(
        tmp_path / "tags" / "111.json",
        {
            "id": "111",
            "name": "Some Guild",
            "tags": {"zeta": {"owner": "5", "content": "z"}, "alpha": {"content": "a", "owner": 6}},
        },
    )
    write(
        tmp_path / "tags" / "222.json", {"id": 222, "name": "Quiet Guild", "tags": {"x": {"owner": 7, "content": "x"}}}
    )
    write(
        tmp_path / "reminders" / "111.json",
        {
            "id": 111,
            "name": "Some Guild",
            "reminders": [
                {
                    "user_id": 5,
                    "name": "alice",
                    "message": "check the oven",
                    "created_at": "2026-06-03T14:16:47",
                    "execution_time": "2026-06-03T14:26:47",
                    "timezone": "utc",
                    "guild_id": 111,
                    "channel_id": 2,
                }
            ],
        },
    )
    write(tmp_path / "reminders" / "222.json", {"id": 222, "name": "Quiet Guild", "reminders": []})
    return tmp_path


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*.json")}


def test_converts_the_legacy_tree(legacy: Path, caplog: pytest.LogCaptureFixture):
    before = snapshot(legacy)
    caplog.set_level(logging.INFO)
    main([str(legacy)])
    expected = State(
        deactivated={222, 444},
        tags={
            111: {"zeta": Tag(owner=5, content="z"), "alpha": Tag(owner=6, content="a")},
            222: {"x": Tag(owner=7, content="x")},
        },
        reminders=[
            Reminder(
                guild_id=111,
                channel_id=2,
                user_id=5,
                message="check the oven",
                due=datetime(2026, 6, 3, 14, 26, 47, tzinfo=UTC),
            )
        ],
    )
    state = Store(legacy / "state.json").state
    assert state == expected
    assert list(state.tags[111]) == ["zeta", "alpha"]
    assert snapshot(legacy) == before | {"state.json": (legacy / "state.json").read_bytes()}
    assert "Imported 2 deactivated groups, tags for 2 guilds (111: 2, 222: 1), and 1 reminders" in caplog.messages


def test_missing_legacy_files_are_skipped(tmp_path: Path):
    assert migrate(tmp_path) == State()
    assert Store(tmp_path / "state.json").state == State()


def test_existing_state_is_refused(legacy: Path):
    (legacy / "state.json").write_text("{}")
    with pytest.raises(MigrationError, match="already exists"):
        migrate(legacy)
    assert (legacy / "state.json").read_text() == "{}"
    with pytest.raises(SystemExit, match="already exists"):
        main([str(legacy)])


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        ("tags/222.json", b"{broken"),
        ("groups.json", b'{"G": {"enabled": true}}'),
        (
            "reminders/111.json",
            (
                b'{"reminders": [{"user_id": 1, "message": "m", "execution_time": "soon",'
                b' "guild_id": 1, "channel_id": 2}]}'
            ),
        ),
        ("reminders/222.json", b"\xff\xfe"),
    ],
)
def test_corrupt_legacy_file_aborts_without_writing(legacy: Path, relative: str, content: bytes):
    path = legacy / relative
    path.write_bytes(content)
    before = snapshot(legacy)
    with pytest.raises(MigrationError, match=str(path)):
        migrate(legacy)
    assert not (legacy / "state.json").exists()
    assert snapshot(legacy) == before


def test_usage(tmp_path: Path):
    with pytest.raises(SystemExit, match="Usage"):
        main([])
    with pytest.raises(SystemExit, match="Usage"):
        main([str(tmp_path), "extra"])
