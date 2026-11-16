"""Settings read from environment variables."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigError(Exception):
    """A required setting is missing or invalid."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    """Runtime settings for the bot."""

    token: str = field(repr=False)
    prefix: str
    data_dir: Path
    log_level: str


def load_settings(environ: Mapping[str, str]) -> Settings:
    """Build settings from the environment, treating empty values as unset."""

    def get(name: str, default: str) -> str:
        return environ.get(name) or default

    token = get("SMURFBOT_DISCORD_TOKEN", "")
    if not token:
        raise ConfigError("SMURFBOT_DISCORD_TOKEN is required")
    log_level = get("SMURFBOT_LOG_LEVEL", "INFO").upper()
    if log_level not in LOG_LEVELS:
        raise ConfigError("SMURFBOT_LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    return Settings(
        token=token,
        prefix=get("SMURFBOT_DELIM", "$"),
        data_dir=Path(get("SMURFBOT_DATA_DIR", "data")).expanduser(),
        log_level=log_level,
    )
