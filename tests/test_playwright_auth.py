"""Tests for Playwright authentication automation."""

from collections.abc import Callable
from typing import Any

import pytest

from kite_auto.auth.playwright_auth import PlaywrightAuthStrategy, extract_request_token
from kite_auto.exceptions import (
    AuthenticationError,
    ConfigurationError,
    RequestTokenNotFoundError,
)

TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # noqa: S105 - standard RFC 4226 test seed


async def _noop_sleeper(_seconds: float) -> None:
    """Sleeper stub so tests never wait on the freshness gate or retry backoff."""
    return None


class FakeLocatorTarget:
    """Models a Playwright ``locator.first`` resolved to a single matched selector.

    ``selector`` is the first candidate from a compound selector that is currently
    available on the page, or ``None`` when nothing matched (so ``wait_for`` times
    out exactly as the real locator would).
    """

    def __init__(self, page: "FakePage", selector: str | None) -> None:
        self._page = page
        self._selector = selector

    async def wait_for(self, **kwargs: object) -> None:
        if self._selector is None:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError

            raise PlaywrightTimeoutError("no matching selector")

    async def fill(self, value: str, **kwargs: object) -> None:
        if value == "":
            return
        assert self._selector is not None
        timeout = kwargs["timeout"]
        self._page.actions.append(("fill", self._selector, value, str(timeout)))

    async def press_sequentially(self, value: str, **kwargs: object) -> None:
        assert self._selector is not None
        timeout = kwargs["timeout"]
        self._page.actions.append(("type", self._selector, value, str(timeout)))

    async def click(self, **kwargs: object) -> None:
        assert self._selector is not None
        timeout = kwargs["timeout"]
        self._page.actions.append(("click", self._selector, str(timeout)))
        if self._page.stage == "credentials":
            self._page.stage = "totp"
            return
        if self._page.stage == "totp":
            self._page.url = self._page.final_url


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> FakeLocatorTarget:
        available = self._page.available_selectors()
        resolved = next(
            (part.strip() for part in self._selector.split(",") if part.strip() in available),
            None,
        )
        return FakeLocatorTarget(self._page, resolved)


class FakeKeyboard:
    def __init__(self, page: "FakePage") -> None:
        self._page = page

    async def press(self, key: str) -> None:
        self._page.actions.append(("press", key))
        self._page.url = self._page.final_url


class FakePage:
    def __init__(self, final_url: str) -> None:
        self.final_url = final_url
        self.url = "about:blank"
        self.stage = "credentials"
        self.actions: list[tuple[str, ...]] = []
        self.handlers: list[tuple[str, Callable[..., object]]] = []
        self.keyboard = FakeKeyboard(self)

    async def goto(self, url: str, **kwargs: object) -> None:
        wait_until = str(kwargs["wait_until"])
        timeout = kwargs["timeout"]
        self.url = url
        self.actions.append(("goto", url, wait_until, str(timeout)))

    def on(self, event: str, handler: Callable[..., object]) -> None:
        # The strategy registers a request listener; the fake never emits
        # requests, so recording the handler is enough.
        self.handlers.append((event, handler))

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def wait_for_url(self, predicate: Callable[[str], bool], **kwargs: object) -> None:
        timeout = kwargs["timeout"]
        self.actions.append(("wait_for_url", str(timeout)))
        if not predicate(self.url):
            self.url = self.final_url

    def available_selectors(self) -> set[str]:
        if self.stage == "credentials":
            return {"input#userid", "input#password", "button[type='submit']"}
        return {"input#totp", "button[type='submit']"}


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.headless: bool | None = None

    async def launch(self, *, headless: bool, **_kwargs: object) -> FakeBrowser:
        self.headless = headless
        return self.browser


class FakePlaywright:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = FakeChromium(browser)


class FakePlaywrightContext:
    def __init__(self, page: FakePage) -> None:
        self.browser = FakeBrowser(page)
        self.playwright = FakePlaywright(self.browser)

    async def __aenter__(self) -> FakePlaywright:
        return self.playwright

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def test_extract_request_token_from_redirect_url() -> None:
    token = extract_request_token(
        "https://example.com/callback?status=success&request_token=request-token&action=login"
    )

    assert token == "request-token"  # noqa: S105


def test_extract_request_token_rejects_missing_token() -> None:
    with pytest.raises(RequestTokenNotFoundError):
        extract_request_token("https://example.com/callback?status=success")


def test_extract_request_token_rejects_error_redirect() -> None:
    with pytest.raises(AuthenticationError, match="Invalid credentials"):
        extract_request_token("https://example.com/callback?status=error&message=Invalid+credentials")


@pytest.mark.asyncio
async def test_playwright_login_returns_request_token() -> None:
    page = FakePage(
        "https://example.com/callback?status=success&request_token=request-token&action=login"
    )
    context = FakePlaywrightContext(page)
    strategy = PlaywrightAuthStrategy(
        api_key="api-key",
        user_id="user-id",
        password="password",  # noqa: S106
        totp_secret=TEST_TOTP_SECRET,
        headless=False,
        max_attempts=1,
        timeout_ms=123,
        verify_clock=False,
        sleeper=_noop_sleeper,
        playwright_factory=lambda: context,
    )

    token = await strategy.login()

    assert token == "request-token"  # noqa: S105
    assert context.playwright.chromium.headless is False
    assert context.browser.closed is True
    assert ("fill", "input#userid", "user-id", "123") in page.actions
    assert ("fill", "input#password", "password", "123") in page.actions
    assert any(action[0] == "type" and action[1] == "input#totp" for action in page.actions)
    assert page.actions[0] == (
        "goto",
        "https://kite.zerodha.com/connect/login?v=3&api_key=api-key",
        "domcontentloaded",
        "123",
    )


