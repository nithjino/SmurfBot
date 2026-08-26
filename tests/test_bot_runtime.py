"""Tests for instance-owned bot event workflows."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import weakref
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from bot_runtime import GENERIC_COMMAND_ERROR, BotRuntime, _RuntimeDependencies
from bot_state import DATA_DIR_ENV_VAR
from models import CommandParameters

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


class FakeClient:
    def __init__(self, guilds: list[object] | None = None, private_channels: list[object] | None = None) -> None:
        self.user = SimpleNamespace(id=999, name="SmurfBot", bot=True)
        self.guilds = guilds or []
        self.private_channels = private_channels or []
        self.fetched_user_ids: list[int] = []

    async def fetch_user(self, user_id: int) -> SimpleNamespace:
        self.fetched_user_ids.append(user_id)
        return SimpleNamespace(id=user_id, name=f"user-{user_id}")


class RecordingChannel:
    def __init__(self, channel_id: int = 789, *, send_error: Exception | None = None) -> None:
        self.id = channel_id
        self.send_error = send_error
        self.sent: list[tuple[str, dict[str, object]]] = []

    async def send(self, content: str, **kwargs: object) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((content, kwargs))


class RecordingTag:
    def __init__(self, response: str = "tag response") -> None:
        self.response = response
        self.parameters: list[CommandParameters] = []

    async def parse_commands(self, parameters: CommandParameters) -> str:
        self.parameters.append(parameters)
        return self.response


class RecordingReminder:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.close_error = close_error
        self.closed = False
        self.parameters: list[CommandParameters] = []

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error

    async def handle(self, parameters: CommandParameters) -> str:
        self.parameters.append(parameters)
        return "reminder response"


class RecordingFactories:
    def __init__(
        self,
        *,
        tag_response: str = "tag response",
        tag_failures: int = 0,
        reminder_failures: int = 0,
        close_error: Exception | None = None,
    ) -> None:
        self.tag_response = tag_response
        self.tag_failures = tag_failures
        self.reminder_failures = reminder_failures
        self.close_error = close_error
        self.tag_calls: list[tuple[object, Path]] = []
        self.reminder_calls: list[tuple[object, Path]] = []
        self.tags: dict[int, RecordingTag] = {}
        self.reminders: dict[int, RecordingReminder] = {}

    async def make_tag(self, guild: object, path: Path) -> RecordingTag:
        self.tag_calls.append((guild, path))
        await asyncio.sleep(0)
        if self.tag_failures:
            self.tag_failures -= 1
            msg = "tag factory failed"
            raise RuntimeError(msg)
        handler = RecordingTag(self.tag_response)
        self.tags[guild.id] = handler
        return handler

    async def make_reminder(self, guild: object, path: Path) -> RecordingReminder:
        self.reminder_calls.append((guild, path))
        await asyncio.sleep(0)
        if self.reminder_failures:
            self.reminder_failures -= 1
            msg = "reminder factory failed"
            raise RuntimeError(msg)
        handler = RecordingReminder(close_error=self.close_error)
        self.reminders[guild.id] = handler
        return handler

    def dependencies(self, substitutions: dict[str, object] | None = None) -> _RuntimeDependencies:
        return _RuntimeDependencies(
            tag_factory=self.make_tag,
            reminder_factory=self.make_reminder,
            command_substitutions=substitutions or {},
        )


def make_guild(guild_id: int = 456, name: str = "Guild") -> SimpleNamespace:
    return SimpleNamespace(id=guild_id, name=name)


def make_message(
    content: str,
    *,
    channel: RecordingChannel | None = None,
    guild: object | None = None,
    author: object | None = None,
    attachments: list[object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        author=author
        or SimpleNamespace(
            id=123,
            name="Alice",
            bot=False,
            guild_permissions=SimpleNamespace(administrator=True, manage_guild=False),
        ),
        guild=make_guild() if guild is None else guild,
        channel=channel or RecordingChannel(),
        created_at=datetime(2026, 6, 4, tzinfo=UTC),
        attachments=attachments or [],
    )


def make_runtime(
    data_dir: Path,
    *,
    client: FakeClient | None = None,
    delimiter: str = "$",
    factories: RecordingFactories | None = None,
    substitutions: dict[str, object] | None = None,
) -> tuple[BotRuntime, FakeClient, RecordingFactories]:
    runtime_client = client or FakeClient()
    runtime_factories = factories or RecordingFactories()
    runtime = BotRuntime(
        runtime_client,
        command_delimiter=delimiter,
        data_dir=data_dir,
        _dependencies=runtime_factories.dependencies(substitutions),
    )
    return runtime, runtime_client, runtime_factories


def sent_text(channel: RecordingChannel) -> list[str]:
    return [content for content, _kwargs in channel.sent]


def test_empty_delimiter_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="command_delimiter"):
        BotRuntime(FakeClient(), command_delimiter="", data_dir=tmp_path)


def test_non_empty_whitespace_delimiter_keeps_existing_parsing_behavior(tmp_path: Path) -> None:
    runtime, _client, _factories = make_runtime(tmp_path, delimiter=" ")
    channel = RecordingChannel()

    run(runtime.on_message(make_message(" ping", channel=channel)))
    run(runtime.on_message(make_message("$ping", channel=channel)))

    assert sent_text(channel) == ["pong"]


def test_equivalent_canonical_data_directory_cannot_be_claimed_twice(tmp_path: Path) -> None:
    runtime, _client, _factories = make_runtime(tmp_path / "state")
    equivalent_path = tmp_path / "state" / "nested" / ".."

    with pytest.raises(RuntimeError, match=str((tmp_path / "state").resolve())):
        make_runtime(equivalent_path)

    assert runtime is not None


def test_unreachable_runtime_releases_data_directory_claim(tmp_path: Path) -> None:
    runtime, _client, _factories = make_runtime(tmp_path)
    runtime_reference = weakref.ref(runtime)

    del runtime
    gc.collect()

    assert runtime_reference() is None
    replacement, _replacement_client, _replacement_factories = make_runtime(tmp_path)
    assert replacement is not None


@pytest.mark.parametrize("event", ["ready", "join"])
def test_activation_completes_before_either_guild_module_initializes(tmp_path: Path, event: str) -> None:
    guild = make_guild(101)
    observations: list[str] = []

    async def tag_factory(_guild: object, path: Path) -> RecordingTag:
        assert (path.parent / "groups.json").is_file()
        observations.append("tag")
        return RecordingTag()

    async def reminder_factory(_guild: object, path: Path) -> RecordingReminder:
        assert (path.parent / "groups.json").is_file()
        observations.append("reminder")
        return RecordingReminder()

    runtime = BotRuntime(
        FakeClient(guilds=[guild]),
        command_delimiter="$",
        data_dir=tmp_path,
        _dependencies=_RuntimeDependencies(tag_factory=tag_factory, reminder_factory=reminder_factory),
    )
    run(runtime.on_ready() if event == "ready" else runtime.on_guild_join(guild))
    assert observations == ["tag", "reminder"]


def test_concurrent_duplicate_ready_initializes_each_registry_once(tmp_path: Path) -> None:
    guild = make_guild(404)
    runtime, _client, factories = make_runtime(tmp_path, client=FakeClient(guilds=[guild]))

    async def ready_twice() -> None:
        await asyncio.gather(runtime.on_ready(), runtime.on_ready())

    run(ready_twice())

    assert factories.tag_calls == [(guild, tmp_path.resolve() / "tags")]
    assert factories.reminder_calls == [(guild, tmp_path.resolve() / "reminders")]


@pytest.mark.parametrize(("failed_factory", "expected_calls"), [("tag", (2, 1)), ("reminder", (1, 2))])
def test_partial_initialization_is_retained_and_missing_entry_retries(
    tmp_path: Path,
    failed_factory: str,
    expected_calls: tuple[int, int],
) -> None:
    guild = make_guild(505)
    factories = RecordingFactories(
        tag_failures=1 if failed_factory == "tag" else 0,
        reminder_failures=1 if failed_factory == "reminder" else 0,
    )
    runtime, _client, _factories = make_runtime(tmp_path, client=FakeClient(guilds=[guild]), factories=factories)

    run(runtime.on_ready())
    run(runtime.on_ready())

    assert (len(factories.tag_calls), len(factories.reminder_calls)) == expected_calls


def test_removal_closes_reminders_and_discards_both_registry_entries(tmp_path: Path) -> None:
    guild = make_guild(606)
    channel = RecordingChannel()
    runtime, _client, factories = make_runtime(tmp_path, client=FakeClient(guilds=[guild]))
    run(runtime.on_ready())

    run(runtime.on_guild_remove(guild))
    run(runtime.on_message(make_message("$tag launch", guild=guild, channel=channel)))

    assert factories.reminders[guild.id].closed is True
    assert sent_text(channel) == ["unable to get tag. tag function parameter is None"]


def test_reminder_close_failure_propagates_after_both_entries_are_discarded(tmp_path: Path) -> None:
    guild = make_guild(707)
    channel = RecordingChannel()
    close_error = RuntimeError("close failed")
    factories = RecordingFactories(close_error=close_error)
    runtime, _client, _factories = make_runtime(
        tmp_path,
        client=FakeClient(guilds=[guild]),
        factories=factories,
    )
    run(runtime.on_ready())

    with pytest.raises(RuntimeError, match="close failed"):
        run(runtime.on_guild_remove(guild))
    run(runtime.on_message(make_message("$tag launch", guild=guild, channel=channel)))

    assert sent_text(channel) == ["unable to get tag. tag function parameter is None"]


def test_two_runtime_instances_keep_guild_handlers_isolated(tmp_path: Path) -> None:
    guild = make_guild(808)
    first, _first_client, _first_factories = make_runtime(
        tmp_path / "first",
        client=FakeClient(guilds=[guild]),
        factories=RecordingFactories(tag_response="first"),
    )
    second, _second_client, _second_factories = make_runtime(
        tmp_path / "second",
        client=FakeClient(guilds=[guild]),
        factories=RecordingFactories(tag_response="second"),
    )
    first_channel = RecordingChannel()
    second_channel = RecordingChannel()

    run(first.on_ready())
    run(second.on_ready())
    run(first.on_message(make_message("$tag launch", guild=guild, channel=first_channel)))
    run(second.on_message(make_message("$tag launch", guild=guild, channel=second_channel)))

    assert sent_text(first_channel) == ["first"]
    assert sent_text(second_channel) == ["second"]


def test_message_ignores_current_user_other_bots_and_missing_delimiter(tmp_path: Path) -> None:
    runtime, client, _factories = make_runtime(tmp_path)
    channel = RecordingChannel()

    run(runtime.on_message(SimpleNamespace(content="$ping", author=client.user)))
    run(runtime.on_message(SimpleNamespace(content="$ping", author=SimpleNamespace(bot=True))))
    run(runtime.on_message(make_message("ping", channel=channel)))

    assert channel.sent == []


def test_delimiter_only_message_posts_help_when_group_is_enabled(tmp_path: Path) -> None:
    runtime, _client, _factories = make_runtime(tmp_path)
    channel = RecordingChannel()

    run(runtime.on_message(make_message("$   ", channel=channel)))

    assert sent_text(channel) == [
        "The commands are: tag, git, remind, activate, and deactive. Tag and remind have their own help commands."
    ]


def test_disabled_group_ignores_commands_until_activate(tmp_path: Path) -> None:
    runtime, _client, _factories = make_runtime(tmp_path)
    channel = RecordingChannel()
    message = make_message("$deactivate", channel=channel)

    run(runtime.on_message(message))
    message.content = "$ping"
    run(runtime.on_message(message))
    message.content = "$   "
    run(runtime.on_message(message))
    message.content = "$deactive"
    run(runtime.on_message(message))
    message.content = "$activate"
    run(runtime.on_message(message))
    message.content = "$ping"
    run(runtime.on_message(message))

    assert sent_text(channel) == ["Guild has been deactived.", "Guild has been activated.", "pong"]


@pytest.mark.parametrize("command", ["deactive", "deactivate"])
def test_both_deactivation_aliases_disable_the_group(tmp_path: Path, command: str) -> None:
    runtime, _client, _factories = make_runtime(tmp_path)
    channel = RecordingChannel()

    run(runtime.on_message(make_message(f"${command}", channel=channel)))
    run(runtime.on_message(make_message("$ping", channel=channel)))

    assert sent_text(channel) == ["Guild has been deactived."]


def test_unknown_command_is_logged_without_a_response(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime, _client, _factories = make_runtime(tmp_path)
    channel = RecordingChannel()

    with caplog.at_level(logging.INFO):
        run(runtime.on_message(make_message("$500 is the cost", channel=channel)))

    assert channel.sent == []
    assert "Ignoring unknown command: 500" in caplog.text


def test_mock_behavior_is_available_only_through_runtime_messages(tmp_path: Path) -> None:
    runtime, _client, _factories = make_runtime(tmp_path)
    channel = RecordingChannel()

    run(runtime.on_message(make_message("$mock  Ship IT  ", channel=channel)))

    assert sent_text(channel) == ["ShIp It"]


def test_command_substitutions_replace_builtins_and_are_copied_during_construction(tmp_path: Path) -> None:
    async def first(_parameters: CommandParameters) -> str:
        return "first"

    async def second(_parameters: CommandParameters) -> str:
        return "second"

    substitutions = {"ping": first, "PING": second}
    runtime, _client, _factories = make_runtime(tmp_path, substitutions=substitutions)
    substitutions["ping"] = second
    channel = RecordingChannel()

    run(runtime.on_message(make_message("$PING", channel=channel)))

    assert sent_text(channel) == ["first"]


def test_uppercase_only_command_substitution_does_not_replace_lowercase_builtin(tmp_path: Path) -> None:
    async def replacement(_parameters: CommandParameters) -> str:
        return "replacement"

    runtime, _client, _factories = make_runtime(tmp_path, substitutions={"PING": replacement})
    channel = RecordingChannel()

    run(runtime.on_message(make_message("$PING", channel=channel)))

    assert sent_text(channel) == ["pong"]


def test_tag_owner_command_receives_client_user_lookup(tmp_path: Path) -> None:
    guild = make_guild(909)
    runtime, client, factories = make_runtime(tmp_path, client=FakeClient(guilds=[guild]))
    channel = RecordingChannel()
    run(runtime.on_ready())

    run(runtime.on_message(make_message("$tag owner launch", guild=guild, channel=channel)))
    parameters = factories.tags[guild.id].parameters[0]
    fetched_user = run(parameters.fetch_user_func(42))

    assert fetched_user.name == "user-42"
    assert client.fetched_user_ids == [42]


def test_tag_command_parameters_preserve_message_context_and_attachment(tmp_path: Path) -> None:
    guild = make_guild(911)
    runtime, _client, factories = make_runtime(tmp_path, client=FakeClient(guilds=[guild]))
    channel = RecordingChannel(321)
    attachment = SimpleNamespace(url="https://example.test/image.png")
    run(runtime.on_ready())

    run(runtime.on_message(make_message("$tag create launch", guild=guild, channel=channel, attachments=[attachment])))

    parameters = factories.tags[guild.id].parameters[0]
    assert parameters.command == "tag"
    assert parameters.message == ["create", "launch"]
    assert parameters.attachment == attachment.url
    assert parameters.created_at == datetime(2026, 6, 4, tzinfo=UTC)
    assert parameters.author_id == 123
    assert parameters.author_name == "Alice"
    assert parameters.guild_id == guild.id
    assert parameters.channel_id == channel.id
    assert parameters.fetch_user_func is None


@pytest.mark.parametrize("arguments", ["5m drink water", "", "list", "help ignored", "bad duration"])
def test_reminder_command_routes_to_initialized_guild_handler(tmp_path: Path, arguments: str) -> None:
    guild = make_guild(910)
    runtime, _client, factories = make_runtime(tmp_path, client=FakeClient(guilds=[guild]))
    channel = RecordingChannel()
    run(runtime.on_ready())

    run(runtime.on_message(make_message(f"$remind {arguments}", guild=guild, channel=channel)))

    assert sent_text(channel) == ["reminder response"]
    assert factories.reminders[guild.id].parameters == [
        CommandParameters(
            command="remind",
            message=arguments.split(),
            created_at=datetime(2026, 6, 4, tzinfo=UTC),
            author_id=123,
            author_name="Alice",
            guild_id=910,
            channel_id=789,
        )
    ]


def test_command_handler_failure_becomes_generic_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def fail(_parameters: CommandParameters) -> str:
        msg = "handler failed"
        raise RuntimeError(msg)

    runtime, _client, _factories = make_runtime(tmp_path, substitutions={"explode": fail})
    channel = RecordingChannel()

    with caplog.at_level(logging.ERROR):
        run(runtime.on_message(make_message("$explode now", channel=channel)))

    assert sent_text(channel) == [GENERIC_COMMAND_ERROR]
    assert "Failed to handle command explode in guild 456 channel 789 author 123" in caplog.text


def test_command_parameter_failure_becomes_generic_error(tmp_path: Path) -> None:
    async def command(_parameters: CommandParameters) -> str:
        return "unreachable"

    runtime, _client, _factories = make_runtime(tmp_path, substitutions={"test": command})
    channel = RecordingChannel()
    message = make_message("$test", channel=channel)
    del message.attachments

    run(runtime.on_message(message))

    assert sent_text(channel) == [GENERIC_COMMAND_ERROR]


def test_response_send_failure_is_logged_and_swallowed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    runtime, _client, _factories = make_runtime(tmp_path)
    channel = RecordingChannel(send_error=RuntimeError("send failed"))

    with caplog.at_level(logging.ERROR):
        run(runtime.on_message(make_message("$ping", channel=channel)))

    assert "Failed to send response for command ping in guild 456 channel 789 author 123" in caplog.text


def test_runtime_uses_owned_groups_path_even_when_environment_points_elsewhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned_dir = tmp_path / "owned"
    environment_dir = tmp_path / "environment"
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(environment_dir))
    runtime, _client, _factories = make_runtime(owned_dir)

    run(runtime.on_message(make_message("$deactive")))

    assert (owned_dir / "groups.json").is_file()
    assert not (environment_dir / "groups.json").exists()


@pytest.mark.parametrize("event", ["ready", "join", "message"])
def test_activation_file_failures_propagate(tmp_path: Path, event: str) -> None:
    (tmp_path / "groups.json").write_text("{bad json", encoding="utf-8")
    guild = make_guild()
    runtime, _client, _factories = make_runtime(tmp_path, client=FakeClient(guilds=[guild]))

    if event == "ready":
        operation = runtime.on_ready()
    elif event == "join":
        operation = runtime.on_guild_join(guild)
    else:
        operation = runtime.on_message(make_message("$ping", guild=guild))

    with pytest.raises(json.JSONDecodeError):
        run(operation)


def test_cancelled_command_is_not_translated_to_generic_error(tmp_path: Path) -> None:
    async def cancel(_parameters: CommandParameters) -> str:
        raise asyncio.CancelledError

    runtime, _client, _factories = make_runtime(tmp_path, substitutions={"cancel": cancel})

    with pytest.raises(asyncio.CancelledError):
        run(runtime.on_message(make_message("$cancel")))


def test_cancelled_factory_is_not_swallowed_as_partial_initialization(tmp_path: Path) -> None:
    guild = make_guild()
    factories = RecordingFactories()

    async def cancel_tag(_guild: object, _path: Path) -> RecordingTag:
        raise asyncio.CancelledError

    runtime = BotRuntime(
        FakeClient(guilds=[guild]),
        command_delimiter="$",
        data_dir=tmp_path,
        _dependencies=_RuntimeDependencies(tag_factory=cancel_tag, reminder_factory=factories.make_reminder),
    )

    with pytest.raises(asyncio.CancelledError):
        run(runtime.on_ready())

    assert factories.reminder_calls == []


def test_cancelled_response_send_is_not_swallowed(tmp_path: Path) -> None:
    class CancellingChannel(RecordingChannel):
        async def send(self, _content: str, **_kwargs: object) -> None:
            raise asyncio.CancelledError

    runtime, _client, _factories = make_runtime(tmp_path)

    with pytest.raises(asyncio.CancelledError):
        run(runtime.on_message(make_message("$ping", channel=CancellingChannel())))


def test_cancelled_reminder_close_propagates_after_registry_removal(tmp_path: Path) -> None:
    guild = make_guild()

    class CancellingReminder(RecordingReminder):
        async def close(self) -> None:
            raise asyncio.CancelledError

    async def make_reminder(_guild: object, _path: Path) -> CancellingReminder:
        return CancellingReminder()

    factories = RecordingFactories()
    runtime = BotRuntime(
        FakeClient(guilds=[guild]),
        command_delimiter="$",
        data_dir=tmp_path,
        _dependencies=_RuntimeDependencies(tag_factory=factories.make_tag, reminder_factory=make_reminder),
    )
    run(runtime.on_ready())

    with pytest.raises(asyncio.CancelledError):
        run(runtime.on_guild_remove(guild))

    channel = RecordingChannel()
    run(runtime.on_message(make_message("$tag launch", guild=guild, channel=channel)))
    assert sent_text(channel) == ["unable to get tag. tag function parameter is None"]


@pytest.mark.parametrize("bad_registry", ["tags", "reminders"])
def test_malformed_guild_registry_file_is_retained_while_other_registry_initializes(
    tmp_path: Path,
    bad_registry: str,
) -> None:
    guild = make_guild(111)
    bad_path = tmp_path / bad_registry
    bad_path.mkdir()
    bad_file = bad_path / f"{guild.id}.json"
    bad_file.write_text("{bad json", encoding="utf-8")
    runtime = BotRuntime(FakeClient(guilds=[guild]), command_delimiter="$", data_dir=tmp_path)

    run(runtime.on_ready())

    assert bad_file.read_text(encoding="utf-8") == "{bad json"
    other_file = tmp_path / ("reminders" if bad_registry == "tags" else "tags") / f"{guild.id}.json"
    assert other_file.exists()


@pytest.mark.parametrize("command", ["remind", "tag"])
def test_guild_commands_preserve_missing_guild_response(tmp_path: Path, command: str) -> None:
    runtime, _client, _factories = make_runtime(tmp_path)
    channel = RecordingChannel()
    message = make_message(f"${command}", channel=channel)
    message.guild = None
    run(runtime.on_message(message))
    expected = (
        "guild_id (None) or channel_id 789 is None." if command == "remind" else "unable to get tag. guild_id is None"
    )
    assert sent_text(channel) == [expected]


def test_missing_reminder_handler_keeps_empty_response(tmp_path: Path) -> None:
    runtime, _client, _factories = make_runtime(tmp_path)
    channel = RecordingChannel()
    run(runtime.on_message(make_message("$remind 5m stretch", channel=channel)))
    assert sent_text(channel) == [""]


def test_guild_removal_discards_both_registries_before_awaiting_shutdown(tmp_path: Path) -> None:
    async def exercise() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class WaitingReminder(RecordingReminder):
            async def close(self) -> None:
                entered.set()
                await release.wait()

        async def make_reminder(_guild: object, _path: Path) -> WaitingReminder:
            return WaitingReminder()

        guild = make_guild()
        factories = RecordingFactories()
        runtime = BotRuntime(
            FakeClient(guilds=[guild]),
            command_delimiter="$",
            data_dir=tmp_path,
            _dependencies=_RuntimeDependencies(tag_factory=factories.make_tag, reminder_factory=make_reminder),
        )
        await runtime.on_ready()
        removal = asyncio.create_task(runtime.on_guild_remove(guild))
        await asyncio.wait_for(entered.wait(), timeout=1)
        channel = RecordingChannel()
        await runtime.on_message(make_message("$tag launch", channel=channel))
        await runtime.on_message(make_message("$remind 5m stretch", channel=channel))
        assert sent_text(channel) == ["unable to get tag. tag function parameter is None", ""]
        release.set()
        await removal

    run(exercise())
