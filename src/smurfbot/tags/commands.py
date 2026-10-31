"""The `tag` chat command and its subcommands."""

from collections.abc import Awaitable, Callable
from typing import Final

from smurfbot.core import CommandContext, CommandError, CommandHandler
lazy from smurfbot.registry import GuildRegistry
lazy from smurfbot.tags.book import TagBook

HELP_TEXT: Final = (
    'To create a tag: "tag create [name]" while uploading a picture or by providing a link or text after '
    'the name. To post an already created tag: "tag [name]". To see what tags are made: "tag list".'
    ' To edit a tag: "tag edit [name] [new material]". To rename a tag:"tag rename [current name]'
    ' [new name]".'
)
GUILD_ONLY: Final = "Tags only work in a server."
_NAME_AND_VALUE_ARGUMENTS: Final = 2

type FetchUserName = Callable[[int], Awaitable[str]]


class UserLookupError(Exception):
    """The transport could not resolve a user ID."""


def _parse_user_id(text: str) -> int | None:
    """Parse a raw Discord user ID or user mention."""
    if text.startswith("<@") and text.endswith(">"):
        text = text[2:-1].removeprefix("!")
    return int(text) if text.isdecimal() else None


class TagCommand:
    """Parse `tag` arguments and word the replies; the rules live in TagBook."""

    def __init__(self, books: GuildRegistry[TagBook], fetch_user_name: FetchUserName) -> None:
        """Bind the guild registry and the transport's user lookup."""
        self._books = books
        self._fetch_user_name = fetch_user_name
        self._subcommands: frozendict[str, CommandHandler] = frozendict(
            {  # pyright: ignore[reportCallIssue]
                "create": self._create,
                "delete": self._delete,
                "edit": self._edit,
                "filter": self._filter,
                "gift": self._gift,
                "help": self._help,
                "list": self._list,
                "owner": self._owner,
                "rename": self._rename,
            }
        )

    async def __call__(self, context: CommandContext) -> str:
        """Run a subcommand, or post the tag named by the first argument."""
        if not context.args:
            return HELP_TEXT
        handler = self._subcommands.get(context.args[0])
        if handler is not None:
            return await handler(context)
        return (await self._book(context)).get(context.args[0]).content

    async def _book(self, context: CommandContext) -> TagBook:
        if context.guild is None:
            raise CommandError(GUILD_ONLY)
        return await self._books.get(context.guild)

    @staticmethod
    async def _help(_context: CommandContext) -> str:
        return HELP_TEXT

    async def _create(self, context: CommandContext) -> str:
        match context.args:
            case [_, name, *words]:
                content = context.attachment_url if context.attachment_url is not None else " ".join(words)
                await (await self._book(context)).create(name, owner=context.author_id, content=content)
                return f'The tag "{name}" was created successfully'
            case _:
                return "Usage: tag create [name] [content]"

    async def _delete(self, context: CommandContext) -> str:
        match context.args:
            case [_, name, *_]:
                await (await self._book(context)).delete(name, actor=context.author_id)
                return f'The tag "{name}" has been deleted'
            case _:
                return "Usage: tag delete [name]"

    async def _edit(self, context: CommandContext) -> str:
        match context.args:
            case [_, name, first, *rest]:
                content = " ".join((first, *rest))
                await (await self._book(context)).edit(name, actor=context.author_id, content=content)
                return f'The tag "{name}" has been edited'
            case _:
                return "Usage: tag edit [name] [new material]"

    async def _rename(self, context: CommandContext) -> str:
        match context.args:
            case [_, name, new_name, *_]:
                await (await self._book(context)).rename(name, new_name, actor=context.author_id)
                return f'The tag "{name}" has been renamed to "{new_name}"'
            case _:
                return "Usage: tag rename [current name] [new name]"

    async def _gift(self, context: CommandContext) -> str:
        match context.args:
            case [_, name, recipient, *_]:
                new_owner = _parse_user_id(recipient)
                if new_owner is None:
                    return "The new owner must be a Discord user mention or user id"
                await (await self._book(context)).gift(name, actor=context.author_id, new_owner=new_owner)
                return f'The tag "{name}" has been gifted to {new_owner}'
            case _:
                return "Usage: tag gift [name] [new owner mention or id]"

    async def _list(self, context: CommandContext) -> str:
        names = (await self._book(context)).names
        return ", ".join(names) if names else "No tags exist"

    async def _filter(self, context: CommandContext) -> str:
        match context.args:
            case [_, keyword, *_]:
                matches = [name for name in (await self._book(context)).names if keyword in name]
                return ", ".join(matches) if matches else f"No tags contain the word {keyword}"
            case _:
                return "Usage: tag filter [keyword]"

    async def _owner(self, context: CommandContext) -> str:
        match context.args:
            case [_, name, *_]:
                owner = (await self._book(context)).get(name).owner
                try:
                    owner_name = await self._fetch_user_name(owner)
                except UserLookupError:
                    return f'Ran into an error when trying to get the owner of "{name}"'
                return f'The owner of "{name}" is {owner_name}'
            case _:
                return "Usage: tag owner [name]"
