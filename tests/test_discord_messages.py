"""Tests for centralized Discord message sending policies."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from discord_messages import send_command_response, send_reminder_response
from message_limits import DISCORD_MESSAGE_LIMIT

if TYPE_CHECKING:
    from collections.abc import Coroutine


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


class RecordingChannel:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.sent_kwargs: list[dict[str, object]] = []

    async def send(self, message: str, **kwargs: object) -> None:
        self.sent_messages.append(message)
        self.sent_kwargs.append(kwargs)


def test_command_response_disables_all_mentions_and_truncates() -> None:
    channel = RecordingChannel()
    content = "@everyone " + ("a" * DISCORD_MESSAGE_LIMIT)

    run(send_command_response(channel, content))

    assert len(channel.sent_messages[0]) == DISCORD_MESSAGE_LIMIT
    assert channel.sent_kwargs[0]["allowed_mentions"].to_dict() == {"parse": []}


def test_reminder_response_allows_only_recipient_user_mention() -> None:
    channel = RecordingChannel()

    run(send_reminder_response(channel, 123, "@everyone <@456> check laundry"))

    assert channel.sent_messages == ["<@123> reminder: @everyone <@456> check laundry"]
    assert channel.sent_kwargs[0]["allowed_mentions"].to_dict() == {"users": [123], "parse": []}
