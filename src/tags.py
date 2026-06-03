"""Handle all tag-related commands."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord
from atomicwrites import atomic_write
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_logger = logging.getLogger(__name__)


class TagContent(BaseModel):
    """Incoming content for a new tag."""

    message: str
    attachment: str | None


class TagRecord(BaseModel):
    """Persisted tag data."""

    owner: int
    content: str


class TagFile(BaseModel):
    """Persisted guild tag file."""

    name: str
    id: int
    tags: dict[str, TagRecord] = Field(default_factory=dict)


class TagCommandParameters(BaseModel):
    """Command context needed by tag operations."""

    message: list[str]
    attachment: str | None = None
    author_id: int
    fetch_user_func: Any = None


class Tags:
    """Handle all tag-related commands."""

    def __init__(self, guild: discord.Guild, tags_json_path: str | Path) -> None:
        """Initialize paths and default tag state for a guild."""
        self.guild = guild
        self.tags_json_path = Path(tags_json_path)
        self.tags_json_file = self.tags_json_path / f"{guild.id}.json"
        self.tags = TagFile(name=guild.name, id=guild.id)

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
        await asyncio.to_thread(self._create_json_sync)

    def _create_json_sync(self) -> None:
        """Create the tags directory and guild JSON file synchronously when missing."""
        if not self.tags_json_path.exists():
            self.tags_json_path.mkdir(parents=True)
            _logger.info("created %s", self.tags_json_path)
        # creates tag json file for the group if it doesn't exist
        if not self.tags_json_file.exists():
            create_json = TagFile(name=self.guild.name, id=self.guild.id)
            with self.tags_json_file.open("w", encoding="utf-8") as tags_file:
                json.dump(create_json.model_dump(mode="json"), tags_file)
            _logger.info("%s: created: %s", self.guild.name, self.tags_json_file)

    async def load_tags(self) -> TagFile:
        """Return the guild's tag JSON data."""
        await self.create_json()
        return await asyncio.to_thread(self._load_tags_sync)

    def _load_tags_sync(self) -> TagFile:
        """Return the guild's tag JSON data synchronously."""
        _logger.info("%s: loading: %s", self.guild.name, self.tags_json_file)
        with self.tags_json_file.open(encoding="utf-8") as tags_file:
            return TagFile.model_validate(json.load(tags_file))

    async def save_tags(self) -> None:
        """Write the guild's tags to disk."""
        await asyncio.to_thread(self._save_tags_sync)

    def _save_tags_sync(self) -> None:
        """Write the guild's tags to disk synchronously."""
        _logger.info("%s: saving: %s", self.guild.name, self.tags_json_file)
        with atomic_write(self.tags_json_file, overwrite=True, encoding="utf-8") as tag_file:
            tag_file.write(json.dumps(self.tags.model_dump(mode="json"), sort_keys=True, indent=2))

    async def parse_commands(self, parameters: object) -> str:
        """Dispatch a tag subcommand using parsed message parameters."""
        parameters = TagCommandParameters.model_validate(parameters)
        _logger.info("tags parse_commands parameters: %s", parameters)
        command = parameters.message[0].rstrip()
        message = parameters.message[1:]
        tag_result = ""
        match command:
            case "create":
                name = message[0]
                content = TagContent(message=" ".join(message[1:]), attachment=parameters.attachment)
                owner = parameters.author_id
                tag_result = await self.create_tag(name, content, owner)
            case "delete":
                tag_result = await self.delete_tag(message[0], parameters.author_id)
            case "list":
                tag_result = await self.list_tags()
            case "help":
                tag_result = await self.post_help()
            case "edit":
                tag_result = await self.edit_tag(message[0], parameters.author_id, " ".join(message[1:]))
            case "rename":
                tag_result = await self.rename_tag(message[0], message[1], parameters.author_id)
            case "gift":
                tag_result = await self.gift_tag(message[0], parameters.author_id, int(message[1]))
            case "owner":
                fetch_user_func = parameters.fetch_user_func
                if fetch_user_func is None:
                    tag_result = f'Ran into an error when trying to get the owner of "{message[0]}"'
                else:
                    tag_result = await self.find_owner(message[0], fetch_user_func)
            case "filter":
                tag_result = await self.filter_tags(message[0])
            case _:
                tag_result = await self.post_tag(command.strip())
        return tag_result

    async def create_tag(self, name: str, content: TagContent, owner: int) -> str:
        """Create a tag owned by the requesting user."""
        tag_content = content.message if content.attachment is None else content.attachment
        if name in self.tags.tags:
            return f'The tag "{name}" already exists'
        self.tags.tags[name] = TagRecord(owner=owner, content=tag_content)
        await self.save_tags()
        return f'The tag "{name}" was created successfully'

    async def post_tag(self, name: str) -> str:
        """Return the content for a saved tag."""
        if name in self.tags.tags:
            return self.tags.tags[name].content
        return f'The tag "{name}" does not exist'

    async def delete_tag(self, name: str, owner: int) -> str:
        """Delete a tag when requested by its owner."""
        if not self.tags.tags:
            return "There are no tags"

        if name in self.tags.tags:
            if self.tags.tags[name].owner == owner:
                del self.tags.tags[name]
                await self.save_tags()
                return f'The tag "{name}" has been deleted'
            return f'You are not the owner of the tag "{name}"'

        return f'The tag "{name}" does not exist'

    async def list_tags(self) -> str:
        """Return a comma-separated list of saved tag names."""
        if not self.tags.tags:
            return "No tags exist"

        return ", ".join(self.tags.tags.keys())

    async def edit_tag(self, name: str, owner: int, content: str) -> str:
        """Replace a tag's content when requested by its owner."""
        if not self.tags.tags:
            return "There are no tags"

        if name not in self.tags.tags:
            return f'The tag "{name}" does not exist'

        if self.tags.tags[name].owner != owner:
            return f'You are not the owner of the tag "{name}"'

        self.tags.tags[name].content = content
        await self.save_tags()
        return f'The tag "{name}" has been edited'

    async def rename_tag(self, old_name: str, new_name: str, owner: int) -> str:
        """Rename a tag when requested by its owner."""
        if not self.tags.tags:
            return "There are no tags"

        if old_name not in self.tags.tags:
            return f'The tag "{old_name}" does not exist'

        if new_name in self.tags.tags:
            return f'The tag "{new_name}" already exists'

        if self.tags.tags[old_name].owner != owner:
            return f'You are not the owner of the tag "{old_name}"'

        self.tags.tags[new_name] = self.tags.tags.pop(old_name)
        await self.save_tags()
        return f'The tag "{old_name}" has been renamed to "{new_name}"'

    async def gift_tag(self, name: str, owner: int, new_owner: int) -> str:
        """Transfer a tag to a new owner when requested by its owner."""
        if not self.tags.tags:
            return "There are no tags"

        if name not in self.tags.tags:
            return f'The tag "{name}" does not exist'

        if self.tags.tags[name].owner != owner:
            return f'You are not the owner of the tag "{name}"'

        self.tags.tags[name].owner = new_owner
        await self.save_tags()
        return f'The tag "{name}" has been gifted to {new_owner}'

    async def filter_tags(self, keyword: str) -> str:
        """Return tag names that contain a keyword."""
        filtered_tags = [tag for tag in self.tags.tags if keyword in tag]
        filtered_tags = ", ".join(filtered_tags)
        if not filtered_tags:
            return f"No tags contain the word {keyword}"
        return filtered_tags

    async def find_owner(self, name: str, fetch_user_func: Callable[[int], Awaitable[discord.User]]) -> str:
        """Look up and return the Discord user that owns a tag."""
        if name in self.tags.tags:
            try:
                user = await fetch_user_func(self.tags.tags[name].owner)
            except discord.NotFound, discord.HTTPException:
                return f'Ran into an error when trying to get the owner of "{name}"'
            else:
                return f'The owner of "{name}" is {user.name}'

        return f'The tag "{name}" does not exist'
