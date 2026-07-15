"""Runtime settings loaded from environment variables and Vault."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import Final
from urllib import error, request
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

ENV_PREFIX: Final[str] = "SMURFBOT_"
DEFAULT_VAULT_DISCORD_TOKEN_PATH: Final[str] = "smurfbot/data/tokens"  # noqa: S105
DEFAULT_VAULT_DISCORD_TOKEN_KEY: Final[str] = "discord_token"  # noqa: S105
HASHICORP_VAULT_URL_ENV_VAR: Final[str] = "HASHICORP_VAULT_URL"
HASHICORP_VAULT_TOKEN_ENV_VAR: Final[str] = "HASHICORP_VAULT_TOKEN"  # noqa: S105
VAULT_ADDR_ENV_VAR: Final[str] = "VAULT_ADDR"
VAULT_TOKEN_ENV_VAR: Final[str] = "VAULT_TOKEN"  # noqa: S105
MAX_VAULT_RESPONSE_BYTES: Final[int] = 1_048_576
LOCAL_VAULT_HOSTNAMES: Final[frozenset[str]] = frozenset({"localhost"})
PRIVATE_VAULT_NETWORKS: Final[tuple[IPv4Network | IPv6Network, ...]] = tuple(
    ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
SETTINGS_ENV_FIELDS: Final[tuple[str, ...]] = (
    "delim",
    "consume_time",
    "refresh_group_interval",
    "discord_token",
)


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
        """Require a Discord token from either env or Vault."""
        if not value.get_secret_value():
            msg = "discord_token must not be empty"
            raise ValueError(msg)
        return value


class VaultSettings(BaseModel):
    """Vault connection settings used when the Discord token is not set directly."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    url: str = Field(alias="vault_url")
    token: SecretStr = Field(alias="vault_token")
    discord_token_path: str = Field(default=DEFAULT_VAULT_DISCORD_TOKEN_PATH, alias="vault_token_path")

    @field_validator("discord_token_path")
    @classmethod
    def validate_non_empty_string(cls, value: str) -> str:
        """Require non-empty Vault connection values."""
        if not value:
            msg = "Vault settings must not be empty"
            raise ValueError(msg)
        return value

    @field_validator("url")
    @classmethod
    def validate_vault_url(cls, value: str) -> str:
        """Require HTTPS except for Vault servers addressed by a local IP."""
        parsed_url = urlsplit(value)
        if parsed_url.username is not None or parsed_url.password is not None:
            msg = "Vault URL must not contain credentials"
            raise ValueError(msg)
        if parsed_url.scheme == "https" and parsed_url.hostname:
            return value
        if parsed_url.scheme == "http" and is_local_vault_host(parsed_url.hostname):
            return value
        msg = "Vault URL must use HTTPS; HTTP is allowed only for private-network or loopback IP addresses"
        raise ValueError(msg)

    @field_validator("token")
    @classmethod
    def validate_vault_token(cls, value: SecretStr) -> SecretStr:
        """Require a token for authenticated Vault reads."""
        if not value.get_secret_value():
            msg = "vault_token must not be empty"
            raise ValueError(msg)
        return value


def is_local_vault_host(hostname: str | None) -> bool:
    """Return whether a Vault hostname is localhost, loopback, or a private IP address."""
    if hostname in LOCAL_VAULT_HOSTNAMES:
        return True
    if hostname is None:
        return False
    try:
        address = ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or any(address in network for network in PRIVATE_VAULT_NETWORKS)


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load settings from SMURFBOT_* env vars, resolving the Discord token from Vault if needed."""
    environment = environ if environ is not None else os.environ
    smurfbot_values = collect_smurfbot_env(environment)
    settings_values = {
        field_name: smurfbot_values[field_name] for field_name in SETTINGS_ENV_FIELDS if field_name in smurfbot_values
    }
    if not settings_values.get("discord_token"):
        settings_values["discord_token"] = fetch_discord_token_from_vault(smurfbot_values, environment)
    return Settings.model_validate(settings_values)


def collect_smurfbot_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Return SMURFBOT_* env vars as lower-case Pydantic field names."""
    return {key.removeprefix(ENV_PREFIX).lower(): value for key, value in environ.items() if key.startswith(ENV_PREFIX)}


