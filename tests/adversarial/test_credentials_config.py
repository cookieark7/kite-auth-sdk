"""Category 4 — Credentials / config (kite_sdk_test_plan.md).

Wired to the real Settings and PlaywrightAuthStrategy. All secrets here are
dummy values; the repo's real .env is isolated by tests/conftest.py.

The password-form-fill fidelity case (special characters) requires a real
browser DOM and lives in test_browser_surface.py.
"""

from __future__ import annotations

import pytest
from _helpers import TEST_TOTP_SECRET

from kite_auto.auth.playwright_auth import PlaywrightAuthStrategy
from kite_auto.exceptions import ConfigurationError


@pytest.mark.parametrize("bad_secret", ["NOT!BASE32", "11111111", "abcdefg="])
def test_malformed_base32_totp_secret_fails_at_config_load(bad_secret: str) -> None:
    """Constructing the strategy with a malformed base32 secret must fail fast
    with a specific, named error — never defer to login and never silently
    produce a wrong code."""
    with pytest.raises((ConfigurationError, ValueError)) as exc:
        PlaywrightAuthStrategy(
            api_key="k",
            user_id="u",
            password="p",  # noqa: S106
            totp_secret=bad_secret,
            playwright_factory=lambda: object(),
        )
    assert "totp" in str(exc.value).lower()


async def test_missing_env_var_fails_fast_with_variable_name() -> None:
    """A required credential that is unset must fail with the variable NAMED.

    Intent: not a bare KeyError/NoneType error three layers down — the message
    must contain the exact env var name so an operator knows what to set.
    """
    # api_key omitted; env is isolated so Settings resolves it to None.
    strategy = PlaywrightAuthStrategy(
        user_id="u",
        password="p",  # noqa: S106
        totp_secret=TEST_TOTP_SECRET,
        max_attempts=1,
        verify_clock=False,
        playwright_factory=lambda: object(),
    )
    with pytest.raises(ConfigurationError, match="KITE_API_KEY"):
        await strategy.login()


async def test_blank_env_var_is_treated_as_missing_and_named() -> None:
    """A set-but-empty credential must be treated as missing, not accepted."""
    strategy = PlaywrightAuthStrategy(
        api_key="k",
        user_id="",  # blank -> normalized to unset
        password="p",  # noqa: S106
        totp_secret=TEST_TOTP_SECRET,
        max_attempts=1,
        verify_clock=False,
        playwright_factory=lambda: object(),
    )
    with pytest.raises(ConfigurationError, match="KITE_USER_ID"):
        await strategy.login()
