"""The Discord client: translates messages, sends replies, and delivers reminders."""

import asyncio
import logging
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord

from smurfbot.commands import Commands
from smurfbot.core import Request
from smurfbot.reminders import PermanentDeliveryError, deliver_due

if TYPE_CHECKING:
    from smurfbot.store import Reminder, Store

log = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000
ERROR_REPLY = "Sorry, something went wrong while handling that command."
PERMANENT_STATUSES = frozenset({400, 403, 404})
IDLE_SECONDS = 1
RETRY_SECONDS = 60


def make_intents() -> discord.Intents:
    """Return the gateway intents the bot needs and nothing more."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.dm_messages = True
    intents.message_content = True
    return intents


def to_request(message: discord.Message) -> Request:
    """Translate a Discord message into a Request."""
    guild = message.guild
    attachment_url = message.attachments[0].url if message.attachments else None
    if guild is None:
        return Request(
            text=message.content,
            author_id=message.author.id,
            guild_id=None,
            group_id=message.channel.id,
            group_name="This DM",
            channel_id=message.channel.id,
            is_admin=True,
            attachment_url=attachment_url,
        )
    permissions: discord.Permissions | None = getattr(message.author, "guild_permissions", None)
    return Request(
        text=message.content,
        author_id=message.author.id,
        guild_id=guild.id,
        group_id=guild.id,
        group_name=guild.name,
        channel_id=message.channel.id,
        is_admin=permissions is not None and (permissions.administrator or permissions.manage_guild),
        attachment_url=attachment_url,
    )


def split_reply(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text into parts of at most limit characters, preferring to break at whitespace."""
    parts: list[str] = []
    while len(text) > limit:
        window = text[: limit + 1]
        cut = next((index for index in range(limit, 0, -1) if window[index].isspace()), 0)
        if cut:
            parts.append(text[:cut])
            text = text[cut + 1 :]
        else:
            parts.append(text[:limit])
            text = text[limit:]
    if text:
        parts.append(text)
    return parts


class SmurfBot(discord.Client):
    """The Discord client for SmurfBot."""

    def __init__(self, store: Store, prefix: str) -> None:
        """Create a client that answers commands against store."""
        super().__init__(intents=make_intents())
        self._store = store
        self._commands = Commands(store, prefix)
        self._reminder_task: asyncio.Task[None] | None = None
        self._sigterm_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        """Start the reminder loop and make SIGTERM close the client."""
        self._reminder_task = asyncio.create_task(self._reminder_loop())
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, self._on_sigterm)

    async def close(self) -> None:
        """Stop the reminder loop, then close the connection."""
        if self._reminder_task is not None:
            self._reminder_task.cancel()
        await super().close()

    def _on_sigterm(self) -> None:
        log.info("Received SIGTERM, shutting down")
        self._sigterm_task = asyncio.create_task(self.close())

    async def on_message(self, message: discord.Message) -> None:
        """Answer a command message."""
        if message.author.bot:
            return
        request = to_request(message)
        try:
            reply = self._commands.handle(request)
        except Exception:
            parsed = self._commands.parse(request.text)
            log.exception(
                "Command %s failed in group %s, channel %s, author %s",
                parsed[0] if parsed else None,
                request.group_id,
                request.channel_id,
                request.author_id,
            )
            reply = ERROR_REPLY
        if reply is None:
            return
        try:
            for part in split_reply(reply):
                await message.channel.send(part, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            log.exception("Could not send a reply to channel %s", request.channel_id)

    async def _send_reminder(self, reminder: Reminder, text: str) -> None:
        """Send a reminder to its channel, mentioning only its user."""
        channel = self.get_partial_messageable(reminder.channel_id)
        mentions = discord.AllowedMentions(
            everyone=False, users=[discord.Object(id=reminder.user_id)], roles=False, replied_user=False
        )
        try:
            await channel.send(text[:MAX_MESSAGE_LENGTH], allowed_mentions=mentions)
        except discord.HTTPException as exc:
            if exc.status in PERMANENT_STATUSES:
                raise PermanentDeliveryError(f"Discord refused the reminder with status {exc.status}") from exc
            raise

    async def _reminder_loop(self) -> None:
        await self.wait_until_ready()
        while True:
            try:
                delivered = await deliver_due(self._store, self._send_reminder, datetime.now(UTC))
            except Exception:
                log.exception("Reminder delivery pass failed")
                delivered = False
            await asyncio.sleep(IDLE_SECONDS if delivered else RETRY_SECONDS)
