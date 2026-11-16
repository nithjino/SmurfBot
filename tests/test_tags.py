from typing import Any

import pytest

from smurfbot.store import Store, Tag
from smurfbot.tags import HELP_TEXT, MAX_TAGS_PER_GUILD, RESERVED_NAMES, parse_user, tag_command
from tests.helpers import AUTHOR, GUILD, make_dm, make_request

OTHER = 2


def tag(store: Store, text: str, **overrides: Any) -> str:
    return tag_command(store, make_request(**overrides), text.split())


@pytest.fixture
def seeded(store: Store) -> Store:
    store.state.tags[GUILD] = {"cool": Tag(owner=AUTHOR, content="a cool tag"), "other": Tag(owner=OTHER, content="x")}
    store.save()
    return store


@pytest.mark.parametrize("text", ["", "help", "help me"])
def test_help(store: Store, text: str):
    assert tag(store, text) == HELP_TEXT


def test_help_text_wording():
    assert HELP_TEXT == (
        'To create a tag: "tag create [name]" while uploading a picture or by providing a link or text after the name. '
        'To post an already created tag: "tag [name]". To see what tags are made: "tag list". To edit a tag: '
        '"tag edit [name] [new material]". To rename a tag: "tag rename [current name] [new name]".'
    )


@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("create", "Usage: tag create [name] [content]"),
        ("edit", "Usage: tag edit [name] [new material]"),
        ("edit cool", "Usage: tag edit [name] [new material]"),
        ("delete", "Usage: tag delete [name]"),
        ("rename", "Usage: tag rename [current name] [new name]"),
        ("rename cool", "Usage: tag rename [current name] [new name]"),
        ("gift", "Usage: tag gift [name] [new owner mention or id]"),
        ("gift cool", "Usage: tag gift [name] [new owner mention or id]"),
        ("owner", "Usage: tag owner [name]"),
        ("filter", "Usage: tag filter [keyword]"),
    ],
)
def test_usage(seeded: Store, text: str, reply: str):
    assert tag(seeded, text) == reply


def test_show(seeded: Store):
    assert tag(seeded, "cool") == "a cool tag"
    assert tag(seeded, "cool and more words") == "a cool tag"
    assert tag(seeded, "missing") == 'The tag "missing" does not exist'


def test_names_and_subcommands_are_case_sensitive(seeded: Store):
    assert tag(seeded, "COOL") == 'The tag "COOL" does not exist'
    assert tag(seeded, "CREATE x y") == 'The tag "CREATE" does not exist'
    assert tag(seeded, "create Cool text") == 'The tag "Cool" was created successfully'
    assert tag(seeded, "Cool") == "text"


def test_create(seeded: Store):
    assert tag(seeded, "create new some  words here") == 'The tag "new" was created successfully'
    assert seeded.state.tags[GUILD]["new"] == Tag(owner=AUTHOR, content="some words here")
    assert list(seeded.state.tags[GUILD]) == ["cool", "other", "new"]


def test_create_refusals(seeded: Store):
    assert tag(seeded, "create cool again") == 'The tag "cool" already exists'
    assert tag(seeded, "create empty") == "A tag needs either text content or an attachment"
    assert tag(seeded, "create empty", attachment_url="") == "A tag needs either text content or an attachment"
    assert "empty" not in seeded.state.tags[GUILD]


@pytest.mark.parametrize("name", sorted(RESERVED_NAMES))
def test_reserved_names_are_refused(seeded: Store, name: str):
    assert tag(seeded, f"create {name} text") == f'The tag name "{name}" is reserved for a tag command'
    assert tag(seeded, f"rename cool {name}") == f'The tag name "{name}" is reserved for a tag command'
    assert set(seeded.state.tags[GUILD]) == {"cool", "other"}


def test_reserved_names_are_exactly_the_subcommands():
    assert {"create", "delete", "edit", "filter", "gift", "help", "list", "owner", "rename"} == RESERVED_NAMES


def test_attachment_wins_over_text(seeded: Store):
    url = "https://cdn.example/pic.png"
    assert tag(seeded, "create pic ignored", attachment_url=url) == 'The tag "pic" was created successfully'
    assert seeded.state.tags[GUILD]["pic"].content == url
    assert tag(seeded, "edit cool ignored", attachment_url=url) == 'The tag "cool" has been edited'
    assert tag(seeded, "edit pic", attachment_url=url + "?2") == 'The tag "pic" has been edited'
    assert seeded.state.tags[GUILD]["cool"].content == url
    assert seeded.state.tags[GUILD]["pic"].content == url + "?2"


def test_limit(store: Store):
    store.state.tags[GUILD] = {f"t{i}": Tag(owner=AUTHOR, content="c") for i in range(MAX_TAGS_PER_GUILD - 1)}
    assert MAX_TAGS_PER_GUILD == 500
    assert tag(store, "create last c") == 'The tag "last" was created successfully'
    assert tag(store, "create over c") == "Tag limit reached. Delete a tag before creating a new one. Limit: 500"
    assert tag(store, "create t1 c") == 'The tag "t1" already exists'
    assert len(store.state.tags[GUILD]) == 500
    assert tag(store, "create over c", guild_id=GUILD + 1) == 'The tag "over" was created successfully'


