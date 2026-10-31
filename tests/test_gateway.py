from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from smurfbot.config import Settings
from smurfbot.core import GroupRef
from smurfbot.gateway import DiscordGateway, build_intents, private_channel_name, to_inbound
from smurfbot.reminders import PermanentDeliveryError
from smurfbot.reminders.models import Reminder
from smurfbot.tags import UserLookupError
from tests.support import read_json, run, until

REMINDER = Reminder(
    user_id=123,
    name="Alice",
    message="check laundry",
    created_at=datetime(2026, 6, 4, tzinfo=UTC),
    execution_time=datetime(2026, 6, 4, 0, 5, tzinfo=UTC),
    guild_id=202,
    channel_id=303,
)


class RecordingChannel:
    def __init__(self, channel_id: int = 789, error: Exception | None = None) -> None:
        self.id = channel_id
        self.error = error
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, content: str, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((content, kwargs))


def http_error(error_type: type[discord.HTTPException], status: int) -> discord.HTTPException:
    return error_type(SimpleNamespace(status=status, reason="reason"), "failed")  # pyright: ignore[reportArgumentType]


def make_author(*, administrator: bool = False, manage_guild: bool = False, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=123,
        name="Alice",
        bot=bot,
        guild_permissions=SimpleNamespace(administrator=administrator, manage_guild=manage_guild),
    )


def make_message(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "content": "$ping",
        "author": make_author(),
        "guild": SimpleNamespace(id=456, name="Guild"),
        "channel": RecordingChannel(),
        "attachments": [],
    }
    return SimpleNamespace(**{**defaults, **overrides})


def make_gateway(data_dir: Path) -> DiscordGateway:
    return DiscordGateway(Settings(discord_token="t", data_dir=data_dir))  # pyright: ignore[reportArgumentType]


def test_intents_are_minimal() -> None:
    intents = build_intents()

    assert intents.guilds is True
    assert intents.guild_messages is True
    assert intents.dm_messages is True
    assert intents.message_content is True
    assert intents.members is False
    assert intents.presences is False


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        (SimpleNamespace(id=1, recipient=SimpleNamespace(name="Alice")), "DM: Alice"),
        (SimpleNamespace(id=2, recipient=None, name="Group Chat"), "DM: Group Chat"),
        (
            SimpleNamespace(id=3, recipients=[SimpleNamespace(name="Bob"), SimpleNamespace(name="Carol")]),
            "DM: Bob, Carol",
        ),
        (SimpleNamespace(id=4), "DM: 4"),
    ],
)
def test_private_channel_names(channel: object, expected: str) -> None:
    assert private_channel_name(channel) == expected


def test_guild_message_translation() -> None:
    attachment = SimpleNamespace(url="https://example.test/p.png")
    inbound = to_inbound(make_message(attachments=[attachment], author=make_author(bot=True)))

    assert inbound.content == "$ping"
    assert inbound.group == GroupRef(456, "Guild")
    assert inbound.is_guild is True
    assert inbound.author_id == 123
    assert inbound.author_name == "Alice"
    assert inbound.author_is_bot is True
    assert inbound.channel_id == 789
    assert inbound.attachment_url == attachment.url


def test_private_message_translation() -> None:
    channel = RecordingChannel(303)
    channel.recipient = SimpleNamespace(name="Alice")  # pyright: ignore[reportAttributeAccessIssue]
    inbound = to_inbound(make_message(guild=None, channel=channel, author=SimpleNamespace(id=1, name="A", bot=False)))

    assert inbound.group == GroupRef(303, "DM: Alice")
    assert inbound.is_guild is False
    assert inbound.can_manage_group is True
    assert inbound.attachment_url is None


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        (make_author(administrator=True), True),
        (make_author(manage_guild=True), True),
        (make_author(), False),
        (SimpleNamespace(id=1, name="A", bot=False), False),
    ],
)
def test_guild_management_permission(author: object, *, expected: bool) -> None:
    assert to_inbound(make_message(author=author)).can_manage_group is expected


def test_replies_cannot_mention_anyone() -> None:
    channel = RecordingChannel()

    async def exercise() -> None:
        await to_inbound(make_message(channel=channel)).reply("@everyone hello")

    run(exercise())

    content, kwargs = channel.sent[0]
    assert content == "@everyone hello"
    assert kwargs["allowed_mentions"].to_dict() == {"parse": []}


