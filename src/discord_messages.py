"""Centralized Discord message sending policies."""

from __future__ import annotations

import discord

from message_limits import truncate_discord_message

NO_MENTIONS = discord.AllowedMentions.none()


def reminder_allowed_mentions(user_id: int) -> discord.AllowedMentions:
    """Allow only the reminder recipient mention."""
    return discord.AllowedMentions(
        everyone=False,
        users=[discord.Object(id=user_id)],
        roles=False,
        replied_user=False,
    )


async def send_command_response(channel: discord.abc.Messageable, content: str) -> None:
    """Send a bot command response without allowing any mentions."""
    await channel.send(truncate_discord_message(content), allowed_mentions=NO_MENTIONS)


async def send_reminder_response(channel: discord.abc.Messageable, user_id: int, content: str) -> None:
    """Send a reminder response that can mention only the reminder recipient."""
    await channel.send(
        truncate_discord_message(f"<@{user_id}> reminder: {content}"),
        allowed_mentions=reminder_allowed_mentions(user_id),
    )
