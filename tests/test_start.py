"""Tests for the Discord process adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import start
from settings import Settings

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_build_discord_intents_uses_only_required_privileges() -> None:
    intents = start.build_discord_intents()

    assert intents.guilds is True
    assert intents.guild_messages is True
    assert intents.dm_messages is True
    assert intents.message_content is True
    assert intents.members is False
    assert intents.presences is False


def test_main_constructs_runtime_registers_events_and_starts_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_value = "test-token"
    settings = Settings(delim="!", consume_time=0.1, refresh_group_interval=600, discord_token=configured_value)

    class RecordingClient:
        def __init__(self, *, intents: object) -> None:
            self.intents = intents
            self.events: list[object] = []
            self.run_tokens: list[str] = []

        def event(self, handler: object) -> object:
            self.events.append(handler)
            return handler

        def run(self, run_token: str) -> None:
            self.run_tokens.append(run_token)

    class RecordingRuntime:
        def __init__(self, client: RecordingClient, *, command_delimiter: str, data_dir: Path) -> None:
            self.client = client
            self.command_delimiter = command_delimiter
            self.data_dir = data_dir

        async def on_ready(self) -> None:
            return None

        async def on_guild_join(self, _guild: object) -> None:
            return None

        async def on_guild_remove(self, _guild: object) -> None:
            return None

        async def on_message(self, _message: object) -> None:
            return None

    clients: list[RecordingClient] = []

    def make_client(*, intents: object) -> RecordingClient:
        client = RecordingClient(intents=intents)
        clients.append(client)
        return client

    runtimes: list[RecordingRuntime] = []

    def make_runtime(client: RecordingClient, *, command_delimiter: str, data_dir: Path) -> RecordingRuntime:
        runtime = RecordingRuntime(client, command_delimiter=command_delimiter, data_dir=data_dir)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(start, "load_settings", lambda: settings)
    monkeypatch.setattr(start, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(start.discord, "Client", make_client)
    monkeypatch.setattr(start, "BotRuntime", make_runtime)
    monkeypatch.setattr(start, "configure_logging", lambda: None)

    start.main()

    assert len(clients) == 1
    assert len(runtimes) == 1
    assert runtimes[0].client is clients[0]
    assert runtimes[0].command_delimiter == "!"
    assert runtimes[0].data_dir == tmp_path
    assert [handler.__name__ for handler in clients[0].events] == [
        "on_ready",
        "on_guild_join",
        "on_guild_remove",
        "on_message",
    ]
    assert clients[0].run_tokens == [configured_value]
