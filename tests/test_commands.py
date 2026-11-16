import logging
from typing import Any

import pytest

from smurfbot.commands import Commands
from smurfbot.store import Store
from tests.helpers import GUILD, NOW, make_dm, make_request

HELP = (
    "The commands are: tag, git, mock, remind, activate, and deactivate. Tag and remind have their own help commands."
)


def run(store: Store, text: str, *, prefix: str = "$", **overrides: Any) -> str | None:
    return Commands(store, prefix, now=lambda: NOW).handle(make_request(text, **overrides))


@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("$ping", "pong"),
        ("$git", "Here is the source code: https://github.com/nithjino/SmurfBot"),
        ("$help", HELP),
        ("$mock", "Usage: mock [message]"),
        ("$mock  Ship IT  ", "ShIp It"),
        ("$mock this is a test", "ThIs Is A tEsT"),
        ("$PING", "pong"),
        ("$Help me", HELP),
        ("$   ", HELP),
        ("$", HELP),
    ],
)
def test_stateless_replies(store: Store, text: str, reply: str):
    assert run(store, text) == reply


@pytest.mark.parametrize("text", ["ping", "hello $ping", "", "$500 is the cost"])
def test_silent(store: Store, text: str):
    assert run(store, text) is None


def test_single_space_prefix(store: Store):
    assert run(store, " ping", prefix=" ") == "pong"
    assert run(store, "ping", prefix=" ") is None


def test_multi_character_prefix(store: Store):
    assert run(store, "!!ping", prefix="!!") == "pong"
    assert run(store, "!ping", prefix="!!") is None


def test_unknown_command_logs_its_name(store: Store, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    assert run(store, "$Frobnicate now") is None
    assert "Ignoring unknown command: frobnicate" in caplog.messages


@pytest.mark.parametrize("spelling", ["deactivate", "deactive", "DEACTIVATE"])
def test_deactivated_group_ignores_everything_but_activate(store: Store, spelling: str):
    assert run(store, f"${spelling}", is_admin=True) == "Guild has been deactivated."
    assert store.state.deactivated == {GUILD}
    for text in ["$ping", "$help", "$tag list", "$remind help", "$deactivate", "$nope"]:
        assert run(store, text, is_admin=True) is None
    assert run(store, "$activate", is_admin=True) == "Guild has been activated."
    assert store.state.deactivated == set()
    assert run(store, "$ping") == "pong"


def test_non_admins_are_refused_both_ways(store: Store):
    refusal = "Only server admins can activate or deactivate this bot."
    assert run(store, "$deactivate") == refusal
    assert run(store, "$deactive") == refusal
    assert store.state.deactivated == set()
    run(store, "$deactivate", is_admin=True)
    assert run(store, "$activate") == refusal
    assert store.state.deactivated == {GUILD}


def test_activate_when_already_active(store: Store):
    assert run(store, "$activate", is_admin=True) == "Guild has been activated."


def test_dm_and_guild_are_independent(store: Store):
    commands = Commands(store, "$")
    assert commands.handle(make_dm("$deactivate")) == "This DM has been deactivated."
    assert commands.handle(make_dm("$ping")) is None
    assert commands.handle(make_request("$ping")) == "pong"
    assert commands.handle(make_request("$deactivate", is_admin=True)) == "Guild has been deactivated."
    assert commands.handle(make_dm("$activate")) == "This DM has been activated."
    assert commands.handle(make_dm("$ping")) == "pong"
    assert commands.handle(make_request("$ping")) is None


def test_activation_survives_a_reload(store: Store):
    run(store, "$deactivate", is_admin=True)
    reloaded = Store(store.path)
    assert reloaded.state.deactivated == {GUILD}
    assert Commands(reloaded, "$").handle(make_request("$ping")) is None


def test_message_text_never_reaches_the_log(store: Store, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)
    secret = "zebra"
    texts = [
        f"$mock {secret}",
        f"$tag create {secret} {secret}",
        f"$tag {secret}",
        f"$tag edit {secret} {secret}2",
        f"$remind 1h {secret}",
        f"$remind list {secret}",
        f"$unknown {secret}",
        f"{secret}",
    ]
    for text in texts:
        run(store, text, attachment_url=None)
    assert caplog.records
    assert secret not in caplog.text
