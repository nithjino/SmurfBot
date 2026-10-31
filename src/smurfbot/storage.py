"""Durable JSON state files and exclusive ownership of the data directory."""

import asyncio
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Final
lazy from collections.abc import Generator

from pydantic import TypeAdapter, ValidationError

LOCK_FILENAME: Final = ".lock"


class StateFileError(Exception):
    """A state file exists but cannot be understood, so it must not be overwritten."""


class DataDirLockedError(RuntimeError):
    """Another bot process already owns the data directory."""


def write_atomic(path: Path, text: str) -> None:
    """Replace path with text so that a crash leaves the old or new file, never a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(text)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class JsonStore[T]:
    """Validated reads and atomic writes of one JSON file, off the event loop."""

    def __init__(self, path: Path, adapter: TypeAdapter[T]) -> None:
        """Bind the file to the schema that validates and serializes it."""
        self._path = path
        self._adapter = adapter

    async def load(self) -> T | None:
        """Return the stored value, or None when the file does not exist yet."""
        return await asyncio.to_thread(self._read)

    async def save(self, value: T) -> None:
        """Persist value, replacing the previous contents atomically."""
        payload = self._adapter.dump_python(value, mode="json")
        await asyncio.to_thread(write_atomic, self._path, json.dumps(payload, sort_keys=True, indent=2))

    def _read(self) -> T | None:
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            return self._adapter.validate_json(raw)
        except ValidationError as error:
            msg = f"{self._path} is not valid; leaving it untouched: {error}"
            raise StateFileError(msg) from error


@contextmanager
def exclusive_data_dir(data_dir: Path) -> Generator[None]:
    """Hold an advisory lock so two bot processes can't overwrite each other's state."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / LOCK_FILENAME).open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            msg = f"Another SmurfBot process is already using {data_dir}"
            raise DataDirLockedError(msg) from error
        yield
