"""Tests for runtime settings loading."""

from __future__ import annotations

import json
from io import BytesIO

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
            "SMURFBOT_VAULT_USERNAME": "smurfbot",
            "SMURFBOT_VAULT_PASSWORD": "vault-password",
        }
    )

    assert loaded_settings.discord_token.get_secret_value() == "vault-token"
    assert observed_vault_settings[0].url == "https://vault.example.com"
    assert observed_vault_settings[0].username == "smurfbot"
    assert observed_vault_settings[0].password.get_secret_value() == "vault-password"


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
            "SMURFBOT_VAULT_USERNAME": "smurfbot",
            "SMURFBOT_VAULT_PASSWORD": "vault-password",
            "SMURFBOT_VAULT_TOKEN_PATH": configured_path,
        }
    )

    assert loaded_settings.discord_token.get_secret_value() == "right-token"
    assert observed_vault_settings[0].discord_token_path == configured_path


def test_load_settings_uses_hashicorp_userpass_env_when_smurfbot_credentials_are_missing(
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
            "HASHICORP_VAULT_USERNAME": "hashicorp-user",
            "HASHICORP_VAULT_PASSWORD": "hashicorp-password",
        }
    )

    assert observed_vault_settings[0].username == "hashicorp-user"
    assert observed_vault_settings[0].password.get_secret_value() == "hashicorp-password"


def test_load_settings_requires_vault_url_when_direct_discord_token_is_missing() -> None:
    with pytest.raises(RuntimeError, match="SMURFBOT_VAULT_URL or HASHICORP_VAULT_URL is required"):
        load_settings({})


def test_load_settings_requires_vault_username_and_password() -> None:
    with pytest.raises(RuntimeError, match="a Vault username and password are required"):
        load_settings({"SMURFBOT_VAULT_URL": "https://vault.example.com"})


def test_read_vault_secret_authenticates_with_userpass_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_value = "vault-password"
    requests: list[settings.request.Request] = []
    responses = [
        BytesIO(b'{"auth":{"client_token":"temporary-vault-token"}}'),
        BytesIO(b'{"data":{"data":{"discord_token":"discord-token"}}}'),
    ]

    class StubOpener:
        def open(self, vault_request: settings.request.Request, timeout: int) -> BytesIO:
            requests.append(vault_request)
            assert timeout == 10
            return responses.pop(0)

    monkeypatch.setattr(settings.request, "build_opener", lambda *_handlers: StubOpener())

    vault_settings = VaultSettings(
        vault_url="https://vault.example.com",
        vault_username="user/name",
        vault_password=configured_value,
        vault_userpass_mount="company/userpass",
    )
    response = settings.read_vault_secret(vault_settings)

    assert response == {"data": {"data": {"discord_token": "discord-token"}}}
    assert requests[0].full_url == "https://vault.example.com/v1/auth/company/userpass/login/user%2Fname"
    assert requests[0].get_method() == "POST"
    assert json.loads(requests[0].data or b"") == {"password": configured_value}
    assert requests[0].get_header("Content-type") == "application/json"
    assert requests[1].full_url == "https://vault.example.com/v1/smurfbot/data/tokens"
    assert requests[1].get_header("X-vault-token") == "temporary-vault-token"


def test_userpass_login_requires_client_token(monkeypatch: pytest.MonkeyPatch) -> None:
    configured_value = "vault-password"

    class StubOpener:
        def open(self, _vault_request: settings.request.Request, *, timeout: int) -> BytesIO:
            assert timeout == 10
            return BytesIO(b'{"auth":{}}')

    monkeypatch.setattr(settings.request, "build_opener", lambda *_handlers: StubOpener())
    vault_settings = VaultSettings(
        vault_url="https://vault.example.com",
        vault_username="smurfbot",
        vault_password=configured_value,
    )

    with pytest.raises(RuntimeError, match="did not contain a client token"):
        settings.authenticate_vault_userpass(vault_settings)


def test_extract_secret_value_supports_vault_kv_v1_and_kv_v2_payloads() -> None:
    assert extract_secret_value({"data": {"discord_token": "kv1-token"}}, "discord_token") == "kv1-token"
    assert extract_secret_value({"data": {"data": {"discord_token": "kv2-token"}}}, "discord_token") == "kv2-token"
    assert extract_secret_value({"data": {"other_key": "missing"}}, "discord_token") is None


def test_vault_url_requires_https_except_for_private_networks_and_loopback() -> None:
    configured_value = "test-value"
    secure = VaultSettings(
        vault_url="https://vault.example.com",
        vault_username="user",
        vault_password=configured_value,
    )
    loopback = VaultSettings(
        vault_url="http://127.0.0.1:8200",
        vault_username="user",
        vault_password=configured_value,
    )
    private_network = VaultSettings(
        vault_url="http://192.168.2.10:8200",
        vault_username="user",
        vault_password=configured_value,
    )

    assert secure.url == "https://vault.example.com"
    assert loopback.url == "http://127.0.0.1:8200"
    assert private_network.url == "http://192.168.2.10:8200"

    with pytest.raises(ValueError, match="must use HTTPS"):
        VaultSettings(vault_url="http://vault.example.com", vault_username="user", vault_password=configured_value)
    with pytest.raises(ValueError, match="must not contain credentials"):
        VaultSettings(
            vault_url="https://user:password@vault.example.com",
            vault_username="user",
            vault_password=configured_value,
        )
