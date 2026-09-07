"""Shared test fixtures.

Isolate the test suite from any developer ``.env`` file and ambient ``KITE_*``
environment variables so credential-presence assertions are deterministic and no
test can accidentally reach a live broker session.
"""

from collections.abc import Iterator

import pytest

from kite_auto.config.settings import Settings, clear_settings_cache

_ISOLATED_ENV_VARS = (
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_USER_ID",
    "KITE_PASSWORD",
    "KITE_TOTP_SECRET",
    "HEADLESS",
    "SESSION_STORE",
    "LOG_LEVEL",
    "MAX_LOGIN_ATTEMPTS",
    "TOTP_MIN_VALIDITY_SECONDS",
    "MAX_CLOCK_DRIFT_SECONDS",
    "CLOCK_DRIFT_HARD_FAIL",
)


@pytest.fixture(autouse=True)
def _isolate_kite_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in _ISOLATED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Stop pydantic-settings from reading a real .env in the working directory.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    clear_settings_cache()
    yield
    clear_settings_cache()
