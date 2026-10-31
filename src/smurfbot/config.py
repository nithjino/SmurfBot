"""Runtime settings loaded from SMURFBOT_* environment variables."""

import os
from pathlib import Path
from typing import Final, Literal
lazy from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

ENV_PREFIX: Final = "SMURFBOT_"
DEFAULT_DATA_DIR: Final = Path("data")

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseModel):
    """Everything the bot needs to know before connecting to Discord."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    discord_token: SecretStr = Field(min_length=1)
    delim: str = Field(default="$", min_length=1)
    data_dir: Path = DEFAULT_DATA_DIR
    log_level: LogLevel = "INFO"

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand_data_dir(cls, value: str | Path) -> Path:
        """Treat an empty value as unset, since Compose passes empty variables through."""
        return Path(value).expanduser() if value else DEFAULT_DATA_DIR

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        return value.upper()


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build settings from SMURFBOT_* variables, ignoring unrelated ones."""
    environment = os.environ if environ is None else environ
    return Settings.model_validate(
        {
            key.removeprefix(ENV_PREFIX).lower(): value
            for key, value in environment.items()
            if key.startswith(ENV_PREFIX)
        }
    )