def fetch_discord_token_from_vault(
    smurfbot_values: Mapping[str, str],
    environ: Mapping[str, str],
) -> str:
    """Fetch the Discord token from Vault using env-provided Vault settings."""
    vault_url = (
        smurfbot_values.get("vault_url") or environ.get(HASHICORP_VAULT_URL_ENV_VAR) or environ.get(VAULT_ADDR_ENV_VAR)
    )
    if not vault_url:
        msg = "SMURFBOT_DISCORD_TOKEN is not set, so SMURFBOT_VAULT_URL or HASHICORP_VAULT_URL is required"
        raise RuntimeError(msg)

    vault_token = (
        smurfbot_values.get("vault_token")
        or environ.get(HASHICORP_VAULT_TOKEN_ENV_VAR)
        or environ.get(VAULT_TOKEN_ENV_VAR)
    )
    if not vault_token:
        msg = (
            "SMURFBOT_DISCORD_TOKEN is not set, so SMURFBOT_VAULT_TOKEN, "
            "HASHICORP_VAULT_TOKEN, or VAULT_TOKEN is required"
        )
        raise RuntimeError(msg)

    vault_settings = VaultSettings.model_validate(
        {**smurfbot_values, "vault_url": vault_url, "vault_token": vault_token}
    )
    vault_response = read_vault_secret(vault_settings)
    discord_token = extract_secret_value(vault_response, DEFAULT_VAULT_DISCORD_TOKEN_KEY)
    if discord_token is None:
        msg = f"Vault secret {vault_settings.discord_token_path!r} did not contain {DEFAULT_VAULT_DISCORD_TOKEN_KEY!r}"
        raise RuntimeError(msg)
    return discord_token


class NoRedirectHandler(request.HTTPRedirectHandler):
    """Prevent Vault credentials from being forwarded through HTTP redirects."""

    def redirect_request(
        self,
        req: request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        """Reject redirects instead of constructing a follow-up request."""


def read_vault_secret(vault_settings: VaultSettings) -> Mapping[str, object]:
    """Read a secret from Vault's HTTP API."""
    vault_url = vault_settings.url.rstrip("/")
    vault_path = vault_settings.discord_token_path.lstrip("/")
    vault_request = request.Request(  # noqa: S310
        f"{vault_url}/v1/{vault_path}",
        headers={"X-Vault-Token": vault_settings.token.get_secret_value()},
    )
    opener = request.build_opener(NoRedirectHandler())
    try:
        with opener.open(vault_request, timeout=10) as response:
            response_body = response.read(MAX_VAULT_RESPONSE_BYTES + 1)
    except (TimeoutError, error.HTTPError, error.URLError) as exc:
        msg = f"Failed to read Discord token from Vault path {vault_settings.discord_token_path!r}"
        raise RuntimeError(msg) from exc

    if len(response_body) > MAX_VAULT_RESPONSE_BYTES:
        msg = f"Vault path {vault_settings.discord_token_path!r} returned an oversized response"
        raise RuntimeError(msg)
    payload: object = json.loads(response_body.decode("utf-8"))

    if not isinstance(payload, Mapping):
        msg = f"Vault path {vault_settings.discord_token_path!r} returned a non-object response"
        raise TypeError(msg)
    return payload


def extract_secret_value(vault_response: Mapping[str, object], key: str) -> str | None:
    """Return a secret value from Vault KV v1 or KV v2 response data."""
    data = vault_response.get("data")
    if not isinstance(data, Mapping):
        return None

    kv2_data = data.get("data")
    if isinstance(kv2_data, Mapping):
        kv2_value = kv2_data.get(key)
        if isinstance(kv2_value, str) and kv2_value:
            return kv2_value

    kv1_value = data.get(key)
    if isinstance(kv1_value, str) and kv1_value:
        return kv1_value
    return None
