"""Configuration management tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from kite_auto.config import (
    LogLevel,
    SessionStoreBackend,
    Settings,
    clear_settings_cache,
    get_settings,
)
from kite_auto.exceptions import ConfigurationError


def test_settings_load_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "KITE_API_KEY=api-key",
                "KITE_API_SECRET=api-secret",
                "KITE_USER_ID=user-id",
                "KITE_PASSWORD=password",
                "KITE_TOTP_SECRET=totp-secret",
                "HEADLESS=false",
                "SESSION_STORE=JSON",
                "LOG_LEVEL=debug",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings.from_env_file(env_file)

    assert settings.kite_api_key == "api-key"
    assert settings.kite_api_secret is not None
    assert settings.kite_api_secret.get_secret_value() == "api-secret"
    assert settings.kite_user_id == "user-id"
    assert settings.kite_password is not None
    assert settings.kite_password.get_secret_value() == "password"
    assert settings.kite_totp_secret is not None
    assert settings.kite_totp_secret.get_secret_value() == "totp-secret"
    assert settings.headless is False
    assert settings.session_store is SessionStoreBackend.JSON
    assert settings.log_level is LogLevel.DEBUG


def test_blank_env_values_are_treated_as_unset(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("KITE_API_KEY=\nKITE_API_SECRET=   \n", encoding="utf-8")

    settings = Settings.from_env_file(env_file)

    assert settings.kite_api_key is None
    assert settings.kite_api_secret is None


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        Settings.model_validate({"LOG_LEVEL": "verbose"})


def test_invalid_session_store_is_rejected() -> None:
    with pytest.raises(ValidationError, match="SESSION_STORE"):
        Settings.model_validate({"SESSION_STORE": "redis"})


def test_singleton_settings_loader_returns_cached_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.log_level is LogLevel.ERROR

    clear_settings_cache()


def test_required_api_credentials_validation() -> None:
    settings = Settings()

    with pytest.raises(ConfigurationError, match="KITE_API_KEY, KITE_API_SECRET"):
        settings.require_api_credentials()


def test_required_automated_login_credentials_validation() -> None:
    settings = Settings()

    with pytest.raises(ConfigurationError, match="KITE_USER_ID, KITE_PASSWORD, KITE_TOTP_SECRET"):
        settings.require_automated_login_credentials()
