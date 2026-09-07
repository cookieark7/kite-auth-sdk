"""Playwright-based Kite Connect request-token authentication."""

import asyncio
import base64
import binascii
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pyotp
from loguru import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from kite_auto.auth.base import AuthStrategy
from kite_auto.config import Settings, get_settings
from kite_auto.exceptions import (
    AuthenticationError,
    CircuitBreakerOpenError,
    ConfigurationError,
    LoginPageStructureError,
    RequestTokenNotFoundError,
    TotpLockoutError,
)
from kite_auto.utils.clock import TrustedTimeSource, assert_clock_sync, fetch_trusted_epoch
from kite_auto.utils.retry import CircuitBreaker

type Sleeper = Callable[[float], Awaitable[None]]

DEFAULT_KITE_LOGIN_URL = "https://kite.zerodha.com/connect/login"

# RFC 4226 recommends TOTP secrets of at least 128 bits and permits 80 bits as
# the floor. Anything shorter is almost certainly a mistyped/truncated secret,
# which would silently generate wrong codes and burn account-lockout budget.
MIN_TOTP_SECRET_BYTES = 10

# Chromium launch flags + init script that remove the most obvious automation
# fingerprints (navigator.webdriver, the AutomationControlled blink feature) so a
# real user's session and this one are not trivially distinguishable.
DEFAULT_LAUNCH_ARGS = ("--disable-blink-features=AutomationControlled",)
_WEBDRIVER_MASK_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)