def test_reminders_mention_only_their_recipient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    channel = RecordingChannel()
    gateway = make_gateway(tmp_path)
    calls: list[tuple[int, int | None]] = []

    def partial(channel_id: int, *, guild_id: int | None = None) -> RecordingChannel:
        calls.append((channel_id, guild_id))
        return channel

    monkeypatch.setattr(gateway, "get_partial_messageable", partial)

    run(gateway._send_reminder(REMINDER, "<@123> reminder: @everyone <@456> hi"))

    assert calls == [(303, 202)]
    content, kwargs = channel.sent[0]
    assert content == "<@123> reminder: @everyone <@456> hi"
    assert kwargs["allowed_mentions"].to_dict() == {"users": [123], "parse": []}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (http_error(discord.NotFound, 404), PermanentDeliveryError),
        (http_error(discord.Forbidden, 403), PermanentDeliveryError),
        (http_error(discord.HTTPException, 400), PermanentDeliveryError),
        (http_error(discord.HTTPException, 429), discord.HTTPException),
        (http_error(discord.DiscordServerError, 503), discord.DiscordServerError),
        (ConnectionError("network"), ConnectionError),
    ],
    ids=["not-found", "forbidden", "bad-request", "rate-limited", "server-error", "network"],
)
def test_send_failures_are_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, expected: type[Exception]
) -> None:
    gateway = make_gateway(tmp_path)
    monkeypatch.setattr(gateway, "get_partial_messageable", lambda *_a, **_k: RecordingChannel(error=error))

    with pytest.raises(expected):
        run(gateway._send_reminder(REMINDER, "text"))


def test_user_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = make_gateway(tmp_path)

    async def fetch_user(_user_id: int) -> SimpleNamespace:
        return SimpleNamespace(name="Alice")

    monkeypatch.setattr(gateway, "fetch_user", fetch_user)

    assert run(gateway._fetch_user_name(7)) == "Alice"


@pytest.mark.parametrize("error_type", [discord.NotFound, discord.HTTPException])
def test_user_lookup_failures_are_translated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[discord.HTTPException]
) -> None:
    gateway = make_gateway(tmp_path)

    async def fetch_user(_user_id: int) -> None:
        raise http_error(error_type, 404)

    monkeypatch.setattr(gateway, "fetch_user", fetch_user)

    with pytest.raises(UserLookupError):
        run(gateway._fetch_user_name(7))


def test_events_drive_the_application(tmp_path: Path) -> None:
    channel = RecordingChannel()
    guild: Any = SimpleNamespace(id=456, name="Guild", unavailable=False)

    async def exercise() -> None:
        gateway = make_gateway(tmp_path)
        await gateway.setup_hook()
        await gateway.on_guild_join(guild)
        await gateway.on_guild_available(guild)
        await gateway.on_message(make_message(channel=channel))
        await gateway.on_message(make_message(content="$tag list", channel=channel))
        await gateway.on_guild_remove(guild)
        await gateway.close()

    run(exercise())

    assert [content for content, _ in channel.sent] == ["pong", "No tags exist"]
    assert read_json(tmp_path / "groups.json") == {"Guild": {"enabled": True, "id": "456"}}


def test_discord_dispatches_to_the_event_methods(tmp_path: Path) -> None:
    channel = RecordingChannel()

    async def exercise() -> None:
        gateway = make_gateway(tmp_path)
        async with gateway:
            await gateway.setup_hook()
            gateway.dispatch("message", make_message(channel=channel))
            await until(lambda: bool(channel.sent))

    run(exercise())

    assert [content for content, _ in channel.sent] == ["pong"]


def test_ready_skips_unavailable_guilds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discord reports outage-hit guilds with no name, which must not overwrite the stored one."""
    guilds = [
        SimpleNamespace(id=1, name="Up", unavailable=False),
        SimpleNamespace(id=2, name="", unavailable=True),
    ]
    private = [SimpleNamespace(id=3, recipient=SimpleNamespace(name="Alice"))]

    async def exercise() -> None:
        gateway = make_gateway(tmp_path)
        monkeypatch.setattr(DiscordGateway, "guilds", guilds)
        monkeypatch.setattr(DiscordGateway, "private_channels", private)
        monkeypatch.setattr(DiscordGateway, "user", None)
        await gateway.setup_hook()
        await gateway.on_ready()
        await gateway.close()

    run(exercise())

    assert read_json(tmp_path / "groups.json") == {
        "Up": {"enabled": True, "id": "1"},
        "DM: Alice": {"enabled": True, "id": "3"},
    }
