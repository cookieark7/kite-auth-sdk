"""Category 1 — TOTP timing (kite_sdk_test_plan.md).

Wired to the real PlaywrightAuthStrategy. Assertion intents are fixed; only the
scenario setup is allowed to change. A failing test here is a real finding.
"""

from __future__ import annotations

import pyotp
import pytest
from _helpers import (
    TEST_TOTP_SECRET,
    FakeClock,
    ScriptedPage,
    noop_sleeper,
    scripted_factory,
)

from kite_auto.auth.playwright_auth import PlaywrightAuthStrategy
from kite_auto.exceptions import ClockDriftError


def _strategy(clock: FakeClock, **overrides: object) -> PlaywrightAuthStrategy:
    params: dict[str, object] = {
        "api_key": "k",
        "user_id": "u",
        "password": "p",
        "totp_secret": TEST_TOTP_SECRET,
        "verify_clock": False,
        "time_source": clock.time,
        "sleeper": clock.sleep,
        "playwright_factory": lambda: object(),
    }
    params.update(overrides)
    return PlaywrightAuthStrategy(**params)  # type: ignore[arg-type]


async def test_totp_generated_one_second_before_window_boundary() -> None:
    """A code with 1s left in its window must not be submitted stale.

    Intent: the freshness gate must move generation into the next window so the
    submitted code is NOT the near-expiry window's code.
    """
    # int(start) % 30 == 29 -> 1s remaining, below the 8s default margin.
    start = 1_700_000_009.0
    clock = FakeClock(start)
    strategy = _strategy(clock)

    code = await strategy._generate_fresh_totp()

    assert code != pyotp.TOTP(TEST_TOTP_SECRET).at(int(start))
    assert code == pyotp.TOTP(TEST_TOTP_SECRET).at(int(clock.t))
    assert int(clock.t) % 30 < 29  # generated with fresh headroom, not at the cliff


async def test_totp_generated_one_second_after_window_boundary() -> None:
    """A code 1s into a fresh window is submitted promptly, no artificial wait."""
    # int(start) % 30 == 1 -> 29s remaining, well above the margin.
    start = 1_700_000_011.0
    clock = FakeClock(start)
    strategy = _strategy(clock)

    code = await strategy._generate_fresh_totp()

    assert clock.t == start  # no pessimistic sleep-to-next-window
    assert code == pyotp.TOTP(TEST_TOTP_SECRET).at(int(start))


@pytest.mark.parametrize("drift_seconds", [-90, -35, -5, 5, 35, 90])
async def test_local_clock_drift_detected_or_compensated(drift_seconds: int) -> None:
    """Drift beyond tolerance must abort BEFORE any doomed submission.

    Intent: simulated purely via injected clocks (no time.sleep). With hard-fail
    on and a 5s tolerance, drift larger than tolerance must raise a specific
    ClockDriftError; drift within tolerance must pass silently.
    """
    tolerance = 5.0
    trusted_epoch = 1_700_000_000.0

    async def trusted() -> float:
        return trusted_epoch

    strategy = _strategy(
        FakeClock(0.0),
        verify_clock=True,
        max_clock_drift_seconds=tolerance,
        clock_drift_hard_fail=True,
        time_source=lambda: trusted_epoch + drift_seconds,
        trusted_time_source=trusted,
        sleeper=noop_sleeper,
    )

    if abs(drift_seconds) > tolerance:
        with pytest.raises(ClockDriftError):
            await strategy._verify_host_clock()
    else:
        await strategy._verify_host_clock()  # must not raise


async def test_network_delay_crosses_window_boundary_between_generation_and_submit() -> None:
    """A submit slow enough to cross a window boundary must not replay a stale code.

    Intent: first attempt's code is rejected (status=error) after the submit
    round-trip pushes past the window; the retry must submit a FRESH code, never
    the same stale one, and the failure must be treated as retryable (not a
    terminal credential error).
    """
    start = 1_700_000_000.0
    clock = FakeClock(start)
    submitted: list[str] = []
    pages = [
        # Attempt 1: submit takes 40s, crossing the window -> Kite rejects.
        ScriptedPage(
            outcome="error:TOTP+expired",
            submitted_totps=submitted,
            clock=clock,
            submit_delay=40.0,
        ),
        # Attempt 2: succeeds.
        ScriptedPage(outcome="success:fresh-token", submitted_totps=submitted),
    ]
    strategy = _strategy(
        clock,
        max_attempts=2,
        timeout_ms=123,
        playwright_factory=scripted_factory(pages),
    )

    token = await strategy.login()

    assert token == "fresh-token"  # noqa: S105
    assert len(submitted) == 2, "each attempt must submit its own code"
    assert submitted[0] != submitted[1], "must not replay the stale, expired code"


async def test_two_concurrent_logins_same_account_same_window() -> None:
    """Concurrent logins in one window must submit the TOTP at most once.

    Intent: a bug or race in calling code must not translate into two 2FA
    submissions racing within a single window. Acceptable: serialized, or the
    second fails fast. Unacceptable: two concurrent TOTP submissions.
    """
    import asyncio

    clock = FakeClock(1_700_000_000.0)
    submitted: list[str] = []
    pages = [
        ScriptedPage(outcome="success:token-a", submitted_totps=submitted),
        ScriptedPage(outcome="success:token-b", submitted_totps=submitted),
    ]
    strategy = _strategy(
        clock,
        max_attempts=1,
        timeout_ms=123,
        playwright_factory=scripted_factory(pages),
    )

    await asyncio.gather(strategy.login(), strategy.login())

    assert len(submitted) <= 1, (
        f"two concurrent logins submitted {len(submitted)} TOTP codes in one "
        "window; the SDK provides no concurrency guard"
    )
