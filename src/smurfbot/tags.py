"""The tag command: named, owned snippets of text per guild."""

import re
from collections.abc import Callable

from smurfbot.core import Request
from smurfbot.store import Store, Tag

MAX_TAGS_PER_GUILD = 500
RESERVED_NAMES = frozenset({"create", "delete", "edit", "filter", "gift", "help", "list", "owner", "rename"})
HELP_TEXT = (
    'To create a tag: "tag create [name]" while uploading a picture or by providing a link or text after the name. '
    'To post an already created tag: "tag [name]". To see what tags are made: "tag list". '
    'To edit a tag: "tag edit [name] [new material]". '
    'To rename a tag: "tag rename [current name] [new name]".'
)
DM_REPLY = "Tags only work in a server."
LIMIT_REPLY = f"Tag limit reached. Delete a tag before creating a new one. Limit: {MAX_TAGS_PER_GUILD}"
EMPTY_REPLY = "A tag needs either text content or an attachment"
BAD_USER_REPLY = "The new owner must be a Discord user mention or user id"

_USER = re.compile(r"([0-9]+)|<@!?([0-9]+)>")

type _Subcommand = Callable[[Store, Request, dict[str, Tag], list[str]], str]


def tag_command(store: Store, request: Request, args: list[str]) -> str:
    """Run one tag subcommand and return the reply."""
    if not args or args[0] == "help":
        return HELP_TEXT
    if request.guild_id is None:
        return DM_REPLY
    guild_tags = store.state.tags
    # Only create stores a new guild's map, so reads never add empty entries to the file.
    tags = guild_tags.setdefault(request.guild_id, {}) if args[0] == "create" else guild_tags.get(request.guild_id, {})
    subcommand = _SUBCOMMANDS.get(args[0])
    if subcommand is None:
        tag = tags.get(args[0])
        return tag.content if tag else _missing(args[0])
    return subcommand(store, request, tags, args[1:])


def parse_user(text: str) -> int | None:
    """Return the user id in a mention or bare id, or None."""
    match = _USER.fullmatch(text)
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


def _missing(name: str) -> str:
    return f'The tag "{name}" does not exist'


def _reserved(name: str) -> str:
    return f'The tag name "{name}" is reserved for a tag command'


def _taken(name: str) -> str:
    return f'The tag "{name}" already exists'


def _content(request: Request, words: list[str]) -> str:
    return request.attachment_url or " ".join(words)


def _owned(tags: dict[str, Tag], name: str, author_id: int) -> Tag | str:
    """Return the tag if author_id owns it, otherwise the refusal."""
    tag = tags.get(name)
    if tag is None:
        return _missing(name)
    if tag.owner != author_id:
        return f'You are not the owner of the tag "{name}"'
    return tag


def _create(store: Store, request: Request, tags: dict[str, Tag], args: list[str]) -> str:
    if not args:
        return "Usage: tag create [name] [content]"
    name = args[0]
    if name in RESERVED_NAMES:
        return _reserved(name)
    if name in tags:
        return _taken(name)
    if len(tags) >= MAX_TAGS_PER_GUILD:
        return LIMIT_REPLY
    content = _content(request, args[1:])
    if not content:
        return EMPTY_REPLY
    tags[name] = Tag(owner=request.author_id, content=content)
    store.save()
    return f'The tag "{name}" was created successfully'


def _edit(store: Store, request: Request, tags: dict[str, Tag], args: list[str]) -> str:
    content = _content(request, args[1:])
    if not args or not content:
        return "Usage: tag edit [name] [new material]"
    tag = _owned(tags, args[0], request.author_id)
    if isinstance(tag, str):
        return tag
    tag.content = content
    store.save()
    return f'The tag "{args[0]}" has been edited'


def _delete(store: Store, request: Request, tags: dict[str, Tag], args: list[str]) -> str:
    if not args:
        return "Usage: tag delete [name]"
    tag = _owned(tags, args[0], request.author_id)
    if isinstance(tag, str):
        return tag
    del tags[args[0]]
    store.save()
    return f'The tag "{args[0]}" has been deleted'


def _rename(store: Store, request: Request, tags: dict[str, Tag], args: list[str]) -> str:
    if len(args) < 2:  # noqa: PLR2004
        return "Usage: tag rename [current name] [new name]"
    name, new = args[0], args[1]
    tag = _owned(tags, name, request.author_id)
    if isinstance(tag, str):
        return tag
    if new in RESERVED_NAMES:
        return _reserved(new)
    if new in tags:
        return _taken(new)
    del tags[name]
    tags[new] = tag
    store.save()
    return f'The tag "{name}" has been renamed to "{new}"'


def _gift(store: Store, request: Request, tags: dict[str, Tag], args: list[str]) -> str:
    if len(args) < 2:  # noqa: PLR2004
        return "Usage: tag gift [name] [new owner mention or id]"
    name = args[0]
    user_id = parse_user(args[1])
    if user_id is None:
        return BAD_USER_REPLY
    tag = _owned(tags, name, request.author_id)
    if isinstance(tag, str):
        return tag
    tag.owner = user_id
    store.save()
    return f'The tag "{name}" has been gifted to <@{user_id}>'


def _owner(_store: Store, _request: Request, tags: dict[str, Tag], args: list[str]) -> str:
    if not args:
        return "Usage: tag owner [name]"
    tag = tags.get(args[0])
    if tag is None:
        return _missing(args[0])
    return f'The owner of "{args[0]}" is <@{tag.owner}>'


def _list(_store: Store, _request: Request, tags: dict[str, Tag], _args: list[str]) -> str:
    return ", ".join(tags) or "No tags exist"


def _filter(_store: Store, _request: Request, tags: dict[str, Tag], args: list[str]) -> str:
    if not args:
        return "Usage: tag filter [keyword]"
    keyword = args[0]
    return ", ".join(name for name in tags if keyword in name) or f"No tags contain the word {keyword}"


_SUBCOMMANDS: dict[str, _Subcommand] = {
    "create": _create,
    "edit": _edit,
    "delete": _delete,
    "rename": _rename,
    "gift": _gift,
    "owner": _owner,
    "list": _list,
    "filter": _filter,
}
