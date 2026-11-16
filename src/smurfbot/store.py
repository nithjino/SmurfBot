"""The bot's state: one JSON file, loaded once and held in memory."""

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from collections.abc import Generator

STATE_FILE = "state.json"


class Tag(BaseModel):
    """A named snippet of text owned by one user."""

    model_config = ConfigDict(extra="forbid")

    owner: int
    content: str


class Reminder(BaseModel):
    """A message to send back to a channel once it is due."""

    model_config = ConfigDict(extra="forbid")

    guild_id: int
    channel_id: int
    user_id: int
    message: str
    due: AwareDatetime


class State(BaseModel):
    """Everything the bot persists."""

    model_config = ConfigDict(extra="forbid")

    deactivated: set[int] = Field(default_factory=set[int])
    tags: dict[int, dict[str, Tag]] = Field(default_factory=dict[int, dict[str, Tag]])
    reminders: list[Reminder] = Field(default_factory=list[Reminder])


class StateFileError(Exception):
    """The state file exists but cannot be loaded."""


class DataDirLockedError(Exception):
    """Another process holds the data directory lock."""


class Store:
    """Holds the state in memory and rewrites the whole file on save."""

    def __init__(self, path: Path) -> None:
        """Load the state from path; a missing file means an empty state."""
        self.path = path
        self.state = _load(path)

    def save(self) -> None:
        """Atomically replace the state file with the in-memory state."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self.state.model_dump_json(indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            tmp.replace(self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def _load(path: Path) -> State:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return State()
    try:
        return State.model_validate_json(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise StateFileError(f"Cannot load {path}: the file is not valid UTF-8") from None
    except ValidationError as exc:
        error = exc.errors(include_url=False, include_input=False, include_context=False)[0]
        where = ".".join(str(part) for part in error["loc"]) or "top level"
        raise StateFileError(f"Cannot load {path}: {where}: {error['msg']}") from None


@contextmanager
def data_dir_lock(data_dir: Path) -> Generator[None]:
    """Hold an exclusive lock on the data directory for the duration of the block."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".lock").open("a") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DataDirLockedError(f"Another SmurfBot process is already using {data_dir}") from None
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
