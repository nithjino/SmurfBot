"""The remind command and reminder delivery."""

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from smurfbot.store import Reminder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from smurfbot.core import Request
    from smurfbot.store import Store

log = logging.getLogger(__name__)

MAX_REMINDERS_PER_GUILD = 100
MAX_DELAY_SECONDS = 31_557_600
LATE_AFTER = timedelta(seconds=60)
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
MIN_ARGS = 2

HELP_TEXT = (
    "Format: $remind [amount of time] [message]. Example: $remind 1h check laundry\n"
    "Support units: s (seconds), m (minutes), h (hours), or d (days)."
)
DM_REPLY = "Reminders only work in a server."
LIMIT_REPLY = (
    "Reminder limit reached. Wait for a reminder to complete before creating a new one. "
    f"Limit: {MAX_REMINDERS_PER_GUILD}"
)
BAD_UNIT_REPLY = "Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)"
TOO_FAR_REPLY = "Why are you using this feature for a reminder that far in the future?"
PAST_REPLY = "Why are you trying to set a reminder for the past?"


class InvalidDelayError(ValueError):
    """A delay could not be used; the message is the reply."""


class PermanentDeliveryError(Exception):
    """A reminder can never be delivered, so it should be dropped."""


def parse_delay(text: str) -> int:
    """Return the delay in seconds for text such as 10m, or raise InvalidDelayError."""
    unit = UNITS.get(text[-1:].lower())
    try:
        amount = int(text[:-1])
    except ValueError:
        amount = None
    if unit is None or amount is None:
        raise InvalidDelayError(BAD_UNIT_REPLY)
    seconds = amount * unit
    if seconds > MAX_DELAY_SECONDS:
        raise InvalidDelayError(TOO_FAR_REPLY)
    if seconds <= 0:
        raise InvalidDelayError(PAST_REPLY)
    return seconds


def render(when: datetime) -> str:
    """Return a Discord timestamp that each reader sees in their own timezone."""
    return f"<t:{int(when.timestamp())}:f>"


def remind_command(store: Store, request: Request, args: list[str], now: datetime) -> str:
    """Run the remind command and return the reply."""
    if args and args[0] == "list":
        return DM_REPLY if request.guild_id is None else _list(store, request.guild_id)
    if len(args) < MIN_ARGS or args[0] == "help":
        return HELP_TEXT
    if request.guild_id is None:
        return DM_REPLY
    try:
        delay = parse_delay(args[0])
    except InvalidDelayError as exc:
        return str(exc)
    reminders = store.state.reminders
    if sum(reminder.guild_id == request.guild_id for reminder in reminders) >= MAX_REMINDERS_PER_GUILD:
        return LIMIT_REPLY
    reminder = Reminder(
        guild_id=request.guild_id,
        channel_id=request.channel_id,
        user_id=request.author_id,
        message=" ".join(args[1:]),
        due=now + timedelta(seconds=delay),
    )
    reminders.append(reminder)
    store.save()
    return (
        f"Created reminder for <@{reminder.user_id}> to go off at {render(reminder.due)} that says `{reminder.message}`"
    )


def _list(store: Store, guild_id: int) -> str:
    blocks = [
        f"\nCreated by: <@{reminder.user_id}>\nReminder date: {render(reminder.due)}\n"
        f"Reminder message: {reminder.message}\n"
        for reminder in store.state.reminders
        if reminder.guild_id == guild_id
    ]
    return "".join(blocks) or "No active reminders were found"


def delivery_text(reminder: Reminder, now: datetime) -> str:
    """Return the message that delivers a reminder, noting when it is late."""
    if now - reminder.due >= LATE_AFTER:
        return f"<@{reminder.user_id}> reminder (was due {render(reminder.due)}): {reminder.message}"
    return f"<@{reminder.user_id}> reminder: {reminder.message}"


async def deliver_due(store: Store, send: Callable[[Reminder, str], Awaitable[None]], now: datetime) -> bool:
    """Send every due reminder once; return False if any delivery failed."""
    delivered_all = True
    for reminder in [reminder for reminder in store.state.reminders if reminder.due <= now]:
        try:
            await send(reminder, delivery_text(reminder, now))
        except PermanentDeliveryError:
            log.warning("Dropping undeliverable reminder for channel %s", reminder.channel_id, exc_info=True)
        except Exception:
            log.warning("Could not deliver reminder to channel %s; will retry", reminder.channel_id, exc_info=True)
            delivered_all = False
            continue
        store.state.reminders = [other for other in store.state.reminders if other is not reminder]
        try:
            store.save()
        except OSError:
            log.exception("Could not save state after delivering a reminder")
    return delivered_all
