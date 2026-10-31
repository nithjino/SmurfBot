"""End-to-end behavior through the router, with real services and real files."""

import asyncio
import logging
from pathlib import Path

import pytest

from smurfbot.app import Application
from smurfbot.commands import HELP_TEXT
from smurfbot.core import CommandContext, GroupRef
from smurfbot.reminders.models import Reminder
from smurfbot.router import GENERIC_COMMAND_ERROR, CommandRouter
from smurfbot.storage import StateFileError
from smurfbot.text import DISCORD_MESSAGE_LIMIT
from tests.support import GUILD, Chat, FakeClock, RecordingSender, read_json, run, until


async def fetch_name(user_id: int) -> str:
    return f"user-{user_id}"


def make(data_dir: Path, *, prefix: str = "$", send: RecordingSender | None = None, clock: FakeClock | None = None):
    return Application(
        data_dir=data_dir,
        prefix=prefix,
        send_reminder=send or RecordingSender(),
        fetch_user_name=fetch_name,
        clock=(clock or FakeClock()).clock,
    )


async def started(data_dir: Path, **kwargs: object) -> Application:
    app = make(data_dir, **kwargs)  # pyright: ignore[reportArgumentType]
    await app.start()
    return app


async def say(app: Application, chat: Chat, *contents: str) -> list[str]:
    for content in contents:
        await app.router.handle(chat.message(content))
    return chat.replies


def test_empty_prefix_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="prefix"):
        make(tmp_path, prefix="")


def test_whitespace_prefix_works(tmp_path: Path) -> None:
    async def exercise() -> list[str]:
        return await say(await started(tmp_path, prefix=" "), Chat(), " ping", "$ping")

    assert run(exercise()) == ["pong"]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("$ping", ["pong"]),
        ("$PING", ["pong"]),
        ("$git", ["Here is the source code: https://github.com/nithjino/SmurfBot"]),
        ("$mock  Ship IT  ", ["ShIp It"]),
        ("$mock", ["Usage: mock [message]"]),
        ("$   ", [HELP_TEXT]),
        ("$help", [HELP_TEXT]),
        ("ping", []),
        ("$500 is the cost", []),
    ],
)
def test_commands(tmp_path: Path, content: str, expected: list[str]) -> None:
    async def exercise() -> list[str]:
        return await say(await started(tmp_path), Chat(), content)

    assert run(exercise()) == expected


def test_bots_are_ignored(tmp_path: Path) -> None:
    chat = Chat()

    async def exercise() -> None:
        app = await started(tmp_path)
        await app.router.handle(chat.message("$ping", author_is_bot=True))

    run(exercise())

    assert chat.replies == []


