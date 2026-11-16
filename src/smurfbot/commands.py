"""Prefix parsing, the activation gate, and the stateless commands."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from smurfbot.core import Request, utc_now
from smurfbot.reminders import remind_command
from smurfbot.tags import tag_command

if TYPE_CHECKING:
    from datetime import datetime

    from smurfbot.store import Store

log = logging.getLogger(__name__)

GIT_REPLY = "Here is the source code: https://github.com/nithjino/SmurfBot"
HELP_REPLY = (
    "The commands are: tag, git, mock, remind, activate, and deactivate. Tag and remind have their own help commands."
)
NOT_ADMIN_REPLY = "Only server admins can activate or deactivate this bot."

type Handler = Callable[[Request, list[str]], str]


def mock(args: list[str]) -> str:
    """Return the words in alternating case, starting with uppercase."""
    if not args:
        return "Usage: mock [message]"
    chars: list[str] = []
    upper = True
    for char in " ".join(args).lower():
        if char.isspace():
            chars.append(char)
            continue
        chars.append(char.upper() if upper else char)
        upper = not upper
    return "".join(chars)


class Commands:
    """Turns a request into a reply."""

    def __init__(self, store: Store, prefix: str, now: Callable[[], datetime] = utc_now) -> None:
        """Bind the commands to a store, a prefix, and a clock."""
        self.prefix = prefix
        self._store = store
        self._now = now
        self._handlers: dict[str, Handler] = {
            "ping": lambda _request, _args: "pong",
            "git": lambda _request, _args: GIT_REPLY,
            "help": lambda _request, _args: HELP_REPLY,
            "mock": lambda _request, args: mock(args),
            "tag": lambda request, args: tag_command(self._store, request, args),
            "remind": lambda request, args: remind_command(self._store, request, args, self._now()),
        }

    def parse(self, text: str) -> tuple[str, list[str]] | None:
        """Split prefixed text into a lowercased command name and its arguments."""
        if not text.startswith(self.prefix):
            return None
        tokens = text[len(self.prefix) :].split()
        if not tokens:
            return "help", []
        return tokens[0].lower(), tokens[1:]

    def handle(self, request: Request) -> str | None:
        """Return the reply to a request, or None when the bot stays silent."""
        parsed = self.parse(request.text)
        if parsed is None:
            return None
        name, args = parsed
        if name == "activate":
            return self._set_active(request, active=True)
        if request.group_id in self._store.state.deactivated:
            return None
        if name in {"deactivate", "deactive"}:
            return self._set_active(request, active=False)
        handler = self._handlers.get(name)
        if handler is None:
            log.info("Ignoring unknown command: %s", name)
            return None
        return handler(request, args)

    def _set_active(self, request: Request, *, active: bool) -> str:
        if not request.is_admin:
            return NOT_ADMIN_REPLY
        if active:
            self._store.state.deactivated.discard(request.group_id)
        else:
            self._store.state.deactivated.add(request.group_id)
        self._store.save()
        return f"{request.group_name} has been {'activated' if active else 'deactivated'}."
