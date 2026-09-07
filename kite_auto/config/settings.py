"""Centralized SDK configuration."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kite_auto.exceptions import ConfigurationError


class LogLevel(StrEnum):
    """Supported application log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SessionStoreBackend(StrEnum):
    """Supported session persistence backends."""

    JSON = "json"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    kite_api_key: str | None = Field(default=None, validation_alias="KITE_API_KEY")
    kite_api_secret: SecretStr | None = Field(default=None, validation_alias="KITE_API_SECRET")
    kite_user_id: str | None = Field(default=None, validation_alias="KITE_USER_ID")
    kite_password: SecretStr | None = Field(default=None, validation_alias="KITE_PASSWORD")
    kite_totp_secret: SecretStr | None = Field(default=None, validation_alias="KITE_TOTP_SECRET")
    headless: bool = Field(default=True, validation_alias="HEADLESS")
    max_login_attempts: int = Field(default=3, validation_alias="MAX_LOGIN_ATTEMPTS")
    totp_min_validity_seconds: int = Field(
        default=8, validation_alias="TOTP_MIN_VALIDITY_SECONDS"
    )
    max_clock_drift_seconds: float = Field(
        default=5.0, validation_alias="MAX_CLOCK_DRIFT_SECONDS"
    )
    clock_drift_hard_fail: bool = Field(default=False, validation_alias="CLOCK_DRIFT_HARD_FAIL")
    session_store: SessionStoreBackend = Field(
        default=SessionStoreBackend.JSON,
        validation_alias="SESSION_STORE",
    )
    log_level: LogLevel = Field(default=LogLevel.INFO, validation_alias="LOG_LEVEL")

    @field_validator(
        "kite_api_key",
        "kite_api_secret",
        "kite_user_id",
        "kite_password",
        "kite_totp_secret",
        mode="before",
    )
    @classmethod
    def normalize_blank_secret(cls, value: object) -> object | None:
        """Treat blank .env values as unset secrets."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> LogLevel:
        """Normalize and validate log level names."""
        normalized = str(value).strip().upper()
        try:
            return LogLevel(normalized)
        except ValueError as exc:
            allowed_values = ", ".join(level.value for level in LogLevel)
            raise ValueError(f"LOG_LEVEL must be one of: {allowed_values}") from exc

    @field_validator("session_store", mode="before")
    @classmethod
    def normalize_session_store(cls, value: object) -> SessionStoreBackend:
        """Normalize and validate session storage backend names."""
        normalized = str(value).strip().lower()
        try:
            return SessionStoreBackend(normalized)
        except ValueError as exc:
            allowed_values = ", ".join(backend.value for backend in SessionStoreBackend)
            raise ValueError(f"SESSION_STORE must be one of: {allowed_values}") from exc

    @classmethod
    def from_env_file(cls, env_file: str | Path) -> Self:
        """Load settings from a specific .env file path."""
        return cls(_env_file=env_file)  # type: ignore[call-arg]

    def require_api_credentials(self) -> None:
        """Validate credentials required for Kite API token exchange."""
        missing = [
            name
            for name, value in (
                ("KITE_API_KEY", self.kite_api_key),
                ("KITE_API_SECRET", self.kite_api_secret),
            )
            if value is None
        ]
        if missing:
            raise ConfigurationError(f"Missing required Kite API settings: {', '.join(missing)}")

    def require_automated_login_credentials(self) -> None:
        """Validate credentials required for Playwright login automation."""
        missing = [
            name
            for name, value in (
                ("KITE_USER_ID", self.kite_user_id),
                ("KITE_PASSWORD", self.kite_password),
                ("KITE_TOTP_SECRET", self.kite_totp_secret),
            )
            if value is None
        ]
        if missing:
            raise ConfigurationError(
                f"Missing required automated login settings: {', '.join(missing)}"
            )


def clear_settings_cache() -> None:
    """Clear cached settings, primarily for tests and long-running reloads."""
    get_settings.cache_clear()


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached SDK settings instance."""
    return Settings()