def test_unknown_commands_are_logged_without_a_reply(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> list[str]:
        return await say(await started(tmp_path), Chat(), "$nope")

    with caplog.at_level(logging.INFO):
        assert run(exercise()) == []

    assert "Ignoring unknown command: nope" in caplog.text


def test_message_text_is_not_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        await say(await started(tmp_path), Chat(), "$mock very private words", "$tag create x secret stuff")

    with caplog.at_level(logging.DEBUG):
        run(exercise())

    assert "private" not in caplog.text
    assert "secret" not in caplog.text


def test_deactivated_group_ignores_everything_but_activate(tmp_path: Path) -> None:
    async def exercise() -> list[str]:
        return await say(
            await started(tmp_path),
            Chat(),
            "$deactivate",
            "$ping",
            "$   ",
            "$deactive",
            "$activate",
            "$ping",
        )

    assert run(exercise()) == ["Guild has been deactivated.", "Guild has been activated.", "pong"]


@pytest.mark.parametrize("command", ["deactive", "deactivate"])
def test_both_deactivation_spellings_work(tmp_path: Path, command: str) -> None:
    async def exercise() -> list[str]:
        return await say(await started(tmp_path), Chat(), f"${command}", "$ping")

    assert run(exercise()) == ["Guild has been deactivated."]


def test_activation_needs_permission_in_a_guild(tmp_path: Path) -> None:
    denied = "Only server admins can activate or deactivate this bot."

    async def exercise() -> list[str]:
        app = await started(tmp_path)
        chat = Chat(can_manage_group=False)
        await say(app, chat, "$deactivate", "$activate", "$ping")
        return chat.replies

    assert run(exercise()) == [denied, denied, "pong"]


def test_private_conversations_track_activation_separately(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = await started(tmp_path)
        private = Chat(group=GroupRef(303, "DM: Alice"), is_guild=False)
        await say(app, private, "$deactivate", "$ping")
        assert private.replies == ["DM: Alice has been deactivated."]
        assert await say(app, Chat(), "$ping") == ["pong"]

    run(exercise())


def test_guild_commands_explain_themselves_in_private_conversations(tmp_path: Path) -> None:
    async def exercise() -> list[str]:
        chat = Chat(group=GroupRef(303, "DM: Alice"), is_guild=False)
        return await say(await started(tmp_path), chat, "$tag list", "$remind list")

    assert run(exercise()) == ["Tags only work in a server.", "Reminders only work in a server."]


def test_tags_and_reminders_work_through_the_router(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = await started(tmp_path)
        chat = Chat()
        await app.router.handle(chat.message("$tag create pic", attachment_url="https://example.test/p.png"))
        await say(app, chat, "$tag pic", "$tag owner pic", "$remind 5m stretch")
        assert chat.replies[:3] == [
            'The tag "pic" was created successfully',
            "https://example.test/p.png",
            'The owner of "pic" is user-7',
        ]
        assert chat.replies[3].startswith("Created reminder for Alice")
        await app.stop()

    run(exercise())


def test_long_replies_are_truncated(tmp_path: Path) -> None:
    async def exercise() -> list[str]:
        return await say(await started(tmp_path), Chat(), f"$mock {'a' * (DISCORD_MESSAGE_LIMIT + 1)}")

    assert len(run(exercise())[0]) == DISCORD_MESSAGE_LIMIT


def test_failed_handler_becomes_a_generic_reply_and_a_log(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def fail(_context: CommandContext) -> str:
        raise RuntimeError("handler failed")

    async def exercise() -> list[str]:
        app = make(tmp_path)
        await app.start()
        router = CommandRouter("$", app._activation, {"explode": fail})
        chat = Chat()
        await router.handle(chat.message("$explode now"))
        return chat.replies

    with caplog.at_level(logging.ERROR):
        assert run(exercise()) == [GENERIC_COMMAND_ERROR]

    assert "Failed to handle command explode in group 202 channel 303 author 7" in caplog.text


def test_failed_reply_is_logged_and_swallowed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        await say(await started(tmp_path), Chat(reply_error=RuntimeError("send failed")), "$ping")

    with caplog.at_level(logging.ERROR):
        run(exercise())

    assert "Failed to send response for command ping in group 202 channel 303 author 7" in caplog.text


@pytest.mark.parametrize("target", ["handler", "reply"])
def test_cancellation_is_never_swallowed(tmp_path: Path, target: str) -> None:
    async def cancel(_context: CommandContext) -> str:
        raise asyncio.CancelledError

    async def exercise() -> None:
        app = make(tmp_path)
        await app.start()
        router = CommandRouter("$", app._activation, {"go": cancel if target == "handler" else _pong})
        chat = Chat(reply_error=asyncio.CancelledError() if target == "reply" else None)  # pyright: ignore[reportArgumentType]
        with pytest.raises(asyncio.CancelledError):
            await router.handle(chat.message("$go"))

    run(exercise())


async def _pong(_context: CommandContext) -> str:
    return "pong"


def test_sync_records_groups_and_opens_each_guild_service(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = await started(tmp_path)
        await app.sync([GUILD], [GroupRef(303, "DM: Alice")])
        await app.sync([GUILD], [GroupRef(303, "DM: Alice")])
        await app.stop()

    run(exercise())

    assert read_json(tmp_path / "groups.json") == {
        "Guild": {"enabled": True, "id": "202"},
        "DM: Alice": {"enabled": True, "id": "303"},
    }
    assert (tmp_path / "tags" / "202.json").is_file()
    assert (tmp_path / "reminders" / "202.json").is_file()


def test_join_opens_a_new_guild(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = await started(tmp_path)
        await app.join(GUILD)
        await app.stop()

    run(exercise())

    assert (tmp_path / "tags" / "202.json").is_file()


def test_commands_work_before_any_sync(tmp_path: Path) -> None:
    """A message that outruns the ready event used to get a confusing failure reply."""

    async def exercise() -> list[str]:
        return await say(await started(tmp_path), Chat(), "$tag list")

    assert run(exercise()) == ["No tags exist"]


@pytest.mark.parametrize(("broken", "working"), [("tags", "reminders"), ("reminders", "tags")])
def test_corrupt_guild_file_disables_only_that_service(
    tmp_path: Path, broken: str, working: str, caplog: pytest.LogCaptureFixture
) -> None:
    bad_file = tmp_path / broken / "202.json"
    bad_file.parent.mkdir()
    bad_file.write_text("{bad json", encoding="utf-8")

    async def exercise() -> list[str]:
        app = await started(tmp_path)
        await app.sync([GUILD], [])
        replies = await say(app, Chat(), "$tag list", "$remind list")
        await app.stop()
        return replies

    replies = run(exercise())

    assert bad_file.read_text(encoding="utf-8") == "{bad json"
    assert (tmp_path / working / "202.json").is_file()
    assert GENERIC_COMMAND_ERROR in replies
    assert len(set(replies)) == 2
    assert f"Skipping {broken} for guild 202" in caplog.text


def test_corrupt_groups_file_aborts_startup(tmp_path: Path) -> None:
    """Running anyway would silently reactivate every deactivated group."""
    path = tmp_path / "groups.json"
    path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(StateFileError):
        run(make(tmp_path).start())

    assert path.read_text(encoding="utf-8") == "{bad json"


def test_leaving_a_guild_stops_timers_and_keeps_files(tmp_path: Path) -> None:
    async def exercise() -> None:
        fake = FakeClock()
        send = RecordingSender()
        app = await started(tmp_path, clock=fake, send=send)
        await say(app, Chat(), "$remind 5m stretch")
        await until(lambda: fake.waiting == 1)
        await app.leave(GUILD.id)
        await fake.advance(300)
        await asyncio.sleep(0)
        assert send.attempts == []
        assert len(read_json(tmp_path / "reminders" / "202.json")["reminders"]) == 1

    run(exercise())


def test_reminders_survive_a_restart(tmp_path: Path) -> None:
    async def exercise() -> None:
        fake = FakeClock()
        send = RecordingSender()
        first = await started(tmp_path, clock=fake)
        await say(first, Chat(), "$remind 5m stretch")
        await first.stop()

        await fake.advance(3600)
        second = await started(tmp_path, clock=fake, send=send)
        await second.sync([GUILD], [])
        await until(lambda: read_json(tmp_path / "reminders" / "202.json")["reminders"] == [])
        reminder, content = send.attempts[0]
        assert isinstance(reminder, Reminder)
        assert content == "<@7> reminder (was due 06/04/2026 @ 08:05AM): stretch"
        await second.stop()

    run(exercise())


def test_stop_is_safe_before_start_and_when_repeated(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = make(tmp_path)
        await app.stop()
        await app.stop()

    run(exercise())
