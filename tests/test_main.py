import asyncio
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar, Self

import discord
import pytest
from pydantic import SecretStr

import smurfbot.__main__ as entrypoint
from smurfbot.config import Settings
from smurfbot.storage import StateFileError, exclusive_data_dir
from tests.support import run

TOKEN = "super-secret-token"


def run_python(code: str, **environment: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("SMURFBOT_")}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env={**env, **environment},
    )


class FakeGateway:
    """Connects until closed, like discord.Client.start."""

    instances: ClassVar[list[FakeGateway]] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tokens: list[str] = []
        self.closed = asyncio.Event()
        self.connected = asyncio.Event()
        type(self).instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def start(self, token: str) -> None:
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        self.connected.set()
        await self.closed.wait()

    async def close(self) -> None:
        self.closed.set()


@pytest.fixture
def fake_gateway(monkeypatch: pytest.MonkeyPatch) -> type[FakeGateway]:
    class Gateway(FakeGateway):
        pass

    Gateway.instances = []
    monkeypatch.setattr(entrypoint, "DiscordGateway", Gateway)
    return Gateway


def settings_for(data_dir: Path) -> Settings:
    return Settings(discord_token=SecretStr(TOKEN), data_dir=data_dir)


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
def test_stop_signals_close_the_gateway_cleanly(
    tmp_path: Path, fake_gateway: type[FakeGateway], stop_signal: signal.Signals
) -> None:
    async def exercise() -> None:
        serving = asyncio.create_task(entrypoint.serve(settings_for(tmp_path)))
        while not fake_gateway.instances:
            await asyncio.sleep(0)
        await asyncio.wait_for(fake_gateway.instances[0].connected.wait(), timeout=1)
        os.kill(os.getpid(), stop_signal)
        await asyncio.wait_for(serving, timeout=1)

    run(exercise())

    assert fake_gateway.instances[0].closed.is_set()
    assert fake_gateway.instances[0].tokens == [TOKEN]


def test_connection_errors_propagate(tmp_path: Path, fake_gateway: type[FakeGateway]) -> None:
    fake_gateway.error = discord.LoginFailure("bad token")

    with pytest.raises(discord.LoginFailure):
        run(entrypoint.serve(settings_for(tmp_path)))

    assert fake_gateway.instances[0].closed.is_set()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (discord.LoginFailure("bad token"), "bad token"),
        (StateFileError("groups.json is not valid"), "groups.json is not valid"),
    ],
)
def test_main_turns_expected_failures_into_a_clean_exit(
    tmp_path: Path,
    fake_gateway: type[FakeGateway],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    message: str,
) -> None:
    fake_gateway.error = error
    monkeypatch.setattr(entrypoint, "load_settings", lambda: settings_for(tmp_path))
    monkeypatch.setattr(entrypoint, "configure_logging", lambda _level: None)

    with pytest.raises(SystemExit) as exit_info:
        entrypoint.main()

    assert exit_info.value.code == message


def test_second_process_refuses_the_same_data_dir(
    tmp_path: Path, fake_gateway: type[FakeGateway], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(entrypoint, "load_settings", lambda: settings_for(tmp_path))
    monkeypatch.setattr(entrypoint, "configure_logging", lambda _level: None)

    with exclusive_data_dir(tmp_path), pytest.raises(SystemExit, match="already using"):
        entrypoint.main()

    assert fake_gateway.instances == []


def test_missing_token_exits_without_a_traceback_or_loading_discord() -> None:
    result = run_python(
        "import sys, smurfbot.__main__ as m\n"
        "try:\n    m.main()\n"
        "except SystemExit as e:\n    print('discord' in sys.modules); sys.exit(e.code)\n"
    )

    assert result.returncode == 1
    assert result.stdout.strip() == "False"
    assert "Invalid configuration: discord_token" in result.stderr
    assert "Traceback" not in result.stderr


def test_invalid_settings_never_echo_values() -> None:
    result = run_python(
        "import smurfbot.__main__ as m; m.main()",
        SMURFBOT_DISCORD_TOKEN=TOKEN,
        SMURFBOT_LOG_LEVEL="LOUD",
    )

    assert "log_level" in result.stderr
    assert TOKEN not in result.stderr
    assert "LOUD" not in result.stderr


def test_logging_setup_applies_the_level(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: calls.append(kwargs))

    entrypoint.configure_logging("WARNING")

    assert calls[0]["level"] == "WARNING"