def test_edit(seeded: Store):
    assert tag(seeded, "edit cool new  text") == 'The tag "cool" has been edited'
    assert tag(seeded, "cool") == "new text"
    assert tag(seeded, "edit missing x") == 'The tag "missing" does not exist'


def test_delete(seeded: Store):
    assert tag(seeded, "delete cool") == 'The tag "cool" has been deleted'
    assert "cool" not in seeded.state.tags[GUILD]
    assert tag(seeded, "delete cool") == 'The tag "cool" does not exist'


def test_rename(seeded: Store):
    assert tag(seeded, "rename cool cooler") == 'The tag "cool" has been renamed to "cooler"'
    assert tag(seeded, "cooler") == "a cool tag"
    assert tag(seeded, "cool") == 'The tag "cool" does not exist'
    assert tag(seeded, "rename missing x") == 'The tag "missing" does not exist'
    seeded.state.tags[GUILD]["mine"] = Tag(owner=AUTHOR, content="m")
    assert tag(seeded, "rename cooler mine") == 'The tag "mine" already exists'
    assert tag(seeded, "rename cooler cooler") == 'The tag "cooler" already exists'


def test_gift(seeded: Store):
    assert tag(seeded, "gift cool <@42>") == 'The tag "cool" has been gifted to <@42>'
    assert seeded.state.tags[GUILD]["cool"].owner == 42
    assert tag(seeded, "gift cool 7") == 'You are not the owner of the tag "cool"'
    assert tag(seeded, "edit cool new", author_id=42) == 'The tag "cool" has been edited'
    assert tag(seeded, "gift missing 42") == 'The tag "missing" does not exist'
    assert tag(seeded, "gift missing alice") == "The new owner must be a Discord user mention or user id"


@pytest.mark.parametrize(("text", "user_id"), [("42", 42), ("<@42>", 42), ("<@!42>", 42)])
def test_parse_user_accepts(text: str, user_id: int):
    assert parse_user(text) == user_id


@pytest.mark.parametrize("text", ["alice", "<@alice>", "<@&42>", "-1", "<@42", "42>", "", "\uff14\uff12"])
def test_parse_user_rejects(seeded: Store, text: str):
    assert parse_user(text) is None
    if text:
        assert tag(seeded, f"gift cool {text}") == "The new owner must be a Discord user mention or user id"


@pytest.mark.parametrize("text", ["edit other x", "delete other", "rename other x", "gift other 1"])
def test_only_the_owner_can_change_a_tag(seeded: Store, text: str):
    before = seeded.path.read_bytes()
    assert tag(seeded, text) == 'You are not the owner of the tag "other"'
    assert seeded.path.read_bytes() == before
    assert seeded.state.tags[GUILD]["other"] == Tag(owner=OTHER, content="x")


def test_owner(seeded: Store):
    assert tag(seeded, "owner other") == 'The owner of "other" is <@2>'
    assert tag(seeded, "owner missing") == 'The tag "missing" does not exist'


def test_list(seeded: Store):
    seeded.state.tags[GUILD]["alpha"] = Tag(owner=AUTHOR, content="a")
    assert tag(seeded, "list") == "cool, other, alpha"
    assert tag(seeded, "list", guild_id=GUILD + 1) == "No tags exist"
    assert GUILD + 1 not in seeded.state.tags


def test_filter(seeded: Store):
    seeded.state.tags[GUILD]["Cooler"] = Tag(owner=AUTHOR, content="a")
    assert tag(seeded, "filter oo") == "cool, Cooler"
    assert tag(seeded, "filter Coo") == "Cooler"
    assert tag(seeded, "filter zzz") == "No tags contain the word zzz"


def test_tags_are_separate_per_guild(seeded: Store):
    assert tag(seeded, "cool", guild_id=GUILD + 1) == 'The tag "cool" does not exist'
    assert tag(seeded, "create cool b", guild_id=GUILD + 1) == 'The tag "cool" was created successfully'
    assert tag(seeded, "cool") == "a cool tag"
    assert tag(seeded, "cool", guild_id=GUILD + 1) == "b"


def test_tags_survive_a_reload(seeded: Store):
    tag(seeded, "create new text")
    tag(seeded, "gift cool 42")
    reloaded = Store(seeded.path)
    assert reloaded.state.tags == seeded.state.tags
    assert tag(reloaded, "new") == "text"


@pytest.mark.parametrize("text", ["", "help"])
def test_help_works_in_a_dm(store: Store, text: str):
    assert tag_command(store, make_dm(), text.split()) == HELP_TEXT


@pytest.mark.parametrize(
    "text", ["cool", "create x y", "edit x y", "delete x", "rename x y", "gift x 1", "owner x", "list", "filter x"]
)
def test_everything_else_is_refused_in_a_dm(store: Store, text: str):
    assert tag_command(store, make_dm(), text.split()) == "Tags only work in a server."
    assert not store.path.exists()
