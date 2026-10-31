"""Turn chat messages into command invocations and send back their replies."""

import logging
from typing import Final
lazy from collections.abc import Mapping

from smurfbot.core import CommandContext, CommandError, CommandHandler, InboundMessage
from smurfbot.text import truncate
lazy from smurfbot.activation import GroupActivation

GENERIC_COMMAND_ERROR: Final = "Sorry, something went wrong while handling that command."
HELP_COMMAND: Final = "help"
ACTIVATE_COMMAND: Final = "activate"
DEACTIVATE_COMMANDS: Final = frozenset({"deactivate", "deactive"})

_logger = logging.getLogger(__name__)


class CommandRouter:
    """Apply activation rules, run one handler, and contain its failures."""

    def __init__(self, prefix: str, activation: GroupActivation, commands: Mapping[str, CommandHandler]) -> None:
        """Bind the command prefix and the handler table."""
        if not prefix:
            msg = "prefix must not be empty"
            raise ValueError(msg)
        self._prefix = prefix
        self._activation = activation
        self._commands = commands

    async def handle(self, message: InboundMessage) -> None:
        """Process one chat message; a bad command never escapes as an exception."""
        if message.author_is_bot or not message.content.startswith(self._prefix):
            return

        name, *args = message.content.removeprefix(self._prefix).split() or [HELP_COMMAND]
        name = name.lower()
        try:
            reply = await self._execute(message, name, tuple(args))
        except CommandError as error:
            reply = error.message
        except Exception as error:
            self._log_failure("handle", name, message, error)
            reply = GENERIC_COMMAND_ERROR

        if not reply:
            return
        try:
            await message.reply(truncate(reply))
        except Exception as error:
            self._log_failure("send response for", name, message, error)

    async def _execute(self, message: InboundMessage, name: str, args: tuple[str, ...]) -> str | None:
        """Run a command unless the group is deactivated; only activate gets past that."""
        if name == ACTIVATE_COMMAND:
            return await self._set_activation(message, enabled=True)
        if not await self._activation.is_enabled(message.group):
            return None
        if name in DEACTIVATE_COMMANDS:
            return await self._set_activation(message, enabled=False)

        handler = self._commands.get(name)
        if handler is None:
            _logger.info("Ignoring unknown command: %s", name)
            return None

        _logger.info("Running %s in group %s for author %s", name, message.group.id, message.author_id)
        return await handler(
            CommandContext(
                args=args,
                author_id=message.author_id,
                author_name=message.author_name,
                guild=message.group if message.is_guild else None,
                channel_id=message.channel_id,
                attachment_url=message.attachment_url,
            )
        )

    async def _set_activation(self, message: InboundMessage, *, enabled: bool) -> str:
        if not message.can_manage_group:
            raise CommandError("Only server admins can activate or deactivate this bot.")
        await self._activation.set_enabled(message.group, enabled=enabled)
        return f"{message.group.name} has been {'activated' if enabled else 'deactivated'}."

    @staticmethod
    def _log_failure(action: str, name: str, message: InboundMessage, error: Exception) -> None:
        _logger.error(
            "Failed to %s command %s in group %s channel %s author %s",
            action,
            name,
            message.group.id,
            message.channel_id,
            message.author_id,
            exc_info=error,
        )
