"""The Discord adapter, and the only module that knows about discord.py."""

import logging
from typing import Final, override

import discord

from smurfbot.app import Application
from smurfbot.core import GroupRef, InboundMessage
from smurfbot.reminders import PermanentDeliveryError
from smurfbot.tags import UserLookupError
lazy from smurfbot.config import Settings
lazy from smurfbot.reminders.models import Reminder

NO_MENTIONS: Final = discord.AllowedMentions.none()
SERVER_ERROR_STATUS: Final = 500
RATE_LIMITED_STATUS: Final = 429

_logger = logging.getLogger(__name__)


def build_intents() -> discord.Intents:
    """Return the minimal intents needed to read commands."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.dm_messages = True
    intents.message_content = True
    return intents


def reminder_mentions(user_id: int) -> discord.AllowedMentions:
    """Allow a reminder to ping its recipient and nobody else."""
    return discord.AllowedMentions(
        everyone=False,
        users=[discord.Object(id=user_id)],
        roles=False,
        replied_user=False,
    )


def private_channel_name(channel: object) -> str:
    """Return a display name for a DM or group DM, which may be only partly cached."""
    if name := getattr(getattr(channel, "recipient", None), "name", None):
        return f"DM: {name}"
    if name := getattr(channel, "name", None):
        return f"DM: {name}"
    if recipients := getattr(channel, "recipients", None):
        return f"DM: {', '.join(recipient.name for recipient in recipients)}"
    return f"DM: {getattr(channel, 'id', 'unknown')}"


def to_inbound(message: discord.Message) -> InboundMessage:
    """Translate a discord.py message into the bot's own type."""
    guild = message.guild
    permissions = getattr(message.author, "guild_permissions", None)

    async def reply(content: str) -> None:
        await message.channel.send(content, allowed_mentions=NO_MENTIONS)

    return InboundMessage(
        content=message.content,
        author_id=message.author.id,
        author_name=message.author.name,
        author_is_bot=message.author.bot,
        group=GroupRef(guild.id, guild.name)
        if guild is not None
        else GroupRef(message.channel.id, private_channel_name(message.channel)),
        is_guild=guild is not None,
        can_manage_group=guild is None or bool(permissions and (permissions.administrator or permissions.manage_guild)),
        channel_id=message.channel.id,
        attachment_url=message.attachments[0].url if message.attachments else None,
        reply=reply,
    )


class DiscordGateway(discord.Client):
    """Forward Discord events to the application and carry its messages back."""

    def __init__(self, settings: Settings) -> None:
        """Build the client and the application it drives."""
        super().__init__(intents=build_intents())
        self._app = Application(
            data_dir=settings.data_dir,
            prefix=settings.delim,
            send_reminder=self._send_reminder,
            fetch_user_name=self._fetch_user_name,
        )

    @override
    async def setup_hook(self) -> None:
        await self._app.start()

    async def on_ready(self) -> None:
        """Reconcile state; Discord repeats this event after long reconnects."""
        _logger.info("Logged in as %s", self.user)
        await self._app.sync(
            [GroupRef(guild.id, guild.name) for guild in self.guilds if not guild.unavailable],
            [GroupRef(channel.id, private_channel_name(channel)) for channel in self.private_channels],
        )
        _logger.info("Ready")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Start serving a guild the bot was added to."""
        await self._app.join(GroupRef(guild.id, guild.name))

    async def on_guild_available(self, guild: discord.Guild) -> None:
        """Start serving a guild that was down when the bot connected."""
        await self._app.join(GroupRef(guild.id, guild.name))

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Stop serving a guild the bot left."""
        await self._app.leave(guild.id)

    async def on_message(self, message: discord.Message) -> None:
        """Hand one message to the command router."""
        await self._app.router.handle(to_inbound(message))

    @override
    async def close(self) -> None:
        await self._app.stop()
        await super().close()

    async def _send_reminder(self, reminder: Reminder, content: str) -> None:
        """Send through a partial channel, which reaches threads and needs no cache."""
        channel = self.get_partial_messageable(reminder.channel_id, guild_id=reminder.guild_id)
        try:
            await channel.send(content, allowed_mentions=reminder_mentions(reminder.user_id))
        except discord.HTTPException as error:
            if error.status < SERVER_ERROR_STATUS and error.status != RATE_LIMITED_STATUS:
                raise PermanentDeliveryError(str(error)) from error
            raise

    async def _fetch_user_name(self, user_id: int) -> str:
        try:
            return (await self.fetch_user(user_id)).name
        except discord.HTTPException as error:
            raise UserLookupError(str(error)) from error
