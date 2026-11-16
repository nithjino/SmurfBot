import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from smurfbot.reminders import (
    HELP_TEXT,
    MAX_REMINDERS_PER_GUILD,
    InvalidDelayError,
    PermanentDeliveryError,
    deliver_due,
    parse_delay,
    remind_command,
    render,
)
from smurfbot.store import Reminder, Store
from tests.helpers import AUTHOR, CHANNEL, GUILD, NOW, make_dm, make_request

BAD_UNIT = "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"
TOO_FAR = "Why are you using this feature for a reminder that far in the future?"
PAST = "Why are you trying to set a reminder for the past?"
NOW_TS = int(NOW.timestamp())


def remind(store: Store, text: str, **overrides: Any) -> str:
    return remind_command(store, make_request(**overrides), text.split(), NOW)


def reminder(message: str = "m", due: datetime = NOW, **overrides: int) -> Reminder:
    fields = {"guild_id": GUILD, "channel_id": CHANNEL, "user_id": AUTHOR} | overrides
    return Reminder(message=message, due=due, **fields)


class Sender:
    def __init__(self, *errors: BaseException | None) -> None:
        self.sent: list[tuple[Reminder, str]] = []
        self.errors = list(errors)

    async def __call__(self, item: Reminder, text: str) -> None:
        error = self.errors.pop(0) if self.errors else None
        if error is not None:
            raise error
        self.sent.append((item, text))


def deliver(store: Store, send: Sender, now: datetime = NOW) -> bool:
    return asyncio.run(deliver_due(store, send, now))


@pytest.mark.parametrize(
    ("text", "seconds"), [("30s", 30), ("2M", 120), ("3h", 10800), ("4d", 345600), ("365d", 31536000)]
)
def test_parse_delay(text: str, seconds: int):
    assert parse_delay(text) == seconds


@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("2w", BAD_UNIT),
        ("soon", BAD_UNIT),
        ("xs", BAD_UNIT),
        ("s", BAD_UNIT),
        ("1.5h", BAD_UNIT),
        ("31557601s", TOO_FAR),
        ("366d", TOO_FAR),
        ("0s", PAST),
        ("-1h", PAST),
    ],
)
def test_parse_delay_failures(text: str, reply: str):
    with pytest.raises(InvalidDelayError) as info:
        parse_delay(text)
    assert str(info.value) == reply


def test_parse_delay_limit_is_inclusive():
    assert parse_delay("31557600s") == 31557600


def test_render():
    assert render(NOW) == f"<t:{NOW_TS}:f>"


@pytest.mark.parametrize("text", ["", "help", "1h", "help me now"])
def test_help(store: Store, text: str):
    assert remind(store, text) == HELP_TEXT
    assert remind_command(store, make_dm(), text.split(), NOW) == HELP_TEXT


def test_help_text_wording():
    assert HELP_TEXT == (
        "Format: $remind [amount of time] [message]. Example: $remind 1h check laundry\n"
        "Support units: s (seconds), m (minutes), h (hours), or d (days)."
    )


def test_list_empty(store: Store):
    assert remind(store, "list") == "No active reminders were found"


def test_list_shows_this_guilds_reminders_in_order(store: Store):
    store.state.reminders = [
        reminder("first", NOW + timedelta(hours=1), user_id=7),
        reminder("elsewhere", guild_id=GUILD + 1),
        reminder("second", NOW),
    ]
    later = NOW_TS + 3600
    assert remind(store, "list") == (
        f"\nCreated by: <@7>\nReminder date: <t:{later}:f>\nReminder message: first\n"
        f"\nCreated by: <@1>\nReminder date: <t:{NOW_TS}:f>\nReminder message: second\n"
    )


def test_create_persists_the_exact_record_and_reply(store: Store):
    reply = remind(store, "10m check  the oven", channel_id=999)
    due = NOW + timedelta(minutes=10)
    assert reply == f"Created reminder for <@1> to go off at <t:{NOW_TS + 600}:f> that says `check the oven`"
    expected = Reminder(guild_id=GUILD, channel_id=999, user_id=AUTHOR, message="check the oven", due=due)
    assert store.state.reminders == [expected]
    assert Store(store.path).state.reminders == [expected]
    assert json.loads(store.path.read_text())["reminders"][0]["due"] == "2026-06-03T14:26:47Z"


@pytest.mark.parametrize("text", ["list", "1h check", "bad check"])
def test_dm_refusals(store: Store, text: str):
    assert remind_command(store, make_dm(), text.split(), NOW) == "Reminders only work in a server."
    assert not store.path.exists()


