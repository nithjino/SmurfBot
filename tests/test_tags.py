import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from smurfbot import storage
from smurfbot.core import CommandError, GroupRef
from smurfbot.registry import GuildRegistry
from smurfbot.storage import StateFileError
from smurfbot.tags import TagBook, TagCommand, UserLookupError
from smurfbot.tags.book import MAX_TAGS_PER_GUILD, RESERVED_NAMES
from smurfbot.tags.commands import HELP_TEXT
from smurfbot.text import DISCORD_MESSAGE_LIMIT, TRUNCATION_SUFFIX
from tests.support import GUILD, context, read_json, run

GUILD_FILE = f"{GUILD.id}.json"


async def fetch_alice(_user_id: int) -> str:
    return "Alice"


def make(directory: Path, fetch_user_name: Any = fetch_alice) -> TagCommand:
    async def open_book(guild: GroupRef) -> TagBook:
        return await TagBook.open(directory, guild)

    return TagCommand(GuildRegistry(open_book), fetch_user_name)


async def tag(command: TagCommand, *args: str, author_id: int = 7, attachment_url: str | None = None) -> str:
    """Run a command the way the router would, showing refusals as replies."""
    try:
        return await command(context(*args, author_id=author_id, attachment_url=attachment_url))
    except CommandError as error:
        return error.message


def saved(directory: Path) -> dict[str, Any]:
    return read_json(directory / GUILD_FILE)


def test_first_use_creates_the_exact_file_and_restarts_keep_tags(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        assert await tag(tags, "create", "launch", "go", "now") == 'The tag "launch" was created successfully'
        assert saved(tmp_path) == {
            "name": "Guild",
            "id": 202,
            "tags": {"launch": {"owner": 7, "content": "go now"}},
        }
        assert await tag(make(tmp_path), "launch") == "go now"

    run(exercise())


def test_listing_an_unused_guild_creates_an_empty_file(tmp_path: Path) -> None:
    run(tag(make(tmp_path), "list"))

    assert saved(tmp_path) == {"name": "Guild", "id": 202, "tags": {}}


def test_help_and_case_sensitive_lookup(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        assert await tag(tags) == HELP_TEXT
        assert await tag(tags, "help", "ignored") == HELP_TEXT
        assert await tag(tags, "missing") == 'The tag "missing" does not exist'
        await tag(tags, "create", "Launch", "go now")
        assert await tag(tags, "Launch", "ignored") == "go now"
        assert await tag(tags, "launch") == 'The tag "launch" does not exist'
        assert await tag(tags, "CREATE", "unused") == 'The tag "CREATE" does not exist'

    run(exercise())


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("create",), "Usage: tag create [name] [content]"),
        (("delete",), "Usage: tag delete [name]"),
        (("edit",), "Usage: tag edit [name] [new material]"),
        (("edit", "launch"), "Usage: tag edit [name] [new material]"),
        (("rename",), "Usage: tag rename [current name] [new name]"),
        (("rename", "launch"), "Usage: tag rename [current name] [new name]"),
        (("gift",), "Usage: tag gift [name] [new owner mention or id]"),
        (("gift", "launch"), "Usage: tag gift [name] [new owner mention or id]"),
        (("owner",), "Usage: tag owner [name]"),
        (("filter",), "Usage: tag filter [keyword]"),
    ],
)
def test_usage_messages(tmp_path: Path, args: tuple[str, ...], expected: str) -> None:
    assert run(tag(make(tmp_path), *args)) == expected


