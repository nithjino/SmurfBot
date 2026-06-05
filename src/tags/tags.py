"""Handle all tag-related commands."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

import discord
from atomicwrites import atomic_write

from message_limits import DISCORD_MESSAGE_LIMIT, truncate_discord_message, truncate_text

from .handlers import TAG_COMMAND_HANDLERS
from .models import TagCommandParameters, TagContent, TagFile, TagRecord

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_logger = logging.getLogger(__name__)

MAX_TAGS_PER_GUILD: Final[int] = 200
RESERVED_TAG_NAMES: Final[frozenset[str]] = frozenset(TAG_COMMAND_HANDLERS)


def reserved_tag_name_message(name: str) -> str:
    """Return the validation message for tag names reserved by subcommands."""
    return f'The tag name "{name}" is reserved for a tag command'


class Tags:
    """Handle all tag-related commands."""

    def __init__(self, guild: discord.Guild, tags_json_path: str | Path) -> None:
        """Initialize paths and default tag state for a guild."""
        self.guild = guild
        self.tags_json_path = Path(tags_json_path)
        self.tags_json_file = self.tags_json_path / f"{guild.id}.json"
        self.tags = TagFile(name=guild.name, id=guild.id)
        self._tag_lock = asyncio.Lock()

    @classmethod
    async def create(cls, guild: discord.Guild, tags_json_path: str | Path) -> Tags:
        """Create a tag handler and load persisted tags without blocking the event loop."""
        tag_handler = cls(guild, tags_json_path)
        tag_handler.tags = await tag_handler.load_tags()
        return tag_handler

    @staticmethod
    async def post_help() -> str:
        """Return help text for tag commands."""
        return (
            'To create a tag: "tag create [name]" while uploading a picture or by providing a link or text after '
            'the name. To post an already created tag: "tag [name]". To see what tags are made: "tag list".'
            ' To edit a tag: "tag edit [name] [new material]". To rename a tag:"tag rename [current name]'
            ' [new name]".'
        )

    async def create_json(self) -> None:
        """Create the tags directory and guild JSON file when missing."""

        def create_json_sync() -> None:
            if not self.tags_json_path.exists():
                self.tags_json_path.mkdir(parents=True)
                _logger.info("created %s", self.tags_json_path)
            # creates tag json file for the group if it doesn't exist
            if not self.tags_json_file.exists():
                create_json = TagFile(name=self.guild.name, id=self.guild.id)
                with self.tags_json_file.open("w", encoding="utf-8") as tags_file:
                    json.dump(create_json.model_dump(mode="json"), tags_file)
                _logger.info("%s: created: %s", self.guild.name, self.tags_json_file)

        await asyncio.to_thread(create_json_sync)

    async def load_tags(self) -> TagFile:
        """Return the guild's tag JSON data."""
        await self.create_json()

        def load_tags_sync() -> TagFile:
            _logger.info("%s: loading: %s", self.guild.name, self.tags_json_file)
            with self.tags_json_file.open(encoding="utf-8") as tags_file:
                return TagFile.model_validate(json.load(tags_file))

        return await asyncio.to_thread(load_tags_sync)

    async def save_tags(self) -> None:
        """Write the guild's tags to disk."""
        async with self._tag_lock:
            await self._save_tags_unlocked()

    async def _save_tags_unlocked(self) -> None:
        """Write tags to disk while the caller holds the tag lock."""

        def save_tags_sync() -> None:
            _logger.info("%s: saving: %s", self.guild.name, self.tags_json_file)
            with atomic_write(self.tags_json_file, overwrite=True, encoding="utf-8") as tag_file:
                tag_file.write(json.dumps(self.tags.model_dump(mode="json"), sort_keys=True, indent=2))

        await asyncio.to_thread(save_tags_sync)

    async def parse_commands(self, parameters: object) -> str:
        """Dispatch a tag subcommand using parsed message parameters."""
        parameters = TagCommandParameters.model_validate(parameters)
        _logger.info("tags parse_commands parameters: %s", parameters)
        if not parameters.message:
            return await self.post_help()

        command = parameters.message[0].rstrip()
        message = parameters.message[1:]
        handler = TAG_COMMAND_HANDLERS.get(command)
        if handler is None:
            return await self.post_tag(command.strip())

        return await handler(self, parameters, message)

    async def create_tag(self, name: str, content: TagContent, owner: int) -> str:
        """Create a tag owned by the requesting user."""
        async with self._tag_lock:
            if name in RESERVED_TAG_NAMES:
                return reserved_tag_name_message(name)
            if name in self.tags.tags:
                return f'The tag "{name}" already exists'
            if len(self.tags.tags) >= MAX_TAGS_PER_GUILD:
                return f"Tag limit reached. Delete a tag before creating a new one. Limit: {MAX_TAGS_PER_GUILD}"
            tag_content = content.attachment if content.attachment is not None else content.message.strip()
            if not tag_content:
                return "A tag needs either text content or an attachment"
            tag_content = truncate_text(tag_content, DISCORD_MESSAGE_LIMIT)
            self.tags.tags[name] = TagRecord(owner=owner, content=tag_content)
            await self._save_tags_unlocked()
            return f'The tag "{name}" was created successfully'

    async def post_tag(self, name: str) -> str:
        """Return the content for a saved tag."""
        async with self._tag_lock:
            if name in self.tags.tags:
                return truncate_discord_message(self.tags.tags[name].content)
        return f'The tag "{name}" does not exist'

    async def delete_tag(self, name: str, owner: int) -> str:
        """Delete a tag when requested by its owner."""
        async with self._tag_lock:
            if not self.tags.tags:
                return "There are no tags"

            if name in self.tags.tags:
                if self.tags.tags[name].owner == owner:
                    del self.tags.tags[name]
                    await self._save_tags_unlocked()
                    return f'The tag "{name}" has been deleted'
                return f'You are not the owner of the tag "{name}"'

        return f'The tag "{name}" does not exist'

    async def list_tags(self) -> str:
        """Return a comma-separated list of saved tag names."""
        async with self._tag_lock:
            if not self.tags.tags:
                return "No tags exist"
            tag_names = list(self.tags.tags)

        return truncate_discord_message(", ".join(tag_names))

    async def edit_tag(self, name: str, owner: int, content: str) -> str:
        """Replace a tag's content when requested by its owner."""
        async with self._tag_lock:
            if not self.tags.tags:
                return "There are no tags"

            if name not in self.tags.tags:
                return f'The tag "{name}" does not exist'

            if self.tags.tags[name].owner != owner:
                return f'You are not the owner of the tag "{name}"'

            self.tags.tags[name].content = truncate_text(content, DISCORD_MESSAGE_LIMIT)
            await self._save_tags_unlocked()
            return f'The tag "{name}" has been edited'

    async def rename_tag(self, old_name: str, new_name: str, owner: int) -> str:
        """Rename a tag when requested by its owner."""
        async with self._tag_lock:
            if not self.tags.tags:
                return "There are no tags"

            if old_name not in self.tags.tags:
                return f'The tag "{old_name}" does not exist'

            if new_name in RESERVED_TAG_NAMES:
                return reserved_tag_name_message(new_name)

            if new_name in self.tags.tags:
                return f'The tag "{new_name}" already exists'

            if self.tags.tags[old_name].owner != owner:
                return f'You are not the owner of the tag "{old_name}"'

            self.tags.tags[new_name] = self.tags.tags.pop(old_name)
            await self._save_tags_unlocked()
            return f'The tag "{old_name}" has been renamed to "{new_name}"'

    async def gift_tag(self, name: str, owner: int, new_owner: int) -> str:
        """Transfer a tag to a new owner when requested by its owner."""
        async with self._tag_lock:
            if not self.tags.tags:
                return "There are no tags"

            if name not in self.tags.tags:
                return f'The tag "{name}" does not exist'

            if self.tags.tags[name].owner != owner:
                return f'You are not the owner of the tag "{name}"'

            self.tags.tags[name].owner = new_owner
            await self._save_tags_unlocked()
            return f'The tag "{name}" has been gifted to {new_owner}'

    async def filter_tags(self, keyword: str) -> str:
        """Return tag names that contain a keyword."""
        async with self._tag_lock:
            filtered_tag_names = [tag for tag in self.tags.tags if keyword in tag]
        filtered_tags = ", ".join(filtered_tag_names)
        if not filtered_tags:
            return f"No tags contain the word {keyword}"
        return truncate_discord_message(filtered_tags)

    async def find_owner(self, name: str, fetch_user_func: Callable[[int], Awaitable[discord.User]]) -> str:
        """Look up and return the Discord user that owns a tag."""
        async with self._tag_lock:
            tag = self.tags.tags.get(name)
            if tag is None:
                return f'The tag "{name}" does not exist'

            owner = tag.owner

        try:
            user = await fetch_user_func(owner)
        except discord.NotFound, discord.HTTPException:
            return f'Ran into an error when trying to get the owner of "{name}"'

        return f'The owner of "{name}" is {user.name}'