@pytest.mark.asyncio
async def test_playwright_login_requires_credentials() -> None:
    strategy = PlaywrightAuthStrategy(max_attempts=1, playwright_factory=lambda: object())

    with pytest.raises(ConfigurationError, match="KITE_API_KEY"):
        await strategy.login()


@pytest.mark.asyncio
async def test_playwright_login_retries_transient_token_failure() -> None:
    contexts = [
        FakePlaywrightContext(FakePage("https://example.com/callback?status=success")),
        FakePlaywrightContext(
            FakePage("https://example.com/callback?status=success&request_token=retry-token")
        ),
    ]

    def factory() -> Any:
        return contexts.pop(0)

    strategy = PlaywrightAuthStrategy(
        api_key="api-key",
        user_id="user-id",
        password="password",  # noqa: S106
        totp_secret=TEST_TOTP_SECRET,
        max_attempts=2,
        timeout_ms=123,
        verify_clock=False,
        sleeper=_noop_sleeper,
        playwright_factory=factory,
    )

    token = await strategy.login()

    assert token == "retry-token"  # noqa: S105
    assert contexts == []


class FakeClock:
    """Deterministic monotonic clock whose sleep() advances time, like the real one."""

    def __init__(self, start: float) -> None:
        self.t = start

    def time(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds


def _gate_strategy(clock: FakeClock, **overrides: Any) -> PlaywrightAuthStrategy:
    params: dict[str, Any] = {
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
    return PlaywrightAuthStrategy(**params)


@pytest.mark.asyncio
async def test_generate_fresh_totp_waits_when_window_nearly_expired() -> None:
    import pyotp

    # int(start) % 30 == 27 -> only 3s left in the window (< default 8s margin).
    start = 1_700_000_007.0
    clock = FakeClock(start)
    strategy = _gate_strategy(clock)

    code = await strategy._generate_fresh_totp()

    # Slept past the boundary (3 + 0.5) and generated in the *next* window.
    assert clock.t == pytest.approx(start + 3.5)
    assert code == pyotp.TOTP(TEST_TOTP_SECRET).at(int(clock.t))
    # Crucially, not the code for the near-expiry window we started in.
    assert code != pyotp.TOTP(TEST_TOTP_SECRET).at(int(start))


@pytest.mark.asyncio
async def test_generate_fresh_totp_does_not_wait_with_ample_window() -> None:
    import pyotp

    # int(start) % 30 == 20 -> 10s left, above the 8s margin: no wait.
    start = 1_700_000_000.0
    clock = FakeClock(start)
    strategy = _gate_strategy(clock)

    code = await strategy._generate_fresh_totp()

    assert clock.t == start
    assert code == pyotp.TOTP(TEST_TOTP_SECRET).at(int(start))


@pytest.mark.asyncio
async def test_generate_fresh_totp_never_replays_across_windows() -> None:
    import pyotp

    clock = FakeClock(1_700_000_000.0)
    strategy = _gate_strategy(clock)

    codes: list[str] = []
    for _ in range(3):
        expected = pyotp.TOTP(TEST_TOTP_SECRET).at(int(clock.t))
        actual = await strategy._generate_fresh_totp()
        assert actual == expected  # freshly computed for the current window
        codes.append(actual)
        clock.t += 30  # advance a full window before the next attempt

    assert len(set(codes)) == 3  # never resubmits a prior window's code


@pytest.mark.asyncio
async def test_strategy_rejects_negative_min_validity() -> None:
    with pytest.raises(ValueError, match="min_validity_seconds"):
        PlaywrightAuthStrategy(
            api_key="k",
            user_id="u",
            password="p",  # noqa: S106
            totp_secret=TEST_TOTP_SECRET,
            min_validity_seconds=-1,
            playwright_factory=lambda: object(),
        )


@pytest.mark.asyncio
async def test_login_aborts_on_clock_drift_when_hard_fail() -> None:
    from kite_auto.exceptions import ClockDriftError

    async def trusted() -> float:
        return 100.0

    strategy = PlaywrightAuthStrategy(
        api_key="k",
        user_id="u",
        password="p",  # noqa: S106
        totp_secret=TEST_TOTP_SECRET,
        verify_clock=True,
        max_clock_drift_seconds=5.0,
        clock_drift_hard_fail=True,
        time_source=lambda: 130.0,  # 30s drift vs trusted 100s
        trusted_time_source=trusted,
        sleeper=_noop_sleeper,
        playwright_factory=lambda: object(),  # never reached
    )

    with pytest.raises(ClockDriftError):
        await strategy.login()


@pytest.mark.asyncio
async def test_capture_diagnostics_is_best_effort_on_supported_page() -> None:
    class DiagPage:
        url = "https://example.com"

        async def screenshot(self, path: str) -> None:
            return None

        async def evaluate(self, script: str) -> list[object]:
            return []

    strategy = _gate_strategy(FakeClock(0.0))
    # Must complete without raising.
    await strategy._capture_diagnostics(DiagPage(), "unit")


@pytest.mark.asyncio
async def test_capture_diagnostics_swallows_errors() -> None:
    strategy = _gate_strategy(FakeClock(0.0))
    # A page missing screenshot/evaluate must be handled silently.
    await strategy._capture_diagnostics(object(), "unit")
