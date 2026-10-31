"""Transport-neutral types shared by the router and every command."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GroupRef:
    """A Discord guild or private conversation, identified by snowflake."""

    id: int
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class InboundMessage:
    """One incoming chat message, already translated from the transport's types."""

    content: str
    author_id: int
    author_name: str
    author_is_bot: bool
    group: GroupRef
    is_guild: bool
    can_manage_group: bool
    channel_id: int
    attachment_url: str | None
    reply: Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandContext:
    """What a command handler knows about its invocation."""

    args: tuple[str, ...]
    author_id: int
    author_name: str
    guild: GroupRef | None
    channel_id: int
    attachment_url: str | None = None


type CommandHandler = Callable[[CommandContext], Awaitable[str]]


class CommandError(Exception):
    """A refused command whose message is meant to be shown in chat."""

    def __init__(self, message: str) -> None:
        """Keep the chat-safe message."""
        super().__init__(message)
        self.message = message
