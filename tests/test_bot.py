import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from smurfbot.bot import ERROR_REPLY, MAX_MESSAGE_LENGTH, SmurfBot, split_reply, to_request
from smurfbot.core import Request
from smurfbot.reminders import PermanentDeliveryError
from smurfbot.store import Reminder, Store
from tests.helpers import NOW


class Channel:
    def __init__(self, channel_id: int = 200, error: BaseException | None = None) -> None:
        self.id = channel_id
        self.error = error
        self.sent: list[tuple[str, discord.AllowedMentions]] = []

    async def send(self, content: str, *, allowed_mentions: discord.AllowedMentions) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((content, allowed_mentions))


def permissions(*, administrator: bool = False, manage_guild: bool = False) -> SimpleNamespace:
    return SimpleNamespace(administrator=administrator, manage_guild=manage_guild)


def message(
    content: str = "$ping",
    *,
    guild: bool = True,
    bot: bool = False,
    perms: SimpleNamespace | None = None,
    attachments: tuple[str, ...] = (),
    channel: Channel | None = None,
) -> Any:
    author = SimpleNamespace(id=1, bot=bot)
    if guild:
        author.guild_permissions = perms or permissions()
    return SimpleNamespace(
        content=content,
        author=author,
        guild=SimpleNamespace(id=100, name="Smurf City") if guild else None,
        channel=channel or Channel(),
        attachments=[SimpleNamespace(url=url) for url in attachments],
    )


@pytest.fixture
def bot(tmp_path: Path) -> SmurfBot:
    return SmurfBot(Store(tmp_path / "state.json"), "$")


def http_error(status: int) -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=status, reason="nope"), "failed")  # pyright: ignore[reportArgumentType]


def assert_no_mentions(mentions: discord.AllowedMentions) -> None:
    assert (mentions.everyone, mentions.users, mentions.roles, mentions.replied_user) == (False, False, False, False)


def test_intents_are_exactly_the_four_needed(bot: SmurfBot):
    enabled = {name for name, on in bot.intents if on}
    assert enabled == {"guilds", "guild_messages", "dm_messages", "message_content"}


def test_guild_message_maps_to_a_request():
    request = to_request(message("$tag x", attachments=("https://a/1.png", "https://a/2.png")))
    assert request == Request(
        text="$tag x",
        author_id=1,
        guild_id=100,
        group_id=100,
        group_name="Smurf City",
        channel_id=200,
        is_admin=False,
        attachment_url="https://a/1.png",
    )


def test_dm_maps_to_a_request():
    request = to_request(message("$ping", guild=False, channel=Channel(555)))
    assert request == Request(
        text="$ping",
        author_id=1,
        guild_id=None,
        group_id=555,
        group_name="This DM",
        channel_id=555,
        is_admin=True,
        attachment_url=None,
    )


@pytest.mark.parametrize(
    ("perms", "is_admin"),
    [
        (permissions(), False),
        (permissions(administrator=True), True),
        (permissions(manage_guild=True), True),
    ],
)
def test_admin_rules(perms: SimpleNamespace, *, is_admin: bool):
    assert to_request(message(perms=perms)).is_admin is is_admin


def test_author_without_guild_permissions_is_not_admin():
    fake = message()
    del fake.author.guild_permissions
    assert to_request(fake).is_admin is False


def test_bot_authors_are_ignored(bot: SmurfBot):
    fake = message(bot=True)
    asyncio.run(bot.on_message(fake))
    assert fake.channel.sent == []


def test_silent_commands_send_nothing(bot: SmurfBot):
    fake = message("hello")
    asyncio.run(bot.on_message(fake))
    assert fake.channel.sent == []


def test_reply_is_sent_without_mentions(bot: SmurfBot):
    fake = message("$ping")
    asyncio.run(bot.on_message(fake))
    [(content, mentions)] = fake.channel.sent
    assert content == "pong"
    assert_no_mentions(mentions)


def test_long_reply_arrives_in_parts(bot: SmurfBot, monkeypatch: pytest.MonkeyPatch):
    words = " ".join(f"word{i}" for i in range(1500))
    monkeypatch.setattr(bot._commands, "handle", lambda _request: words)
    fake = message()
    asyncio.run(bot.on_message(fake))
    parts = [content for content, _ in fake.channel.sent]
    assert len(parts) > 1
    assert all(len(part) <= MAX_MESSAGE_LENGTH for part in parts)
    assert " ".join(parts).split() == words.split()
    for _, mentions in fake.channel.sent:
        assert_no_mentions(mentions)


def test_split_reply():
    assert split_reply("short") == ["short"]
    assert split_reply("") == []
    assert split_reply("x" * 2000) == ["x" * 2000]
    assert split_reply("x" * 4500) == ["x" * 2000, "x" * 2000, "x" * 500]
    assert split_reply("aaaa bbbb cc", limit=9) == ["aaaa bbbb", "cc"]
    assert split_reply("aaaa bbbbb", limit=9) == ["aaaa", "bbbbb"]
    assert split_reply("aaaa\nbb\ncc", limit=8) == ["aaaa\nbb", "cc"]


def test_raising_handler_sends_the_generic_reply_and_logs(
    bot: SmurfBot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    def boom(_request: Request) -> str:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(bot._commands, "handle", boom)
    fake = message("$Tag secret words")
    asyncio.run(bot.on_message(fake))
    assert [content for content, _ in fake.channel.sent] == [ERROR_REPLY]
    assert "Command tag failed in group 100, channel 200, author 1" in caplog.messages
    assert "secret" not in caplog.text


def test_failed_send_is_swallowed(bot: SmurfBot, caplog: pytest.LogCaptureFixture):
    fake = message("$ping", channel=Channel(error=http_error(500)))
    asyncio.run(bot.on_message(fake))
    assert "Could not send a reply to channel 200" in caplog.messages


def send_reminder(bot: SmurfBot, monkeypatch: pytest.MonkeyPatch, channel: Channel, text: str = "hi") -> list[int]:
    targets: list[int] = []

    def partial(channel_id: int) -> Channel:
        targets.append(channel_id)
        return channel

    monkeypatch.setattr(bot, "get_partial_messageable", partial)
    item = Reminder(guild_id=100, channel_id=321, user_id=42, message="m", due=NOW)
    asyncio.run(bot._send_reminder(item, text))
    return targets


def test_send_reminder_targets_the_channel_and_allows_only_the_recipient(
    bot: SmurfBot, monkeypatch: pytest.MonkeyPatch
):
    channel = Channel()
    assert send_reminder(bot, monkeypatch, channel, "x" * 2500) == [321]
    [(content, mentions)] = channel.sent
    assert content == "x" * 2000
    assert mentions.to_dict() == {"parse": [], "users": [42]}
    assert (mentions.everyone, mentions.roles, mentions.replied_user) == (False, False, False)


@pytest.mark.parametrize("status", [400, 403, 404])
def test_send_reminder_permanent_errors(bot: SmurfBot, monkeypatch: pytest.MonkeyPatch, status: int):
    with pytest.raises(PermanentDeliveryError):
        send_reminder(bot, monkeypatch, Channel(error=http_error(status)))


@pytest.mark.parametrize("error", [http_error(429), http_error(503), ConnectionError("reset")])
def test_send_reminder_reraises_other_errors_unchanged(
    bot: SmurfBot, monkeypatch: pytest.MonkeyPatch, error: Exception
):
    with pytest.raises(type(error)) as info:
        send_reminder(bot, monkeypatch, Channel(error=error))
    assert info.value is error