def test_limit(store: Store):
    store.state.reminders = [reminder() for _ in range(MAX_REMINDERS_PER_GUILD - 1)]
    assert MAX_REMINDERS_PER_GUILD == 100
    assert remind(store, "1h last").startswith("Created reminder")
    before = store.path.read_bytes()
    assert remind(store, "1h over") == (
        "Reminder limit reached. Wait for a reminder to complete before creating a new one. Limit: 100"
    )
    assert store.path.read_bytes() == before
    assert len(store.state.reminders) == 100
    assert remind(store, "1h other", guild_id=GUILD + 1).startswith("Created reminder")


@pytest.mark.parametrize(("text", "reply"), [("2w x", BAD_UNIT), ("400d x", TOO_FAR), ("0s x", PAST)])
def test_refused_create_writes_nothing(store: Store, text: str, reply: str):
    assert remind(store, text) == reply
    assert store.state.reminders == []
    assert not store.path.exists()


def test_nothing_due_sends_nothing(store: Store):
    store.state.reminders = [reminder(due=NOW + timedelta(seconds=1))]
    send = Sender()
    assert deliver(store, send) is True
    assert send.sent == []
    assert len(store.state.reminders) == 1


def test_due_reminder_is_sent_then_removed_from_the_file(store: Store):
    store.state.reminders = [reminder("check the oven"), reminder("later", NOW + timedelta(hours=1))]
    store.save()
    send = Sender()
    assert deliver(store, send) is True
    assert send.sent == [(reminder("check the oven"), "<@1> reminder: check the oven")]
    assert [r.message for r in Store(store.path).state.reminders] == ["later"]


@pytest.mark.parametrize(("late", "noted"), [(59, False), (60, True), (3600, True)])
def test_late_note(store: Store, late: int, *, noted: bool):
    store.state.reminders = [reminder("x", NOW - timedelta(seconds=late))]
    send = Sender()
    deliver(store, send)
    due_ts = NOW_TS - late
    expected = f"<@1> reminder (was due <t:{due_ts}:f>): x" if noted else "<@1> reminder: x"
    assert send.sent[0][1] == expected


def test_identical_reminders_each_deliver_once(store: Store):
    store.state.reminders = [reminder("same"), reminder("same")]
    send = Sender()
    assert deliver(store, send) is True
    assert len(send.sent) == 2
    assert store.state.reminders == []
    assert deliver(store, send) is True
    assert len(send.sent) == 2


def test_transient_error_keeps_the_reminder_for_the_next_pass(store: Store, caplog: pytest.LogCaptureFixture):
    store.state.reminders = [reminder("a"), reminder("b")]
    send = Sender(RuntimeError("503"), None)
    assert deliver(store, send) is False
    assert [r.message for r in store.state.reminders] == ["a"]
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert deliver(store, send) is True
    assert [item.message for item, _ in send.sent] == ["b", "a"]
    assert store.state.reminders == []


def test_permanent_error_drops_the_reminder(store: Store):
    store.state.reminders = [reminder("gone")]
    store.save()
    send = Sender(PermanentDeliveryError("404"))
    assert deliver(store, send) is True
    assert store.state.reminders == []
    assert Store(store.path).state.reminders == []
    assert deliver(store, send) is True
    assert send.sent == []


def test_cancellation_during_a_send_keeps_the_reminder(store: Store):
    store.state.reminders = [reminder("stay")]
    send = Sender(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        deliver(store, send)
    assert [r.message for r in store.state.reminders] == ["stay"]


def test_failed_save_after_a_send_still_removes_it_from_memory(
    store: Store, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    store.state.reminders = [reminder("once")]

    def fail() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", fail)
    send = Sender()
    assert deliver(store, send) is True
    assert store.state.reminders == []
    assert "Could not save state after delivering a reminder" in caplog.messages
    deliver(store, send)
    assert len(send.sent) == 1


def test_overdue_reminders_from_an_existing_file_deliver_on_the_first_pass(store: Store):
    store.state.reminders = [reminder("old", datetime(2026, 1, 1, tzinfo=UTC))]
    store.save()
    reloaded = Store(store.path)
    send = Sender()
    assert deliver(reloaded, send) is True
    assert send.sent[0][1] == f"<@1> reminder (was due <t:{int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())}:f>): old"
    assert Store(store.path).state.reminders == []
