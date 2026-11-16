from pathlib import Path

import pytest

from smurfbot.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "state.json")