def validate_totp_secret(secret: str) -> None:
    """Validate that ``secret`` is usable base32 TOTP material.

    Raises :class:`ConfigurationError` for non-base32 input or a secret too short
    to be a real TOTP seed. This runs at config-load time so a malformed secret
    fails immediately instead of silently producing a wrong code at login.
    """
    cleaned = secret.strip().replace(" ", "").upper()
    padding = "=" * ((8 - len(cleaned) % 8) % 8)
    try:
        decoded = base64.b32decode(cleaned + padding, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigurationError(
            "KITE_TOTP_SECRET is not valid base32 TOTP secret material."
        ) from exc
    if len(decoded) < MIN_TOTP_SECRET_BYTES:
        raise ConfigurationError(
            f"KITE_TOTP_SECRET is too short to be a valid TOTP secret "
            f"({len(decoded) * 8} bits; need at least {MIN_TOTP_SECRET_BYTES * 8})."
        )


@dataclass(frozen=True, slots=True)
class PlaywrightLoginSelectors:
    """Selector candidates for Kite's browser login form.

    Zerodha can adjust form markup independently of the API contract, so the
    automation intentionally tries a small set of semantic and historical selectors.
    """

    user_id: Sequence[str] = field(
        default_factory=lambda: (
            "input#userid",
            "input[name='userid']",
            "input[name='user_id']",
            "input[autocomplete='username']",
            "input[type='text']",
        )
    )
    password: Sequence[str] = field(
        default_factory=lambda: (
            "input#password",
            "input[name='password']",
            "input[autocomplete='current-password']",
            "input[type='password']",
        )
    )
    login_button: Sequence[str] = field(
        default_factory=lambda: (
            "button[type='submit']",
            "button:has-text('Login')",
            "button:has-text('Continue')",
        )
    )
    totp: Sequence[str] = field(
        default_factory=lambda: (
            "input#totp",
            "input[name='totp']",
            "input[name='otp']",
            "input[autocomplete='one-time-code']",
            "input[type='number']",
            "input[type='tel']",
            # Legacy fallback: older Kite markup reused the userid id for the OTP step.
            "input#userid",
            "input[type='text']",
        )
    )
    totp_submit_button: Sequence[str] = field(
        default_factory=lambda: (
            "button[type='submit']",
            "button:has-text('Continue')",
            "button:has-text('Submit')",
        )
    )


type PlaywrightFactory = Callable[[], Any]


class PlaywrightAuthStrategy(AuthStrategy):
    """Automated browser strategy that returns a Kite Connect request token."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str | None = None,
        user_id: str | None = None,
        password: str | None = None,
        totp_secret: str | None = None,
        headless: bool | None = None,
        login_url: str = DEFAULT_KITE_LOGIN_URL,
        max_attempts: int | None = None,
        max_totp_failures: int = 3,
        lockout_cooldown_seconds: float = 300.0,
        timeout_ms: int = 30_000,
        min_validity_seconds: int | None = None,
        max_clock_drift_seconds: float | None = None,
        clock_drift_hard_fail: bool | None = None,
        verify_clock: bool = True,
        time_source: Callable[[], float] = time.time,
        sleeper: Sleeper = asyncio.sleep,
        trusted_time_source: TrustedTimeSource = fetch_trusted_epoch,
        selectors: PlaywrightLoginSelectors | None = None,
        playwright_factory: PlaywrightFactory | None = None,
        launch_args: Sequence[str] = DEFAULT_LAUNCH_ARGS,
    ) -> None:
        self._settings = settings or get_settings()
        self._api_key = api_key or self._settings.kite_api_key
        self._user_id = user_id or self._settings.kite_user_id
        self._password = password or self._secret_value(self._settings.kite_password)
        self._totp_secret = totp_secret or self._secret_value(self._settings.kite_totp_secret)
        self._headless = self._settings.headless if headless is None else headless
        self._login_url = login_url
        self._max_attempts = (
            max_attempts if max_attempts is not None else self._settings.max_login_attempts
        )
        self._timeout_ms = timeout_ms
        self._min_validity_seconds = (
            min_validity_seconds
            if min_validity_seconds is not None
            else self._settings.totp_min_validity_seconds
        )
        self._max_clock_drift_seconds = (
            max_clock_drift_seconds
            if max_clock_drift_seconds is not None
            else self._settings.max_clock_drift_seconds
        )
        self._clock_drift_hard_fail = (
            clock_drift_hard_fail
            if clock_drift_hard_fail is not None
            else self._settings.clock_drift_hard_fail
        )
        self._verify_clock = verify_clock
        self._time_source = time_source
        self._sleeper = sleeper
        self._trusted_time_source = trusted_time_source
        self._selectors = selectors or PlaywrightLoginSelectors()
        self._playwright_factory = playwright_factory or async_playwright
        self._launch_args = tuple(launch_args)

        if self._max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if max_totp_failures < 1:
            raise ValueError("max_totp_failures must be at least 1")
        if self._timeout_ms < 1:
            raise ValueError("timeout_ms must be at least 1")
        if self._min_validity_seconds < 0:
            raise ValueError("min_validity_seconds must be non-negative")

        # Fail fast on a malformed TOTP secret instead of generating wrong codes.
        if self._totp_secret is not None:
            validate_totp_secret(self._totp_secret)

        # Account-safety cap: a breaker over the wrong-TOTP submissions that trips
        # below Kite's real lockout threshold and persists across login() calls,
        # so neither a high max_attempts nor a buggy caller can lock the account.
        self._totp_breaker = CircuitBreaker(
            failure_threshold=max_totp_failures,
            recovery_timeout=lockout_cooldown_seconds,
        )
        # Single-flight guard so concurrent login() calls share one browser login
        # rather than racing two TOTP submissions in the same window.
        self._inflight: asyncio.Future[str] | None = None

    async def login(self) -> str:
        """Login through Chromium and return only the `request_token`.

        Concurrent calls share a single in-flight login (single-flight) so two
        callers never race two TOTP submissions in the same window.
        """
        inflight = self._inflight
        if inflight is None:
            inflight = asyncio.ensure_future(self._run_login())
            self._inflight = inflight
            inflight.add_done_callback(self._clear_inflight)
        return await inflight

    def _clear_inflight(self, _future: object) -> None:
        self._inflight = None

    async def _run_login(self) -> str:
        self._validate_required_settings()

        if self._verify_clock:
            await self._verify_host_clock()

        async for attempt in AsyncRetrying(
            reraise=True,
            # Retry transient credential/token failures, but never a UI-structure
            # change or a tripped safety cap: those will not fix themselves and
            # retrying only wastes attempts against the lockout budget.
            retry=(
                retry_if_exception_type(AuthenticationError)
                & retry_if_not_exception_type((LoginPageStructureError, TotpLockoutError))
            ),
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            sleep=self._sleeper,
        ):
            with attempt:
                attempt_number = attempt.retry_state.attempt_number
                logger.info("Starting Kite Playwright login attempt {}", attempt_number)
                try:
                    return await self._totp_breaker.call(
                        self._login_once,
                        record_failure=self._counts_as_totp_failure,
                    )
                except CircuitBreakerOpenError as exc:
                    raise TotpLockoutError(
                        "TOTP safety cap reached; refusing further submissions to "
                        "avoid a Kite account lockout. Wait for the cooldown before "
                        "retrying."
                    ) from exc

        raise AuthenticationError("Kite Playwright login did not complete.")

    @staticmethod
    def _counts_as_totp_failure(exc: BaseException) -> bool:
        """Only rejected submissions count toward the lockout cap.

        A UI-structure error or the cap's own error did not submit a wrong TOTP,
        so they must not advance the breaker toward opening.
        """
        return isinstance(exc, AuthenticationError) and not isinstance(
            exc, LoginPageStructureError | TotpLockoutError
        )

    async def _verify_host_clock(self) -> None:
        """Warn (or fail) if the host clock drifts from a trusted time source."""
        await assert_clock_sync(
            max_drift_seconds=self._max_clock_drift_seconds,
            hard_fail=self._clock_drift_hard_fail,
            trusted_time_source=self._trusted_time_source,
            local_time_source=self._time_source,
        )

    async def _generate_fresh_totp(self) -> str:
        """Return a TOTP generated as late as possible in a window with headroom.

        If the current window has less than ``min_validity_seconds`` left, wait
        for the next window before generating, so the code stays valid through the
        (slow) browser submit and the server validates it in the same window. Each
        call produces a fresh code — never replay a previously submitted one.
        """
        totp = pyotp.TOTP(self._totp_secret_value)
        interval = totp.interval
        now = self._time_source()
        remaining = interval - int(now) % interval
        if remaining < self._min_validity_seconds:
            logger.debug(
                "TOTP window has {}s left (< {}s margin); waiting for next window",
                remaining,
                self._min_validity_seconds,
            )
            await self._sleeper(remaining + 0.5)
            now = self._time_source()
        return totp.at(int(now))

    async def _login_once(self) -> str:
        login_url = self._build_login_url()
        logger.debug("Opening Kite login URL")

        async with self._playwright_factory() as playwright:
            browser = await playwright.chromium.launch(
                headless=self._headless,
                args=list(self._launch_args),
            )
            page = await self._new_hardened_page(browser)

            captured_url: str | None = None

            def handle_request(req: Any) -> None:
                nonlocal captured_url
                if "request_token=" in req.url or "status=error" in req.url:
                    captured_url = req.url

            page.on("request", handle_request)

            try:
                await page.goto(login_url, wait_until="domcontentloaded", timeout=self._timeout_ms)
                await self._fill_first_available(page, self._selectors.user_id, self._user_id_value)
                await self._fill_first_available(
                    page,
                    self._selectors.password,
                    self._password_value,
                )
                await self._click_first_available(page, self._selectors.login_button)
                await self._capture_diagnostics(page, "1_after_credentials")

                # Generate the code as late as possible, in a window with enough
                # headroom to survive the submit round-trip (see _generate_fresh_totp).
                totp = await self._generate_fresh_totp()
                await self._type_first_available(page, self._selectors.totp, totp)
                await self._capture_diagnostics(page, "2_totp_filled")
                submitted = await self._click_first_available(
                    page,
                    self._selectors.totp_submit_button,
                    required=False,
                )
                if not submitted:
                    await page.keyboard.press("Enter")

                from playwright.async_api import Error as PlaywrightError

                try:
                    await page.wait_for_url(
                        lambda url: "request_token=" in str(url) or "status=error" in str(url),
                        timeout=self._timeout_ms,
                    )
                except (PlaywrightTimeoutError, PlaywrightError):
                    await asyncio.sleep(0.5)

                await self._capture_diagnostics(page, "3_after_2fa")
                final_url = captured_url if captured_url else str(page.url)
                request_token = extract_request_token(final_url)

                logger.info("Kite request token extracted from login redirect")
                return request_token
            finally:
                await browser.close()

    async def _new_hardened_page(self, browser: Any) -> Any:
        """Open a page in a context whose most obvious automation tells are masked.

        Falls back to ``browser.new_page()`` for lightweight stand-ins that do not
        implement the browser-context API.
        """
        if not hasattr(browser, "new_context"):
            return await browser.new_page()
        context = await browser.new_context()
        add_init_script = getattr(context, "add_init_script", None)
        if add_init_script is not None:
            await add_init_script(_WEBDRIVER_MASK_SCRIPT)
        return await context.new_page()

    def _build_login_url(self) -> str:
        query = urlencode({"v": "3", "api_key": self._api_key_value})
        separator = "&" if "?" in self._login_url else "?"
        return f"{self._login_url}{separator}{query}"

    async def _fill_first_available(
        self,
        page: Any,
        selectors: Sequence[str],
        value: str,
    ) -> None:
        compound_selector = ", ".join(selectors)
        locator = page.locator(compound_selector).first
        try:
            await locator.wait_for(state="visible", timeout=self._timeout_ms)
            await locator.fill(value, timeout=self._timeout_ms)
            return
        except PlaywrightTimeoutError as exc:
            raise LoginPageStructureError(
                f"Could not find login input for selectors: {compound_selector}. "
                "The Kite login page may have changed."
            ) from exc

    async def _type_first_available(
        self,
        page: Any,
        selectors: Sequence[str],
        value: str,
    ) -> None:
        """Type a value key-by-key so input-event-driven auto-submit (e.g. TOTP) fires."""
        compound_selector = ", ".join(selectors)
        locator = page.locator(compound_selector).first
        try:
            await locator.wait_for(state="visible", timeout=self._timeout_ms)
            await locator.fill("", timeout=self._timeout_ms)
            await locator.press_sequentially(value, delay=50, timeout=self._timeout_ms)
            return
        except PlaywrightTimeoutError as exc:
            raise LoginPageStructureError(
                f"Could not find login input for selectors: {compound_selector}. "
                "The Kite login page may have changed or shown an unexpected screen."
            ) from exc

    async def _capture_diagnostics(self, page: Any, label: str) -> None:
        """Best-effort screenshot + input-field dump. Must never break the login flow."""
        try:
            path = str(Path(tempfile.gettempdir()) / f"kite_diag_{label}.png")
            await page.screenshot(path=path)
            inputs = await page.evaluate(
                "() => Array.from(document.querySelectorAll('input')).map(e => ({"
                "id: e.id, name: e.name, type: e.type, "
                "visible: !!(e.offsetWidth || e.offsetHeight), "
                "value_len: (e.value || '').length}))"
            )
            logger.info("DIAG[{}] url={}", label, page.url)
            logger.info("DIAG[{}] inputs={}", label, inputs)
            logger.info("DIAG[{}] screenshot -> {}", label, path)
        except Exception as exc:
            logger.warning("DIAG[{}] capture failed: {}", label, exc)

    async def _click_first_available(
        self,
        page: Any,
        selectors: Sequence[str],
        *,
        required: bool = True,
    ) -> bool:
        compound_selector = ", ".join(selectors)
        locator = page.locator(compound_selector).first
        try:
            await locator.wait_for(state="visible", timeout=self._timeout_ms)
            await locator.click(timeout=self._timeout_ms)
            return True
        except PlaywrightTimeoutError as exc:
            if required:
                raise LoginPageStructureError(
                    f"Could not click login button for selectors: {compound_selector}. "
                    "The control may be missing, disabled, or not actionable."
                ) from exc
            return False


    def _validate_required_settings(self) -> None:
        missing = [
            name
            for name, value in (
                ("KITE_API_KEY", self._api_key),
                ("KITE_USER_ID", self._user_id),
                ("KITE_PASSWORD", self._password),
                ("KITE_TOTP_SECRET", self._totp_secret),
            )
            if value is None
        ]
        if missing:
            raise ConfigurationError(
                f"Missing required Playwright auth settings: {', '.join(missing)}"
            )

    @property
    def _api_key_value(self) -> str:
        if self._api_key is None:
            raise ConfigurationError("KITE_API_KEY is required")
        return self._api_key

    @property
    def _user_id_value(self) -> str:
        if self._user_id is None:
            raise ConfigurationError("KITE_USER_ID is required")
        return self._user_id

    @property
    def _password_value(self) -> str:
        if self._password is None:
            raise ConfigurationError("KITE_PASSWORD is required")
        return self._password

    @property
    def _totp_secret_value(self) -> str:
        if self._totp_secret is None:
            raise ConfigurationError("KITE_TOTP_SECRET is required")
        return self._totp_secret

    @staticmethod
    def _secret_value(secret: object | None) -> str | None:
        if secret is None:
            return None
        if hasattr(secret, "get_secret_value"):
            return str(secret.get_secret_value())
        return str(secret)


def extract_request_token(url: str) -> str:
    """Extract request_token from a Kite redirect URL."""
    query = parse_qs(urlparse(url).query)

    status = query.get("status", [None])[0]
    if status == "error":
        message = query.get("message", ["Kite login failed"])[0]
        raise AuthenticationError(message)

    request_token = query.get("request_token", [None])[0]
    if not request_token:
        raise RequestTokenNotFoundError("Kite redirect URL did not contain request_token.")

    return request_token
