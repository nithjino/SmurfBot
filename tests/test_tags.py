"""Tests for tag command behavior and persistence."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

from message_limits import DISCORD_MESSAGE_LIMIT, TRUNCATION_SUFFIX
from tags.models import TagContent, TagRecord
from tags.tags import MAX_TAGS_PER_GUILD, Tags

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path


def run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def make_tags(tmp_path: Path) -> Tags:
    return Tags(SimpleNamespace(id=101, name="Guild"), tmp_path)


def test_create_tag_uses_attachment_or_text_and_persists(tmp_path: Path) -> None:
    tags = make_tags(tmp_path)

    text_result = run(tags.create_tag("launch", TagContent(message="go now", attachment=None), owner=7))
    attachment_result = run(
        tags.create_tag("diagram", TagContent(message="ignored text", attachment="https://example.test/d.png"), owner=7)
    )
    empty_result = run(tags.create_tag("empty", TagContent(message="   ", attachment=None), owner=7))

    assert text_result == 'The tag "launch" was created successfully'
    assert attachment_result == 'The tag "diagram" was created successfully'
    assert empty_result == "A tag needs either text content or an attachment"
    assert run(tags.post_tag("launch")) == "go now"
    assert run(tags.post_tag("diagram")) == "https://example.test/d.png"

    saved = json.loads((tmp_path / "101.json").read_text(encoding="utf-8"))
    assert saved["tags"]["launch"] == {"owner": 7, "content": "go now"}
    assert saved["tags"]["diagram"] == {"owner": 7, "content": "https://example.test/d.png"}
    assert "empty" not in saved["tags"]


def test_tag_mutations_enforce_ownership_and_keep_names_consistent(tmp_path: Path) -> None:
    tags = make_tags(tmp_path)
    tags.tags.tags["launch"] = TagRecord(owner=7, content="go now")
    tags.tags.tags["taken"] = TagRecord(owner=7, content="already here")

    assert run(tags.edit_tag("launch", owner=8, content="blocked")) == 'You are not the owner of the tag "launch"'
    assert run(tags.edit_tag("launch", owner=7, content="updated")) == 'The tag "launch" has been edited'
    assert run(tags.rename_tag("launch", "taken", owner=7)) == 'The tag "taken" already exists'
    assert run(tags.rename_tag("launch", "mission", owner=7)) == 'The tag "launch" has been renamed to "mission"'
    assert run(tags.delete_tag("mission", owner=8)) == 'You are not the owner of the tag "mission"'
    assert run(tags.gift_tag("mission", owner=7, new_owner=9)) == 'The tag "mission" has been gifted to 9'

    assert "launch" not in tags.tags.tags
    assert tags.tags.tags["mission"].content == "updated"
    assert tags.tags.tags["mission"].owner == 9


def test_parse_commands_routes_known_handlers_and_posts_unknown_tags(tmp_path: Path) -> None:
    tags = make_tags(tmp_path)
    tags.tags.tags["launch"] = TagRecord(owner=7, content="go now")
    base_parameters = {"author_id": 7, "attachment": None, "fetch_user_func": None}

    assert run(tags.parse_commands({**base_parameters, "message": []})).startswith("To create a tag")
    assert run(tags.parse_commands({**base_parameters, "message": ["list"]})) == "launch"
    assert run(tags.parse_commands({**base_parameters, "message": ["launch"]})) == "go now"
    assert run(tags.parse_commands({**base_parameters, "message": ["missing"]})) == 'The tag "missing" does not exist'


def test_create_and_edit_tag_truncate_content_to_discord_limit(tmp_path: Path) -> None:
    tags = make_tags(tmp_path)
    long_content = "a" * (DISCORD_MESSAGE_LIMIT + 50)
    edited_content = "b" * (DISCORD_MESSAGE_LIMIT + 50)

    create_result = run(tags.create_tag("long", TagContent(message=long_content, attachment=None), owner=7))
    edit_result = run(tags.edit_tag("long", owner=7, content=edited_content))
    posted_content = run(tags.post_tag("long"))

    assert create_result == 'The tag "long" was created successfully'
    assert edit_result == 'The tag "long" has been edited'
    assert len(posted_content) == DISCORD_MESSAGE_LIMIT
    assert posted_content.endswith(TRUNCATION_SUFFIX)
    assert tags.tags.tags["long"].content == posted_content


def test_list_and_filter_tags_truncate_to_discord_limit(tmp_path: Path) -> None:
    tags = make_tags(tmp_path)
    for index in range(300):
        tags.tags.tags[f"launch-tag-{index:03d}"] = TagRecord(owner=7, content="go now")

    listed_tags = run(tags.list_tags())
    filtered_tags = run(tags.filter_tags("launch"))

    assert len(listed_tags) == DISCORD_MESSAGE_LIMIT
    assert listed_tags.endswith(TRUNCATION_SUFFIX)
    assert len(filtered_tags) == DISCORD_MESSAGE_LIMIT
    assert filtered_tags.endswith(TRUNCATION_SUFFIX)


def test_create_tag_rejects_new_tags_when_guild_limit_is_reached(tmp_path: Path) -> None:
    tags = make_tags(tmp_path)
    for index in range(MAX_TAGS_PER_GUILD):
        tags.tags.tags[f"tag-{index}"] = TagRecord(owner=7, content="go now")

    result = run(tags.create_tag("overflow", TagContent(message="go now", attachment=None), owner=7))

    assert result == f"Tag limit reached. Delete a tag before creating a new one. Limit: {MAX_TAGS_PER_GUILD}"
    assert "overflow" not in tags.tags.tags
