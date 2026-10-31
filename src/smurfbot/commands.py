"""Stateless chat commands."""

from typing import Final

from smurfbot.core import CommandContext, CommandError, CommandHandler

SOURCE_URL: Final = "https://github.com/nithjino/SmurfBot"
HELP_TEXT: Final = (
    "The commands are: tag, git, mock, remind, activate, and deactivate. Tag and remind have their own help commands."
)


async def ping(_context: CommandContext) -> str:
    """Show that the bot is alive."""
    return "pong"


async def git(_context: CommandContext) -> str:
    """Link the source repository."""
    return f"Here is the source code: {SOURCE_URL}"


async def post_help(_context: CommandContext) -> str:
    """List the top-level commands."""
    return HELP_TEXT


async def mock(context: CommandContext) -> str:
    """Repeat the message in alternating case, skipping over whitespace."""
    if not context.args:
        raise CommandError("Usage: mock [message]")
    result: list[str] = []
    uppercase = True
    for character in " ".join(context.args).lower():
        if character.isspace():
            result.append(character)
            continue
        result.append(character.upper() if uppercase else character)
        uppercase = not uppercase
    return "".join(result)


BASIC_COMMANDS: Final[frozendict[str, CommandHandler]] = frozendict(
    {"ping": ping, "git": git, "help": post_help, "mock": mock}  # pyright: ignore[reportCallIssue]
)
