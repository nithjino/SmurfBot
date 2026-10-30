"""Tests for runtime settings loading."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from settings import Settings, collect_smurfbot_env, load_settings


def test_collect_smurfbot_env_maps_prefixed_keys_to_lowercase_fields() -> None:
    values = collect_smurfbot_env(
        {
            "SMURFBOT_DELIM": "!",
            "SMURFBOT_CONSUME_TIME": ".2",
            "OTHER_VALUE": "ignored",
        }
    )

    assert values == {"delim": "!", "consume_time": ".2"}


def test_load_settings_uses_direct_discord_token_env() -> None:
    expected_value = "env-token"

    loaded_settings = load_settings(
        {
            "SMURFBOT_DELIM": "!",
            "SMURFBOT_CONSUME_TIME": ".2",
            "SMURFBOT_REFRESH_GROUP_INTERVAL": "300",
            "SMURFBOT_DISCORD_TOKEN": expected_value,
            "SMURFBOT_DATA_DIR": "ignored",
        }
    )

    assert loaded_settings == Settings(
        delim="!",
        consume_time=0.2,
        refresh_group_interval=300,
        discord_token=expected_value,
    )


def test_load_settings_requires_discord_token() -> None:
    with pytest.raises(ValidationError, match="discord_token"):
        load_settings({})


def test_load_settings_rejects_empty_discord_token() -> None:
    with pytest.raises(ValidationError, match="discord_token must not be empty"):
        load_settings({"SMURFBOT_DISCORD_TOKEN": ""})
