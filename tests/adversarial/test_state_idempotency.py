"""Category 5 — State / idempotency (kite_sdk_test_plan.md).

Wired to the real TokenManager and PlaywrightAuthStrategy.
"""

from __future__ import annotations

from _helpers import (
    TEST_TOTP_SECRET,
    CountingAuthStrategy,
    FakeClock,
    MemorySessionStore,
    ScriptedPage,
    _CrashError,  # simulated crash exception
    scripted_factory,
    valid_session,
)

from kite_auto.auth.token_manager import TokenManager


class _RecordingExchange:
    """Token-exchange stand-in that must never be called on the reuse path."""

    def __init__(self) -> None:
        self.calls = 0

    async def exchange_request_token(self, request_token: str):
        self.calls += 1
        raise AssertionError("exchange must not run when a valid session exists")


async def test_login_with_valid_existing_session_does_not_relogin() -> None:
    """A valid stored session must short-circuit login entirely.

    Intent: zero browser logins and zero token exchanges; the stored token is
    returned as-is. Any re-login must be explicit (force_refresh), never
    implicit.
    """
    session = valid_session()
    store = MemorySessionStore(session)
    auth = CountingAuthStrategy(responses=["should-not-be-used"])
    exchange = _RecordingExchange()
    manager = TokenManager(
        exchange,
        auth_strategy=auth,
        session_store=store,
    )

    token = await manager.get_access_token()

    assert token == session.access_token
    assert auth.calls == 0, "reused a valid session but still triggered a browser login"
    assert exchange.calls == 0


async def test_crash_between_username_submit_and_totp_submit_then_retry() -> None:
    """A crash before TOTP submit must leave no double-submission; a fresh retry
    must succeed cleanly.

    Intent: attempt 1 crashes after credentials but before the TOTP is submitted;
    a subsequent login() call starts clean and submits exactly one TOTP. No code
    is submitted twice, and no half-written session survives from the crash.
    """
    from kite_auto.auth.playwright_auth import PlaywrightAuthStrategy

    clock = FakeClock(1_700_000_000.0)
    submitted: list[str] = []
    crash_page = ScriptedPage(
        outcome="success:unused", submitted_totps=submitted, crash_at_totp=True
    )
    good_page = ScriptedPage(outcome="success:clean-token", submitted_totps=submitted)

    strategy = PlaywrightAuthStrategy(
        api_key="k",
        user_id="u",
        password="p",  # noqa: S106
        totp_secret=TEST_TOTP_SECRET,
        verify_clock=False,
        max_attempts=1,
        timeout_ms=123,
        time_source=clock.time,
        sleeper=clock.sleep,
        playwright_factory=scripted_factory([crash_page, good_page]),
    )

    # Attempt 1: the crash propagates (it is not an AuthenticationError).
    try:
        await strategy.login()
    except _CrashError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected the simulated crash to surface")

    assert submitted == [], "a TOTP was submitted despite crashing before submit"

    # Attempt 2 (caller retries): clean run, exactly one TOTP submitted.
    token = await strategy.login()
    assert token == "clean-token"  # noqa: S105
    assert submitted == submitted[:1], "retry double-submitted the TOTP"
    assert len(submitted) == 1
