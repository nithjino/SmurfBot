"""Tag commands and observable persistence through the public interface."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import discord
import pytest
from pydantic import ValidationError

from message_limits import DISCORD_MESSAGE_LIMIT, TRUNCATION_SUFFIX
from models import CommandParameters
from tags.tags import MAX_TAGS_PER_GUILD, Tags

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path

    from models import FetchUser

HELP = (
    'To create a tag: "tag create [name]" while uploading a picture or by providing a link or text after '
    'the name. To post an already created tag: "tag [name]". To see what tags are made: "tag list".'
    ' To edit a tag: "tag edit [name] [new material]". To rename a tag:"tag rename [current name]'
    ' [new name]".'
)
COMMAND_NAMES = ("create", "delete", "list", "help", "edit", "rename", "gift", "owner", "filter")


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def command(
    *message: str, owner: int = 7, attachment: str | None = None, fetch_user: FetchUser | None = None
) -> CommandParameters:
    return CommandParameters(
        command="tag",
        message=list(message),
        created_at=datetime(2026, 6, 4, tzinfo=UTC),
        author_id=owner,
        author_name="Alice",
        guild_id=101,
        channel_id=202,
        attachment=attachment,
        fetch_user_func=fetch_user,
    )


async def make_tags(path: Path) -> Tags:
    return await Tags.create(SimpleNamespace(id=101, name="Guild"), path)


def saved(path: Path) -> dict[str, object]:
    return json.loads((path / "101.json").read_text(encoding="utf-8"))


def test_construction_creates_and_loads_exact_file(tmp_path: Path) -> None:
    async def exercise() -> None:
        path = tmp_path / "nested"
        tags = await make_tags(path)
        assert saved(path) == {"name": "Guild", "id": 101, "tags": {}}
        assert (
            await tags.parse_commands(command("create", "launch", "go", "now"))
            == 'The tag "launch" was created successfully'
        )
        assert saved(path) == {"name": "Guild", "id": 101, "tags": {"launch": {"owner": 7, "content": "go now"}}}
        loaded = await make_tags(path)
        assert await loaded.parse_commands(command("launch")) == "go now"

    run(exercise())


def test_help_fallback_and_command_matching(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        assert await tags.parse_commands(command()) == HELP
        assert await tags.parse_commands(command("help", "ignored")) == HELP
        assert await tags.parse_commands(command("missing")) == 'The tag "missing" does not exist'
        await tags.parse_commands(command("create", "Launch", "go now"))
        assert await tags.parse_commands(command("Launch", "ignored")) == "go now"
        assert await tags.parse_commands(command(" Launch ")) == "go now"
        assert await tags.parse_commands(command("launch")) == 'The tag "launch" does not exist'
        assert await tags.parse_commands(command("CREATE", "unused")) == 'The tag "CREATE" does not exist'
        assert await tags.parse_commands(command("list ", "ignored")) == "Launch"

    run(exercise())


@pytest.mark.parametrize(
    ("message", "expected"),
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
def test_usage_validation_does_not_write(tmp_path: Path, message: tuple[str, ...], expected: str) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        before = (tmp_path / "101.json").stat()
        assert await tags.parse_commands(command(*message)) == expected
        assert (tmp_path / "101.json").stat() == before

    run(exercise())


def test_create_attachment_precedence_empty_content_and_duplicates(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        assert (
            await tags.parse_commands(command("create", "diagram", "ignored", attachment="https://example.test/d.png"))
            == 'The tag "diagram" was created successfully'
        )
        assert await tags.parse_commands(command("diagram")) == "https://example.test/d.png"
        assert (
            await tags.parse_commands(command("create", "picture", attachment="https://example.test/p.png"))
            == 'The tag "picture" was created successfully'
        )
        assert (
            await tags.parse_commands(command("create", "text", "  trimmed  "))
            == 'The tag "text" was created successfully'
        )
        assert (
            await tags.parse_commands(command("create", "empty")) == "A tag needs either text content or an attachment"
        )
        assert (
            await tags.parse_commands(command("create", "empty", "   "))
            == "A tag needs either text content or an attachment"
        )
        assert (
            await tags.parse_commands(command("create", "empty", "ignored", attachment=""))
            == "A tag needs either text content or an attachment"
        )
        assert await tags.parse_commands(command("create", "text", "replacement")) == 'The tag "text" already exists'
        assert saved(tmp_path) == {
            "name": "Guild",
            "id": 101,
            "tags": {
                "diagram": {"owner": 7, "content": "https://example.test/d.png"},
                "picture": {"owner": 7, "content": "https://example.test/p.png"},
                "text": {"owner": 7, "content": "trimmed"},
            },
        }

    run(exercise())


@pytest.mark.parametrize("name", COMMAND_NAMES)
def test_create_and_rename_reject_every_reserved_name(tmp_path: Path, name: str) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        await tags.parse_commands(command("create", "launch", "go now"))
        before = (tmp_path / "101.json").read_bytes()
        expected = f'The tag name "{name}" is reserved for a tag command'
        assert await tags.parse_commands(command("create", name, "shadowed")) == expected
        assert await tags.parse_commands(command("rename", "launch", name)) == expected
        assert (tmp_path / "101.json").read_bytes() == before

    run(exercise())


@pytest.mark.parametrize(
    "mutation",
    [("edit", "launch", "blocked"), ("rename", "launch", "mission"), ("delete", "launch"), ("gift", "launch", "9")],
)
def test_mutations_reject_non_owner_without_writing(tmp_path: Path, mutation: tuple[str, ...]) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        await tags.parse_commands(command("create", "launch", "go now"))
        before = (tmp_path / "101.json").read_bytes()
        assert await tags.parse_commands(command(*mutation, owner=8)) == 'You are not the owner of the tag "launch"'
        assert (tmp_path / "101.json").read_bytes() == before

    run(exercise())


def test_mutation_sequence_persists_exact_records(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        await tags.parse_commands(command("create", "launch", "go now"))
        await tags.parse_commands(command("create", "taken", "already here"))
        assert (
            await tags.parse_commands(command("edit", "launch", "updated", "content"))
            == 'The tag "launch" has been edited'
        )
        assert saved(tmp_path)["tags"]["launch"] == {"owner": 7, "content": "updated content"}
        assert await tags.parse_commands(command("rename", "launch", "taken")) == 'The tag "taken" already exists'
        assert (
            await tags.parse_commands(command("rename", "launch", "mission", "ignored"))
            == 'The tag "launch" has been renamed to "mission"'
        )
        assert saved(tmp_path)["tags"] == {
            "taken": {"owner": 7, "content": "already here"},
            "mission": {"owner": 7, "content": "updated content"},
        }
        assert (
            await tags.parse_commands(command("gift", "mission", "9", "ignored"))
            == 'The tag "mission" has been gifted to 9'
        )
        assert saved(tmp_path)["tags"]["mission"] == {"owner": 9, "content": "updated content"}
        assert (
            await tags.parse_commands(command("edit", "mission", "blocked"))
            == 'You are not the owner of the tag "mission"'
        )
        assert (
            await tags.parse_commands(command("delete", "mission", "ignored", owner=9))
            == 'The tag "mission" has been deleted'
        )
        assert saved(tmp_path)["tags"] == {"taken": {"owner": 7, "content": "already here"}}

    run(exercise())


@pytest.mark.parametrize(
    "mutation",
    [("edit", "missing", "value"), ("rename", "missing", "new"), ("delete", "missing"), ("gift", "missing", "9")],
)
def test_empty_and_missing_tag_mutation_responses(tmp_path: Path, mutation: tuple[str, ...]) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        assert await tags.parse_commands(command(*mutation)) == "There are no tags"
        await tags.parse_commands(command("create", "present", "value"))
        assert await tags.parse_commands(command(*mutation)) == 'The tag "missing" does not exist'

    run(exercise())


@pytest.mark.parametrize("new_owner", ["42", "<@42>", "<@!42>", " 42 "])
def test_gift_accepts_raw_ids_and_mentions(tmp_path: Path, new_owner: str) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        await tags.parse_commands(command("create", "launch", "go now"))
        assert (
            await tags.parse_commands(command("gift", "launch", new_owner)) == 'The tag "launch" has been gifted to 42'
        )
        assert saved(tmp_path)["tags"]["launch"] == {"owner": 42, "content": "go now"}

    run(exercise())


@pytest.mark.parametrize("new_owner", ["alice", "<@alice>", "", "<@&42>", "-1"])
def test_gift_rejects_invalid_ids_before_mutation(tmp_path: Path, new_owner: str) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        before = (tmp_path / "101.json").read_bytes()
        assert (
            await tags.parse_commands(command("gift", "missing", new_owner))
            == "The new owner must be a Discord user mention or user id"
        )
        assert (tmp_path / "101.json").read_bytes() == before

    run(exercise())


def test_list_filter_order_matching_and_empty_responses(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        assert await tags.parse_commands(command("list")) == "No tags exist"
        assert await tags.parse_commands(command("filter", "launch")) == "No tags contain the word launch"
        for name in ["z-launch", "Launch", "a-launch"]:
            await tags.parse_commands(command("create", name, "go now"))
        assert await tags.parse_commands(command("list", "ignored")) == "z-launch, Launch, a-launch"
        assert await tags.parse_commands(command("filter", "launch", "ignored")) == "z-launch, a-launch"
        assert await tags.parse_commands(command("filter", "Launch")) == "Launch"
        await tags.parse_commands(command("rename", "z-launch", "renamed"))
        assert await tags.parse_commands(command("list")) == "Launch, a-launch, renamed"
        loaded = await make_tags(tmp_path)
        assert await loaded.parse_commands(command("list")) == "Launch, a-launch, renamed"

    run(exercise())


def test_content_list_filter_truncation_and_guild_limit(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        await tags.parse_commands(command("create", "long", "a" * (DISCORD_MESSAGE_LIMIT + 50)))
        content = await tags.parse_commands(command("long"))
        assert content == "a" * (DISCORD_MESSAGE_LIMIT - len(TRUNCATION_SUFFIX)) + TRUNCATION_SUFFIX
        assert saved(tmp_path)["tags"]["long"]["content"] == content
        await tags.parse_commands(command("edit", "long", "b" * (DISCORD_MESSAGE_LIMIT + 50)))
        content = await tags.parse_commands(command("long"))
        assert content == "b" * (DISCORD_MESSAGE_LIMIT - len(TRUNCATION_SUFFIX)) + TRUNCATION_SUFFIX
        assert saved(tmp_path)["tags"]["long"]["content"] == content
        for index in range(MAX_TAGS_PER_GUILD - 1):
            await tags.parse_commands(command("create", f"launch-tag-{index:03}", "go now"))
        for parameters in [command("list"), command("filter", "launch")]:
            result = await tags.parse_commands(parameters)
            assert len(result) == DISCORD_MESSAGE_LIMIT
            assert result.endswith(TRUNCATION_SUFFIX)
        before = (tmp_path / "101.json").read_bytes()
        assert (
            await tags.parse_commands(command("create", "overflow", "value"))
            == "Tag limit reached. Delete a tag before creating a new one. Limit: 200"
        )
        assert (tmp_path / "101.json").read_bytes() == before

    run(exercise())


def test_owner_lookup_uses_existing_adapter_and_missing_tag_does_not_fetch(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        fetched: list[int] = []

        async def fetch(user_id: int) -> SimpleNamespace:
            fetched.append(user_id)
            return SimpleNamespace(name="Alice")

        assert (
            await tags.parse_commands(command("owner", "missing", fetch_user=fetch))
            == 'The tag "missing" does not exist'
        )
        assert fetched == []
        await tags.parse_commands(command("create", "launch", "go now"))
        assert (
            await tags.parse_commands(command("owner", "launch", fetch_user=fetch)) == 'The owner of "launch" is Alice'
        )
        assert fetched == [7]
        assert (
            await tags.parse_commands(command("owner", "launch"))
            == 'Ran into an error when trying to get the owner of "launch"'
        )
        assert (
            await tags.parse_commands(command("owner", "missing"))
            == 'Ran into an error when trying to get the owner of "missing"'
        )

    run(exercise())


@pytest.mark.parametrize("error", [discord.NotFound, discord.HTTPException])
def test_discord_lookup_failures_preserve_response(tmp_path: Path, error: type[discord.HTTPException]) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        await tags.parse_commands(command("create", "launch", "go now"))

        async def fetch(_user_id: int) -> discord.User:
            raise error(SimpleNamespace(status=404, reason="Not Found"), "lookup failed")

        assert (
            await tags.parse_commands(command("owner", "launch", fetch_user=fetch))
            == 'Ran into an error when trying to get the owner of "launch"'
        )

    run(exercise())


@pytest.mark.parametrize("error", [asyncio.CancelledError, RuntimeError])
def test_non_discord_lookup_errors_propagate_without_writing(tmp_path: Path, error: type[BaseException]) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        await tags.parse_commands(command("create", "launch", "go now"))
        before = (tmp_path / "101.json").read_bytes()

        async def fetch(_user_id: int) -> discord.User:
            raise error

        with pytest.raises(error):
            await tags.parse_commands(command("owner", "launch", fetch_user=fetch))
        assert (tmp_path / "101.json").read_bytes() == before

    run(exercise())


def test_concurrent_mutations_keep_all_records(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        results = await asyncio.gather(
            *(tags.parse_commands(command("create", f"tag-{index}", "original")) for index in range(20))
        )
        assert results == [f'The tag "tag-{index}" was created successfully' for index in range(20)]
        await asyncio.gather(*(tags.parse_commands(command("edit", f"tag-{index}", "updated")) for index in range(20)))
        assert saved(tmp_path)["tags"] == {f"tag-{index}": {"owner": 7, "content": "updated"} for index in range(20)}
        results = await asyncio.gather(*(tags.parse_commands(command("create", "shared", "value")) for _ in range(2)))
        assert results == ['The tag "shared" was created successfully', 'The tag "shared" already exists']

    run(exercise())


@pytest.mark.parametrize(
    ("source", "error"),
    [("{bad json", json.JSONDecodeError), ('{"id":101,"name":"Guild","tags":{"broken":{}}}', ValidationError)],
)
def test_malformed_files_remain_unchanged(tmp_path: Path, source: str, error: type[Exception]) -> None:
    path = tmp_path / "101.json"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(error):
        run(make_tags(tmp_path))
    assert path.read_text(encoding="utf-8") == source


def test_persisted_legacy_values_load_without_rewriting(tmp_path: Path) -> None:
    source = {"name": "Old Guild", "id": "101", "tags": {"launch": {"owner": "7", "content": "go now"}}}
    path = tmp_path / "101.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        assert await tags.parse_commands(command("launch")) == "go now"
        assert saved(tmp_path) == source
        assert await tags.parse_commands(command("edit", "launch", "updated")) == 'The tag "launch" has been edited'
        assert saved(tmp_path) == {
            "name": "Old Guild",
            "id": 101,
            "tags": {"launch": {"owner": 7, "content": "updated"}},
        }

    run(exercise())


def test_owner_lookup_does_not_hold_mutation_lock_while_waiting_for_discord(tmp_path: Path) -> None:
    async def exercise() -> None:
        tags = await make_tags(tmp_path)
        await tags.parse_commands(command("create", "launch", "go now"))
        entered = asyncio.Event()
        release = asyncio.Event()
        fetched: list[int] = []

        async def fetch(user_id: int) -> SimpleNamespace:
            fetched.append(user_id)
            entered.set()
            await release.wait()
            return SimpleNamespace(name="Alice")

        lookup = asyncio.create_task(tags.parse_commands(command("owner", "launch", fetch_user=fetch)))
        await asyncio.wait_for(entered.wait(), timeout=1)
        result = await asyncio.wait_for(tags.parse_commands(command("gift", "launch", "9")), timeout=1)
        assert result == 'The tag "launch" has been gifted to 9'
        release.set()
        assert await lookup == 'The owner of "launch" is Alice'
        assert fetched == [7]
        assert saved(tmp_path)["tags"]["launch"]["owner"] == 9

    run(exercise())