def test_attachments_win_over_text_and_empty_tags_are_refused(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        url = "https://example.test/d.png"
        assert await tag(tags, "create", "diagram", "ignored", attachment_url=url) == (
            'The tag "diagram" was created successfully'
        )
        assert await tag(tags, "diagram") == url
        empty = "A tag needs either text content or an attachment"
        assert await tag(tags, "create", "empty") == empty
        assert await tag(tags, "create", "empty", "ignored", attachment_url="") == empty
        assert await tag(tags, "create", "diagram", "again") == 'The tag "diagram" already exists'
        assert saved(tmp_path)["tags"] == {"diagram": {"owner": 7, "content": url}}

    run(exercise())


@pytest.mark.parametrize("name", sorted(RESERVED_NAMES))
def test_reserved_names_are_refused(tmp_path: Path, name: str) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        await tag(tags, "create", "launch", "go now")
        before = (tmp_path / GUILD_FILE).read_bytes()
        expected = f'The tag name "{name}" is reserved for a tag command'
        assert await tag(tags, "create", name, "shadowed") == expected
        assert await tag(tags, "rename", "launch", name) == expected
        assert (tmp_path / GUILD_FILE).read_bytes() == before

    run(exercise())


def test_every_subcommand_is_reserved(tmp_path: Path) -> None:
    assert set(make(tmp_path)._subcommands) == RESERVED_NAMES


@pytest.mark.parametrize(
    "mutation",
    [("edit", "launch", "blocked"), ("rename", "launch", "mission"), ("delete", "launch"), ("gift", "launch", "9")],
)
def test_only_the_owner_can_change_a_tag(tmp_path: Path, mutation: tuple[str, ...]) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        await tag(tags, "create", "launch", "go now")
        before = (tmp_path / GUILD_FILE).read_bytes()
        assert await tag(tags, *mutation, author_id=8) == 'You are not the owner of the tag "launch"'
        assert (tmp_path / GUILD_FILE).read_bytes() == before

    run(exercise())


def test_mutations_persist_exact_records(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        await tag(tags, "create", "launch", "go now")
        await tag(tags, "create", "taken", "already here")
        assert await tag(tags, "edit", "launch", "updated", "content") == 'The tag "launch" has been edited'
        assert saved(tmp_path)["tags"]["launch"] == {"owner": 7, "content": "updated content"}
        assert await tag(tags, "rename", "launch", "taken") == 'The tag "taken" already exists'
        assert await tag(tags, "rename", "launch", "mission", "ignored") == (
            'The tag "launch" has been renamed to "mission"'
        )
        assert saved(tmp_path)["tags"] == {
            "taken": {"owner": 7, "content": "already here"},
            "mission": {"owner": 7, "content": "updated content"},
        }
        assert await tag(tags, "gift", "mission", "9", "ignored") == 'The tag "mission" has been gifted to 9'
        assert saved(tmp_path)["tags"]["mission"] == {"owner": 9, "content": "updated content"}
        assert await tag(tags, "edit", "mission", "blocked") == 'You are not the owner of the tag "mission"'
        assert await tag(tags, "delete", "mission", "ignored", author_id=9) == 'The tag "mission" has been deleted'
        assert saved(tmp_path)["tags"] == {"taken": {"owner": 7, "content": "already here"}}

    run(exercise())


@pytest.mark.parametrize(
    "mutation",
    [("edit", "missing", "value"), ("rename", "missing", "new"), ("delete", "missing"), ("gift", "missing", "9")],
)
def test_empty_and_missing_tag_responses(tmp_path: Path, mutation: tuple[str, ...]) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        assert await tag(tags, *mutation) == "There are no tags"
        await tag(tags, "create", "present", "value")
        assert await tag(tags, *mutation) == 'The tag "missing" does not exist'

    run(exercise())


@pytest.mark.parametrize("new_owner", ["42", "<@42>", "<@!42>"])
def test_gift_accepts_ids_and_mentions(tmp_path: Path, new_owner: str) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        await tag(tags, "create", "launch", "go now")
        assert await tag(tags, "gift", "launch", new_owner) == 'The tag "launch" has been gifted to 42'
        assert saved(tmp_path)["tags"]["launch"] == {"owner": 42, "content": "go now"}

    run(exercise())


@pytest.mark.parametrize("new_owner", ["alice", "<@alice>", "<@&42>", "-1"])
def test_gift_rejects_invalid_recipients(tmp_path: Path, new_owner: str) -> None:
    expected = "The new owner must be a Discord user mention or user id"

    assert run(tag(make(tmp_path), "gift", "missing", new_owner)) == expected


def test_list_and_filter(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        assert await tag(tags, "list") == "No tags exist"
        assert await tag(tags, "filter", "launch") == "No tags contain the word launch"
        for name in ["z-launch", "Launch", "a-launch"]:
            await tag(tags, "create", name, "go now")
        assert await tag(tags, "list", "ignored") == "z-launch, Launch, a-launch"
        assert await tag(tags, "filter", "launch", "ignored") == "z-launch, a-launch"
        assert await tag(tags, "filter", "Launch") == "Launch"
        await tag(tags, "rename", "z-launch", "renamed")
        assert await tag(tags, "list") == "Launch, a-launch, renamed"
        assert await tag(make(tmp_path), "list") == "Launch, a-launch, renamed"

    run(exercise())


def test_content_is_truncated_and_the_guild_limit_holds(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        await tag(tags, "create", "long", "a" * (DISCORD_MESSAGE_LIMIT + 50))
        expected = "a" * (DISCORD_MESSAGE_LIMIT - len(TRUNCATION_SUFFIX)) + TRUNCATION_SUFFIX
        assert await tag(tags, "long") == expected
        assert saved(tmp_path)["tags"]["long"]["content"] == expected
        await tag(tags, "edit", "long", "b" * (DISCORD_MESSAGE_LIMIT + 50))
        assert len(saved(tmp_path)["tags"]["long"]["content"]) == DISCORD_MESSAGE_LIMIT
        for index in range(MAX_TAGS_PER_GUILD - 1):
            await tag(tags, "create", f"tag-{index:03}", "go now")
        before = (tmp_path / GUILD_FILE).read_bytes()
        assert await tag(tags, "create", "overflow", "value") == (
            "Tag limit reached. Delete a tag before creating a new one. Limit: 200"
        )
        assert (tmp_path / GUILD_FILE).read_bytes() == before

    run(exercise())


def test_owner_lookup(tmp_path: Path) -> None:
    async def exercise() -> None:
        fetched: list[int] = []

        async def fetch(user_id: int) -> str:
            fetched.append(user_id)
            return "Alice"

        tags = make(tmp_path, fetch)
        assert await tag(tags, "owner", "missing") == 'The tag "missing" does not exist'
        assert fetched == []
        await tag(tags, "create", "launch", "go now")
        assert await tag(tags, "owner", "launch") == 'The owner of "launch" is Alice'
        assert fetched == [7]

    run(exercise())


def test_failed_owner_lookup_is_reported(tmp_path: Path) -> None:
    async def fetch(_user_id: int) -> str:
        raise UserLookupError

    async def exercise() -> None:
        tags = make(tmp_path, fetch)
        await tag(tags, "create", "launch", "go now")
        assert await tag(tags, "owner", "launch") == 'Ran into an error when trying to get the owner of "launch"'

    run(exercise())


@pytest.mark.parametrize("error", [asyncio.CancelledError, RuntimeError])
def test_unexpected_lookup_errors_propagate(tmp_path: Path, error: type[BaseException]) -> None:
    async def fetch(_user_id: int) -> str:
        raise error

    async def exercise() -> None:
        tags = make(tmp_path, fetch)
        await tag(tags, "create", "launch", "go now")
        with pytest.raises(error):
            await tag(tags, "owner", "launch")

    run(exercise())


def test_owner_lookup_does_not_block_mutations(tmp_path: Path) -> None:
    async def exercise() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def fetch(_user_id: int) -> str:
            entered.set()
            await release.wait()
            return "Alice"

        tags = make(tmp_path, fetch)
        await tag(tags, "create", "launch", "go now")
        lookup = asyncio.create_task(tag(tags, "owner", "launch"))
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert await asyncio.wait_for(tag(tags, "gift", "launch", "9"), timeout=1) == (
            'The tag "launch" has been gifted to 9'
        )
        release.set()
        assert await lookup == 'The owner of "launch" is Alice'
        assert saved(tmp_path)["tags"]["launch"]["owner"] == 9

    run(exercise())


def test_concurrent_mutations_keep_all_records(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        created = await asyncio.gather(*(tag(tags, "create", f"tag-{i}", "original") for i in range(20)))
        assert created == [f'The tag "tag-{i}" was created successfully' for i in range(20)]
        await asyncio.gather(*(tag(tags, "edit", f"tag-{i}", "updated") for i in range(20)))
        assert saved(tmp_path)["tags"] == {f"tag-{i}": {"owner": 7, "content": "updated"} for i in range(20)}
        results = await asyncio.gather(*(tag(tags, "create", "shared", "value") for _ in range(2)))
        assert results == ['The tag "shared" was created successfully', 'The tag "shared" already exists']

    run(exercise())


@pytest.mark.parametrize(
    "source",
    ["{bad json", '{"id":202,"name":"Guild","tags":{"broken":{}}}', '{"id":202,"name":"G","tags":{},"extra":1}'],
)
def test_corrupt_files_are_refused_and_stay_untouched(tmp_path: Path, source: str) -> None:
    path = tmp_path / GUILD_FILE
    path.write_text(source, encoding="utf-8")

    with pytest.raises(StateFileError):
        run(tag(make(tmp_path), "list"))

    assert path.read_text(encoding="utf-8") == source


def test_string_ids_from_older_files_load_and_normalize_on_write(tmp_path: Path) -> None:
    source = {"name": "Old Guild", "id": "202", "tags": {"launch": {"owner": "7", "content": "go now"}}}
    path = tmp_path / GUILD_FILE
    path.write_text(json.dumps(source), encoding="utf-8")

    async def exercise() -> None:
        tags = make(tmp_path)
        assert await tag(tags, "launch") == "go now"
        assert saved(tmp_path) == source
        assert await tag(tags, "edit", "launch", "updated") == 'The tag "launch" has been edited'

    run(exercise())

    assert saved(tmp_path) == {"name": "Old Guild", "id": 202, "tags": {"launch": {"owner": 7, "content": "updated"}}}


def test_failed_save_does_not_change_what_chat_sees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_path: Path, _text: str) -> None:
        raise OSError("disk full")

    async def exercise() -> None:
        tags = make(tmp_path)
        await tag(tags, "create", "launch", "go now")
        monkeypatch.setattr(storage, "write_atomic", fail)
        with pytest.raises(OSError, match="disk full"):
            await tag(tags, "create", "new", "value")
        with pytest.raises(OSError, match="disk full"):
            await tag(tags, "edit", "launch", "changed")
        with pytest.raises(OSError, match="disk full"):
            await tag(tags, "delete", "launch")
        assert await tag(tags, "list") == "launch"
        assert await tag(tags, "launch") == "go now"

    run(exercise())


def test_tags_need_a_guild_but_help_does_not(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = make(tmp_path)
        assert await tags(context(guild=None)) == HELP_TEXT
        with pytest.raises(CommandError, match="only work in a server"):
            await tags(context("list", guild=None))

    run(exercise())
