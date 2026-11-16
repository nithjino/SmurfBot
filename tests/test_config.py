from pathlib import Path

import pytest

from smurfbot.config import ConfigError, load_settings

TOKEN = "secret-token-value"


def test_reads_all_four_variables():
    settings = load_settings(
        {
            "SMURFBOT_DISCORD_TOKEN": TOKEN,
            "SMURFBOT_DELIM": "!",
            "SMURFBOT_DATA_DIR": "/srv/smurf",
            "SMURFBOT_LOG_LEVEL": "debug",
        }
    )
    assert settings.token == TOKEN
    assert settings.prefix == "!"
    assert settings.data_dir == Path("/srv/smurf")
    assert settings.log_level == "DEBUG"


def test_defaults():
    settings = load_settings({"SMURFBOT_DISCORD_TOKEN": TOKEN})
    assert (settings.prefix, settings.data_dir, settings.log_level) == ("$", Path("data"), "INFO")


def test_empty_values_fall_back_to_defaults():
    settings = load_settings(
        {
            "SMURFBOT_DISCORD_TOKEN": TOKEN,
            "SMURFBOT_DELIM": "",
            "SMURFBOT_DATA_DIR": "",
            "SMURFBOT_LOG_LEVEL": "",
        }
    )
    assert (settings.prefix, settings.data_dir, settings.log_level) == ("$", Path("data"), "INFO")


def test_a_space_prefix_is_kept():
    assert load_settings({"SMURFBOT_DISCORD_TOKEN": TOKEN, "SMURFBOT_DELIM": " "}).prefix == " "


def test_data_dir_expands_home():
    settings = load_settings({"SMURFBOT_DISCORD_TOKEN": TOKEN, "SMURFBOT_DATA_DIR": "~/smurf"})
    assert settings.data_dir == Path.home() / "smurf"


def test_unknown_variables_are_ignored():
    settings = load_settings({"SMURFBOT_DISCORD_TOKEN": TOKEN, "SMURFBOT_COLOR": "blue", "HOME": "/x"})
    assert settings.prefix == "$"


@pytest.mark.parametrize("token", [None, ""])
def test_missing_token_raises(token: str | None):
    environ = {"SMURFBOT_LOG_LEVEL": "INFO"} | ({} if token is None else {"SMURFBOT_DISCORD_TOKEN": token})
    with pytest.raises(ConfigError, match="SMURFBOT_DISCORD_TOKEN"):
        load_settings(environ)


def test_invalid_log_level_raises_without_the_value():
    with pytest.raises(ConfigError, match="SMURFBOT_LOG_LEVEL") as info:
        load_settings({"SMURFBOT_DISCORD_TOKEN": TOKEN, "SMURFBOT_LOG_LEVEL": "loud-and-proud"})
    assert "loud-and-proud" not in str(info.value).lower()
    assert TOKEN not in str(info.value)


def test_repr_hides_the_token():
    assert TOKEN not in repr(load_settings({"SMURFBOT_DISCORD_TOKEN": TOKEN}))
