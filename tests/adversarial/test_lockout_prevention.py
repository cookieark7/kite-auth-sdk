"""Category 3 — Account safety / lockout prevention (kite_sdk_test_plan.md).

Wired to the real PlaywrightAuthStrategy. This category defends the REAL Zerodha
account: Kite locks accounts after a handful of wrong TOTPs, so the SDK must stop
ITSELF well below that threshold regardless of configuration or caller behaviour.

The headless-fingerprint check needs a real browser and lives in
test_browser_surface.py.
"""

from __future__ import annotations

import pytest
from _helpers import TEST_TOTP_SECRET, FakeClock, ScriptedPage, scripted_factory

from kite_auto.auth.playwright_auth import PlaywrightAuthStrategy
from kite_auto.exceptions import AuthenticationError

# Kite locks accounts around 5 wrong TOTPs; a safe breaker must trip below that.
SAFE_SUBMISSION_CEILING = 3


def _strategy(submitted: list[str], pages: list[ScriptedPage], **overrides: object):
    clock = FakeClock(1_700_000_000.0)
    params: dict[str, object] = {
        "api_key": "k",
        "user_id": "u",
        "password": "p",
        "totp_secret": TEST_TOTP_SECRET,
        "verify_clock": False,
        "timeout_ms": 123,
        "time_source": clock.time,
        "sleeper": clock.sleep,
        "playwright_factory": scripted_factory(pages),
    }
    params.update(overrides)
    return PlaywrightAuthStrategy(**params)  # type: ignore[arg-type]


async def test_circuit_breaker_stops_after_three_consecutive_wrong_totp() -> None:
    """A run of wrong-TOTP rejections must open a breaker BELOW Kite's lockout
    threshold, independent of how high max_attempts is set."""
    submitted: list[str] = []
    pages = [
        ScriptedPage(outcome="error:the+password+or+totp+is+wrong", submitted_totps=submitted)
        for _ in range(10)
    ]
    strategy = _strategy(submitted, pages, max_attempts=10)

    with pytest.raises(AuthenticationError):
        await strategy.login()

    assert len(submitted) <= SAFE_SUBMISSION_CEILING, (
        f"submitted {len(submitted)} wrong TOTPs; no breaker capped it below "
        f"Kite's lockout threshold"
    )


async def test_hard_cap_on_attempts_per_window_independent_of_caller() -> None:
    """A pathological caller that loops login() must still be capped by the SDK.

    Intent: the defense must live inside the SDK, not in the caller. Count real
    submissions across many caller-driven attempts; they must respect an
    internal hard cap.
    """
    submitted: list[str] = []
    pages = [
        ScriptedPage(outcome="error:the+password+or+totp+is+wrong", submitted_totps=submitted)
        for _ in range(6)
    ]
    strategy = _strategy(submitted, pages, max_attempts=1)

    for _ in range(6):  # buggy caller hammering login()
        try:
            await strategy.login()
        except AuthenticationError:
            continue

    assert len(submitted) <= SAFE_SUBMISSION_CEILING, (
        f"caller drove {len(submitted)} wrong-TOTP submissions; the SDK enforces "
        "no cross-call cap"
    )
