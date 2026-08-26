"""Handle all tag-related commands."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import discord
from atomicwrites import atomic_write

from message_limits import DISCORD_MESSAGE_LIMIT, truncate_discord_message, truncate_text

from .models import TagContent, TagFile, TagRecord

if TYPE_CHECKING:
    from models import CommandParameters

_logger = logging.getLogger(__name__)

MAX_TAGS_PER_GUILD: Final[int] = 200
MIN_NAMED_VALUE_ARGUMENTS: Final[int] = 2


def _reserved_tag_name_message(name: str) -> str:
    """Return the validation message for tag names reserved by subcommands."""
    return f'The tag name "{name}" is reserved for a tag command'


def _parse_discord_user_id(user_id_text: str) -> int | None:
    """Parse a raw Discord user ID or user mention into an integer ID."""
    cleaned_user_id = user_id_text.strip()
    if cleaned_user_id.startswith("<@") and cleaned_user_id.endswith(">"):
        cleaned_user_id = cleaned_user_id[2:-1].removeprefix("!")

    if not cleaned_user_id.isdecimal():
        return None
    return int(cleaned_user_id)


class Tags:
    """Handle all tag-related commands."""

    def __init__(self, guild: discord.Guild, tags_json_path: str | Path) -> None:
        """Initialize paths and default tag state for a guild."""
        self._guild = guild
        self._tags_json_path = Path(tags_json_path)
        self._tags_json_file = self._tags_json_path / f"{guild.id}.json"
        self._tags = TagFile(name=guild.name, id=guild.id)
        self._tag_lock = asyncio.Lock()
        self._commands = MappingProxyType(
            {
                "create": self._create_tag,
                "delete": self._delete_tag,
                "list": self._list_tags,
                "help": self._post_help,
                "edit": self._edit_tag,
                "rename": self._rename_tag,
                "gift": self._gift_tag,
                "owner": self._find_owner,
                "filter": self._filter_tags,
            }
        )

    @classmethod
    async def create(cls, guild: discord.Guild, tags_json_path: str | Path) -> Tags:
        """Create a tag handler and load persisted tags without blocking the event loop."""
        tag_handler = cls(guild, tags_json_path)
        tag_handler._tags = await tag_handler._load_tags()
        return tag_handler

    @staticmethod
    async def _post_help(_parameters: CommandParameters) -> str:
        """Return help text for tag commands."""
        return (
            'To create a tag: "tag create [name]" while uploading a picture or by providing a link or text after '
            'the name. To post an already created tag: "tag [name]". To see what tags are made: "tag list".'
            ' To edit a tag: "tag edit [name] [new material]". To rename a tag:"tag rename [current name]'
            ' [new name]".'
        )

    async def _create_json(self) -> None:
        """Create the tags directory and guild JSON file when missing."""

        def create_json_sync() -> None:
            self._tags_json_path.mkdir(parents=True, exist_ok=True)
            initial_state = TagFile(name=self._guild.name, id=self._guild.id)
            try:
                with self._tags_json_file.open("x", encoding="utf-8") as tags_file:
                    json.dump(initial_state.model_dump(mode="json"), tags_file)
            except FileExistsError:
                return
            _logger.info("%s: created: %s", self._guild.name, self._tags_json_file)

        await asyncio.to_thread(create_json_sync)

    async def _load_tags(self) -> TagFile:
        """Return the guild's tag JSON data."""
        await self._create_json()
        _logger.info("%s: loading: %s", self._guild.name, self._tags_json_file)
        with self._tags_json_file.open(encoding="utf-8") as tags_file:
            return TagFile.model_validate(json.load(tags_file))

    async def _save_tags_unlocked(self) -> None:
        """Write tags to disk while the caller holds the tag lock."""
        _logger.info("%s: saving: %s", self._guild.name, self._tags_json_file)
        with atomic_write(self._tags_json_file, overwrite=True, encoding="utf-8") as tag_file:
            tag_file.write(json.dumps(self._tags.model_dump(mode="json"), sort_keys=True, indent=2))

    async def parse_commands(self, parameters: CommandParameters) -> str:
        """Dispatch a tag subcommand using parsed message parameters."""
        _logger.info("tags parse_commands parameters: %s", parameters)
        if not parameters.message:
            return await self._post_help(parameters)

        command = parameters.message[0].rstrip()
        handler = self._commands.get(command)
        if handler is None:
            return await self._post_tag(command.strip())

        return await handler(parameters)

    async def _create_tag(self, parameters: CommandParameters) -> str:
        """Validate and create a tag owned by the requesting user."""
        message = parameters.message[1:]
        if not message:
            return "Usage: tag create [name] [content]"
        name = message[0]
        content = TagContent(message=" ".join(message[1:]), attachment=parameters.attachment)
        owner = parameters.author_id
        async with self._tag_lock:
            if name in self._commands:
                return _reserved_tag_name_message(name)
            if name in self._tags.tags:
                return f'The tag "{name}" already exists'
            if len(self._tags.tags) >= MAX_TAGS_PER_GUILD:
                return f"Tag limit reached. Delete a tag before creating a new one. Limit: {MAX_TAGS_PER_GUILD}"
            tag_content = content.attachment if content.attachment is not None else content.message.strip()
            if not tag_content:
                return "A tag needs either text content or an attachment"
            tag_content = truncate_text(tag_content, DISCORD_MESSAGE_LIMIT)
            self._tags.tags[name] = TagRecord(owner=owner, content=tag_content)
            await self._save_tags_unlocked()
            return f'The tag "{name}" was created successfully'

    async def _post_tag(self, name: str) -> str:
        """Return the content for a saved tag."""
        async with self._tag_lock:
            if name in self._tags.tags:
                return truncate_discord_message(self._tags.tags[name].content)
        return f'The tag "{name}" does not exist'

    async def _delete_tag(self, parameters: CommandParameters) -> str:
        """Validate and delete a tag when requested by its owner."""
        message = parameters.message[1:]
        if not message:
            return "Usage: tag delete [name]"
        name = message[0]
        owner = parameters.author_id
        async with self._tag_lock:
            if not self._tags.tags:
                return "There are no tags"

            if name in self._tags.tags:
                if self._tags.tags[name].owner == owner:
                    del self._tags.tags[name]
                    await self._save_tags_unlocked()
                    return f'The tag "{name}" has been deleted'
                return f'You are not the owner of the tag "{name}"'

        return f'The tag "{name}" does not exist'

    async def _list_tags(self, _parameters: CommandParameters) -> str:
        """Return a comma-separated list of saved tag names."""
        async with self._tag_lock:
            if not self._tags.tags:
                return "No tags exist"
            tag_names = list(self._tags.tags)

        return truncate_discord_message(", ".join(tag_names))

    async def _edit_tag(self, parameters: CommandParameters) -> str:
        """Validate and replace a tag's content when requested by its owner."""
        message = parameters.message[1:]
        if len(message) < MIN_NAMED_VALUE_ARGUMENTS:
            return "Usage: tag edit [name] [new material]"
        name = message[0]
        content = " ".join(message[1:])
        owner = parameters.author_id
        async with self._tag_lock:
            if not self._tags.tags:
                return "There are no tags"

            if name not in self._tags.tags:
                return f'The tag "{name}" does not exist'

            if self._tags.tags[name].owner != owner:
                return f'You are not the owner of the tag "{name}"'

            self._tags.tags[name].content = truncate_text(content, DISCORD_MESSAGE_LIMIT)
            await self._save_tags_unlocked()
            return f'The tag "{name}" has been edited'

    async def _rename_tag(self, parameters: CommandParameters) -> str:
        """Validate and rename a tag when requested by its owner."""
        message = parameters.message[1:]
        if len(message) < MIN_NAMED_VALUE_ARGUMENTS:
            return "Usage: tag rename [current name] [new name]"
        old_name, new_name = message[:2]
        owner = parameters.author_id
        async with self._tag_lock:
            if old_name not in self._tags.tags:
                return f'The tag "{old_name}" does not exist' if self._tags.tags else "There are no tags"

            if new_name in self._commands:
                return _reserved_tag_name_message(new_name)

            if new_name in self._tags.tags:
                return f'The tag "{new_name}" already exists'

            if self._tags.tags[old_name].owner != owner:
                return f'You are not the owner of the tag "{old_name}"'

            self._tags.tags[new_name] = self._tags.tags.pop(old_name)
            await self._save_tags_unlocked()
            return f'The tag "{old_name}" has been renamed to "{new_name}"'

    async def _gift_tag(self, parameters: CommandParameters) -> str:
        """Validate and transfer a tag to a new owner when requested by its owner."""
        message = parameters.message[1:]
        if len(message) < MIN_NAMED_VALUE_ARGUMENTS:
            return "Usage: tag gift [name] [new owner mention or id]"
        new_owner = _parse_discord_user_id(message[1])
        if new_owner is None:
            return "The new owner must be a Discord user mention or user id"
        name = message[0]
        owner = parameters.author_id
        async with self._tag_lock:
            if not self._tags.tags:
                return "There are no tags"

            if name not in self._tags.tags:
                return f'The tag "{name}" does not exist'

            if self._tags.tags[name].owner != owner:
                return f'You are not the owner of the tag "{name}"'

            self._tags.tags[name].owner = new_owner
            await self._save_tags_unlocked()
            return f'The tag "{name}" has been gifted to {new_owner}'

    async def _filter_tags(self, parameters: CommandParameters) -> str:
        """Validate a keyword and return matching tag names."""
        message = parameters.message[1:]
        if not message:
            return "Usage: tag filter [keyword]"
        keyword = message[0]
        async with self._tag_lock:
            filtered_tag_names = [tag for tag in self._tags.tags if keyword in tag]
        filtered_tags = ", ".join(filtered_tag_names)
        if not filtered_tags:
            return f"No tags contain the word {keyword}"
        return truncate_discord_message(filtered_tags)

    async def _find_owner(self, parameters: CommandParameters) -> str:
        """Validate a lookup and return the Discord user that owns a tag."""
        message = parameters.message[1:]
        if not message:
            return "Usage: tag owner [name]"
        name = message[0]
        fetch_user_func = parameters.fetch_user_func
        if fetch_user_func is None:
            return f'Ran into an error when trying to get the owner of "{name}"'
        async with self._tag_lock:
            tag = self._tags.tags.get(name)
            if tag is None:
                return f'The tag "{name}" does not exist'

            owner = tag.owner

        try:
            user = await fetch_user_func(owner)
        except discord.NotFound, discord.HTTPException:
            return f'Ran into an error when trying to get the owner of "{name}"'

        return f'The owner of "{name}" is {user.name}'
