"""Handlers for tag subcommands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final

from models import CommandParameters

from .models import TagContent

if TYPE_CHECKING:
    from .tags import Tags


MIN_NAMED_VALUE_ARGUMENTS: Final = 2
TagCommandHandler = Callable[["Tags", CommandParameters, list[str]], Awaitable[str]]


def parse_discord_user_id(user_id_text: str) -> int | None:
    """Parse a raw Discord user ID or user mention into an integer ID."""
    cleaned_user_id = user_id_text.strip()
    if cleaned_user_id.startswith("<@") and cleaned_user_id.endswith(">"):
        cleaned_user_id = cleaned_user_id[2:-1].removeprefix("!")

    if not cleaned_user_id.isdecimal():
        return None
    return int(cleaned_user_id)


async def handle_create(tags: Tags, parameters: CommandParameters, message: list[str]) -> str:
    """Handle tag creation."""
    if not message:
        return "Usage: tag create [name] [content]"

    name = message[0]
    content = TagContent(message=" ".join(message[1:]), attachment=parameters.attachment)
    return await tags.create_tag(name, content, parameters.author_id)


async def handle_delete(tags: Tags, parameters: CommandParameters, message: list[str]) -> str:
    """Handle tag deletion."""
    if not message:
        return "Usage: tag delete [name]"

    return await tags.delete_tag(message[0], parameters.author_id)


async def handle_list(tags: Tags, _parameters: CommandParameters, _message: list[str]) -> str:
    """Handle listing tags."""
    return await tags.list_tags()


async def handle_help(tags: Tags, _parameters: CommandParameters, _message: list[str]) -> str:
    """Handle tag help."""
    return await tags.post_help()


async def handle_edit(tags: Tags, parameters: CommandParameters, message: list[str]) -> str:
    """Handle tag edits."""
    if len(message) < MIN_NAMED_VALUE_ARGUMENTS:
        return "Usage: tag edit [name] [new material]"

    return await tags.edit_tag(message[0], parameters.author_id, " ".join(message[1:]))


async def handle_rename(tags: Tags, parameters: CommandParameters, message: list[str]) -> str:
    """Handle tag renames."""
    if len(message) < MIN_NAMED_VALUE_ARGUMENTS:
        return "Usage: tag rename [current name] [new name]"

    return await tags.rename_tag(message[0], message[1], parameters.author_id)


async def handle_gift(tags: Tags, parameters: CommandParameters, message: list[str]) -> str:
    """Handle tag ownership transfers."""
    if len(message) < MIN_NAMED_VALUE_ARGUMENTS:
        return "Usage: tag gift [name] [new owner mention or id]"

    new_owner = parse_discord_user_id(message[1])
    if new_owner is None:
        return "The new owner must be a Discord user mention or user id"

    return await tags.gift_tag(message[0], parameters.author_id, new_owner)


async def handle_owner(tags: Tags, parameters: CommandParameters, message: list[str]) -> str:
    """Handle tag owner lookups."""
    if not message:
        return "Usage: tag owner [name]"

    fetch_user_func = parameters.fetch_user_func
    if fetch_user_func is None:
        return f'Ran into an error when trying to get the owner of "{message[0]}"'

    return await tags.find_owner(message[0], fetch_user_func)


async def handle_filter(tags: Tags, _parameters: CommandParameters, message: list[str]) -> str:
    """Handle tag name filtering."""
    if not message:
        return "Usage: tag filter [keyword]"

    return await tags.filter_tags(message[0])


TAG_COMMAND_HANDLERS: Final[dict[str, TagCommandHandler]] = {
    "create": handle_create,
    "delete": handle_delete,
    "list": handle_list,
    "help": handle_help,
    "edit": handle_edit,
    "rename": handle_rename,
    "gift": handle_gift,
    "owner": handle_owner,
    "filter": handle_filter,
}
