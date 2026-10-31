from pathlib import Path

import pytest
from pydantic import ValidationError

from smurfbot.config import DEFAULT_DATA_DIR, Settings, load_settings


def test_loads_prefixed_variables_and_ignores_others() -> None:
    settings = load_settings(
        {
            "SMURFBOT_DELIM": "!",
            "SMURFBOT_DISCORD_TOKEN": "env-token",
            "SMURFBOT_DATA_DIR": "/srv/smurf",
            "SMURFBOT_LOG_LEVEL": "debug",
            "OTHER_VALUE": "ignored",
        }
    )

    assert settings == Settings(
        delim="!",
        discord_token="env-token",  # pyright: ignore[reportArgumentType]
        data_dir=Path("/srv/smurf"),
        log_level="DEBUG",
    )


def test_defaults() -> None:
    settings = load_settings({"SMURFBOT_DISCORD_TOKEN": "t"})

    assert settings.delim == "$"
    assert settings.data_dir == DEFAULT_DATA_DIR
    assert settings.log_level == "INFO"


def test_retired_variables_from_old_deployments_are_ignored() -> None:
    settings = load_settings(
        {"SMURFBOT_DISCORD_TOKEN": "t", "SMURFBOT_CONSUME_TIME": ".1", "SMURFBOT_REFRESH_GROUP_INTERVAL": "600"}
    )

    assert settings.discord_token.get_secret_value() == "t"


def test_empty_data_dir_means_default() -> None:
    assert load_settings({"SMURFBOT_DISCORD_TOKEN": "t", "SMURFBOT_DATA_DIR": ""}).data_dir == DEFAULT_DATA_DIR


def test_data_dir_expands_home() -> None:
    assert load_settings({"SMURFBOT_DISCORD_TOKEN": "t", "SMURFBOT_DATA_DIR": "~/x"}).data_dir == Path.home() / "x"


@pytest.mark.parametrize(
    ("environment", "field"),
    [
        ({}, "discord_token"),
        ({"SMURFBOT_DISCORD_TOKEN": ""}, "discord_token"),
        ({"SMURFBOT_DISCORD_TOKEN": "t", "SMURFBOT_DELIM": ""}, "delim"),
        ({"SMURFBOT_DISCORD_TOKEN": "t", "SMURFBOT_LOG_LEVEL": "LOUD"}, "log_level"),
    ],
)
def test_invalid_settings_are_rejected(environment: dict[str, str], field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        load_settings(environment)
