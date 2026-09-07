"""Shared helpers for the adversarial suite.

Deliberately NOT copied from the implementation's own test fakes. These model
external reality (a caller, a clock, a session store, a real browser DOM) so the
tests exercise the SDK rather than confirm its internal expectations.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from kite_auto.auth.base import AuthStrategy
from kite_auto.auth.session_store import KiteSession, SessionStore

# RFC 6238 / pyotp canonical test seed. Never a real secret.
TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # noqa: S105


async def noop_sleeper(_seconds: float) -> None:
    """Sleeper stub: never actually waits (freshness gate / retry backoff)."""
    return None


class FakeClock:
    """Injectable clock whose ``sleep`` advances virtual time (no real waiting)."""

    def __init__(self, start: float) -> None:
        self.t = start

    def time(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds


class CountingAuthStrategy(AuthStrategy):
    """Auth strategy that counts how many real login attempts a caller triggers.

    Models the boundary the SDK must protect: every ``login()`` here stands for
    one real 2FA interaction with Kite. ``responses`` may hold request-token
    strings or exceptions to raise, consumed in order.
    """

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def login(self) -> str:
        self.calls += 1
        if not self._responses:
            raise AssertionError("CountingAuthStrategy exhausted its scripted responses")
        result = self._responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return str(result)


class MemorySessionStore(SessionStore):
    """In-memory session store that records save/load/clear traffic."""

    def __init__(self, initial: KiteSession | None = None) -> None:
        self._session = initial
        self.loads = 0
        self.saves = 0
        self.clears = 0

    async def save(self, session: KiteSession) -> None:
        self.saves += 1
        self._session = session

    async def load(self) -> KiteSession | None:
        self.loads += 1
        return self._session

    async def clear(self) -> None:
        self.clears += 1
        self._session = None


def valid_session(*, ttl: timedelta = timedelta(hours=12)) -> KiteSession:
    """A session comfortably inside its validity window."""
    now = datetime.now(UTC)
    return KiteSession(
        access_token="existing-access-token",  # noqa: S106
        created_at=now,
        expires_at=now + ttl,
    )


CounterFn = Callable[[], Awaitable[None]]


# ---------------------------------------------------------------------------
# Scripted browser harness
#
# Models a real Kite login *browser* (two-stage form, redirect on submit) whose
# per-attempt outcome we control. It records the TOTP actually typed each attempt
# so tests can prove non-replay, count submissions, and inject a mid-flow crash.
# This models the browser's externally observable behaviour, not the SDK's
# internal expectations.
# ---------------------------------------------------------------------------


class _CrashError(RuntimeError):
    """Stand-in for a process/browser crash mid-login."""


class _ScriptedLocatorTarget:
    def __init__(self, page: ScriptedPage, selector: str | None) -> None:
        self._page = page
        self._selector = selector

    async def wait_for(self, **_kwargs: object) -> None:
        if self._selector is None:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError

            raise PlaywrightTimeoutError("no matching selector")

    async def fill(self, value: str, **_kwargs: object) -> None:
        if value == "":
            return
        self._page.actions.append(("fill", self._selector, value))

    async def press_sequentially(self, value: str, **_kwargs: object) -> None:
        if self._page.crash_at_totp:
            raise _CrashError("browser crashed before TOTP submit")
        self._page.submitted_totps.append(value)
        self._page.actions.append(("type", self._selector, value))

    async def click(self, **_kwargs: object) -> None:
        self._page.actions.append(("click", self._selector))
        if self._page.stage == "credentials":
            self._page.stage = "totp"
        elif self._page.stage == "totp":
            self._page.resolve()


class _ScriptedLocator:
    def __init__(self, page: ScriptedPage, selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> _ScriptedLocatorTarget:
        available = self._page.available_selectors()
        resolved = next(
            (p.strip() for p in self._selector.split(",") if p.strip() in available),
            None,
        )
        return _ScriptedLocatorTarget(self._page, resolved)


class _ScriptedKeyboard:
    def __init__(self, page: ScriptedPage) -> None:
        self._page = page

    async def press(self, key: str) -> None:
        self._page.actions.append(("press", key))
        self._page.resolve()


class ScriptedPage:
    """One login attempt against a scripted two-stage Kite form."""

    def __init__(
        self,
        *,
        outcome: str,
        submitted_totps: list[str],
        clock: FakeClock | None = None,
        submit_delay: float = 0.0,
        crash_at_totp: bool = False,
        broken_totp: bool = False,
    ) -> None:
        # outcome: "success:<token>" or "error:<message>"
        self._outcome = outcome
        self.submitted_totps = submitted_totps
        self._clock = clock
        self._submit_delay = submit_delay
        self.crash_at_totp = crash_at_totp
        self._broken_totp = broken_totp
        self.stage = "credentials"
        self.url = "about:blank"
        self.actions: list[tuple[object, ...]] = []

    keyboard: _ScriptedKeyboard

    def __post_init__(self) -> None:  # pragma: no cover - not a dataclass
        ...

    async def goto(self, url: str, **_kwargs: object) -> None:
        self.url = url
        self.actions.append(("goto", url))

    def on(self, _event: str, _handler: object) -> None:
        return None

    def locator(self, selector: str) -> _ScriptedLocator:
        return _ScriptedLocator(self, selector)

    async def wait_for_url(self, predicate: object, **_kwargs: object) -> None:
        self.actions.append(("wait_for_url",))

    async def screenshot(self, **_kwargs: object) -> None:
        return None

    async def evaluate(self, _script: str) -> list[object]:
        return []

    def available_selectors(self) -> set[str]:
        if self.stage == "credentials":
            return {"input#userid", "input#password", "button[type='submit']"}
        if self._broken_totp:
            # Simulates an unexpected screen (CAPTCHA / new-device) with none of
            # the expected TOTP-step elements present.
            return set()
        return {"input#totp", "button[type='submit']"}

    def resolve(self) -> None:
        # A slow submit round-trip can push the clock across a TOTP window.
        if self._clock is not None and self._submit_delay:
            self._clock.t += self._submit_delay
        kind, _, value = self._outcome.partition(":")
        if kind == "success":
            self.url = f"https://example.com/callback?status=success&request_token={value}"
        else:
            self.url = f"https://example.com/callback?status=error&message={value}"


class ScriptedContext:
    """Async-context playwright stand-in serving a single ScriptedPage."""

    def __init__(self, page: ScriptedPage) -> None:
        page.keyboard = _ScriptedKeyboard(page)
        self._page = page
        self.closed = False

        harness = self

        class _Browser:
            async def new_page(self) -> ScriptedPage:
                return harness._page

            async def close(self) -> None:
                harness.closed = True

        class _Chromium:
            async def launch(self, **_kwargs: object) -> _Browser:
                return _Browser()

        class _PW:
            chromium = _Chromium()

        self._pw = _PW()

    async def __aenter__(self) -> object:
        return self._pw

    async def __aexit__(self, *_exc: object) -> None:
        return None


def scripted_factory(pages: list[ScriptedPage]) -> Callable[[], ScriptedContext]:
    """Return a playwright_factory that serves one ScriptedPage per attempt."""

    def factory() -> ScriptedContext:
        return ScriptedContext(pages.pop(0))

    return factory
