"""The `remind` chat command."""

from typing import Final

from smurfbot.core import CommandContext, CommandError, GroupRef
from smurfbot.reminders.book import ReminderBook, format_time
lazy from smurfbot.registry import GuildRegistry

HELP_TEXT: Final = (
    "Format: $remind [amount of time] [message]. Example: $remind 1h check laundry\nSupport units: "
    "s (seconds), m (minutes), h (hours), or d (days)."
)
GUILD_ONLY: Final = "Reminders only work in a server."
UNIT_SECONDS: Final = frozendict({"s": 1, "m": 60, "h": 3_600, "d": 86_400})  # pyright: ignore[reportCallIssue]
MAX_REMINDER_SECONDS: Final = 31_557_600
MIN_CREATE_ARGUMENTS: Final = 2


def parse_delay(text: str) -> int:
    """Convert a compact duration such as 5m to seconds, refusing unusable ones."""
    multiplier = UNIT_SECONDS.get(text[-1:].lower())
    try:
        amount = int(text[:-1])
    except ValueError:
        amount = None
    if multiplier is None or amount is None:
        raise CommandError("Unsupported unit of time. Please use s (seconds), m (minutes), h (hours), or d (days)")
    seconds = amount * multiplier
    if seconds > MAX_REMINDER_SECONDS:
        raise CommandError("Why are you using this feature for a reminder that far in the future?")
    if seconds <= 0:
        raise CommandError("Why are you trying to set a reminder for the past?")
    return seconds


class RemindCommand:
    """Parse `remind` arguments and word the replies; the rules live in ReminderBook."""

    def __init__(self, books: GuildRegistry[ReminderBook]) -> None:
        """Bind the guild registry."""
        self._books = books

    async def __call__(self, context: CommandContext) -> str:
        """List reminders, explain the format, or create a reminder."""
        args = context.args
        if args[:1] == ("list",):
            return await self._list(self._require_guild(context))
        if len(args) < MIN_CREATE_ARGUMENTS or args[0] == "help":
            return HELP_TEXT
        return await self._create(context, args[0], " ".join(args[1:]))

    @staticmethod
    def _require_guild(context: CommandContext) -> GroupRef:
        if context.guild is None:
            raise CommandError(GUILD_ONLY)
        return context.guild

    async def _list(self, guild: GroupRef) -> str:
        reminders = (await self._books.get(guild)).pending
        if not reminders:
            return "No active reminders were found"
        return "".join(
            f"\nCreated by: {reminder.name}\nReminder date: {format_time(reminder.execution_time)}\n"
            f"Reminder message: {reminder.message}\n"
            for reminder in reminders
        )

    async def _create(self, context: CommandContext, duration: str, message: str) -> str:
        guild = self._require_guild(context)
        delay_seconds = parse_delay(duration)
        name = context.author_name or str(context.author_id)
        reminder = await (await self._books.get(guild)).add(
            user_id=context.author_id,
            name=name,
            message=message,
            channel_id=context.channel_id,
            delay_seconds=delay_seconds,
        )
        return (
            f"Created reminder for {name} to go off at {format_time(reminder.execution_time)} "
            f"that says `{reminder.message}`"
        )
