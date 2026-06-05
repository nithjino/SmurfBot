"""Tests for runtime settings loading."""

from __future__ import annotations

import pytest

import settings
from settings import Settings, VaultSettings, collect_smurfbot_env, extract_secret_value, load_settings


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
            "SMURFBOT_VAULT_URL": "https://vault.example.com",
        }
    )

    assert loaded_settings == Settings(
        delim="!",
        consume_time=0.2,
        refresh_group_interval=300,
        discord_token=expected_value,
    )


def test_load_settings_fetches_discord_token_from_vault_when_env_token_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_vault_settings: list[VaultSettings] = []

    def read_secret(vault_settings: VaultSettings) -> dict[str, object]:
        observed_vault_settings.append(vault_settings)
        return {"data": {"data": {"discord_token": "vault-token"}}}

    monkeypatch.setattr(settings, "read_vault_secret", read_secret)

    loaded_settings = load_settings(
        {
            "SMURFBOT_VAULT_URL": "https://vault.example.com",
            "SMURFBOT_VAULT_TOKEN": "vault-auth-token",
        }
    )

    assert loaded_settings.discord_token.get_secret_value() == "vault-token"
    assert observed_vault_settings[0].url == "https://vault.example.com"
    assert observed_vault_settings[0].token.get_secret_value() == "vault-auth-token"


def test_load_settings_always_uses_discord_token_vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_vault_settings: list[VaultSettings] = []
    configured_path = "custom/data/smurfbot"

    def read_secret(vault_settings: VaultSettings) -> dict[str, object]:
        observed_vault_settings.append(vault_settings)
        return {"data": {"data": {"custom_token_key": "wrong-token", "discord_token": "right-token"}}}

    monkeypatch.setattr(settings, "read_vault_secret", read_secret)

    loaded_settings = load_settings(
        {
            "SMURFBOT_VAULT_URL": "https://vault.example.com",
            "SMURFBOT_VAULT_TOKEN": "vault-auth-token",
            "SMURFBOT_VAULT_TOKEN_PATH": configured_path,
        }
    )

    assert loaded_settings.discord_token.get_secret_value() == "right-token"
    assert observed_vault_settings[0].discord_token_path == configured_path


def test_load_settings_uses_standard_vault_token_env_when_smurfbot_vault_token_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_vault_settings: list[VaultSettings] = []

    def read_secret(vault_settings: VaultSettings) -> dict[str, object]:
        observed_vault_settings.append(vault_settings)
        return {"data": {"data": {"discord_token": "vault-token"}}}

    monkeypatch.setattr(settings, "read_vault_secret", read_secret)

    load_settings(
        {
            "SMURFBOT_VAULT_URL": "https://vault.example.com",
            "VAULT_TOKEN": "standard-vault-token",
        }
    )

    assert observed_vault_settings[0].token.get_secret_value() == "standard-vault-token"


def test_load_settings_requires_vault_url_when_direct_discord_token_is_missing() -> None:
    with pytest.raises(RuntimeError, match="SMURFBOT_VAULT_URL or HASHICORP_VAULT_URL is required"):
        load_settings({})


def test_extract_secret_value_supports_vault_kv_v1_and_kv_v2_payloads() -> None:
    assert extract_secret_value({"data": {"discord_token": "kv1-token"}}, "discord_token") == "kv1-token"
    assert extract_secret_value({"data": {"data": {"discord_token": "kv2-token"}}}, "discord_token") == "kv2-token"
    assert extract_secret_value({"data": {"other_key": "missing"}}, "discord_token") is None
