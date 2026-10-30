"""Runtime settings loaded from environment variables."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

if TYPE_CHECKING:
    from collections.abc import Mapping

ENV_PREFIX: Final[str] = "SMURFBOT_"


class Settings(BaseModel):
    """Bot settings used before connecting to Discord."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    delim: str = "$"
    consume_time: float = 0.1
    refresh_group_interval: int = 600
    discord_token: SecretStr

    @field_validator("delim")
    @classmethod
    def validate_delim(cls, value: str) -> str:
        """Require a non-empty command delimiter."""
        if not value:
            msg = "delim must not be empty"
            raise ValueError(msg)
        return value

    @field_validator("consume_time")
    @classmethod
    def validate_consume_time(cls, value: float) -> float:
        """Require a positive consume time."""
        if value <= 0:
            msg = "consume_time must be greater than 0"
            raise ValueError(msg)
        return value

    @field_validator("refresh_group_interval")
    @classmethod
    def validate_refresh_group_interval(cls, value: int) -> int:
        """Require a positive refresh interval."""
        if value <= 0:
            msg = "refresh_group_interval must be greater than 0"
            raise ValueError(msg)
        return value

    @field_validator("discord_token")
    @classmethod
    def validate_discord_token(cls, value: SecretStr) -> SecretStr:
        """Require a non-empty Discord token."""
        if not value.get_secret_value():
            msg = "discord_token must not be empty"
            raise ValueError(msg)
        return value


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load settings from SMURFBOT_* env vars."""
    environment = environ if environ is not None else os.environ
    return Settings.model_validate(collect_smurfbot_env(environment))


def collect_smurfbot_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Return SMURFBOT_* env vars as lower-case Pydantic field names."""
    return {key.removeprefix(ENV_PREFIX).lower(): value for key, value in environ.items() if key.startswith(ENV_PREFIX)}
