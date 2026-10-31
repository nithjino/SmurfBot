import pytest

from smurfbot.text import DISCORD_MESSAGE_LIMIT, TRUNCATION_SUFFIX, truncate


def test_short_text_is_unchanged() -> None:
    assert truncate("hello") == "hello"
    assert truncate("a" * DISCORD_MESSAGE_LIMIT) == "a" * DISCORD_MESSAGE_LIMIT


def test_long_text_is_cut_to_the_limit_with_a_suffix() -> None:
    result = truncate("a" * (DISCORD_MESSAGE_LIMIT + 1))

    assert len(result) == DISCORD_MESSAGE_LIMIT
    assert result.endswith(TRUNCATION_SUFFIX)


@pytest.mark.parametrize(("limit", "expected"), [(0, ""), (2, ".."), (3, "..."), (4, "a...")])
def test_small_limits(limit: int, expected: str) -> None:
    assert truncate("abcdefgh", limit) == expected
