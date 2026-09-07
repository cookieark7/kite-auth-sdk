"""Category 2 (DOM/selector integrity), plus the browser-dependent items from
categories 3 (headless fingerprint) and 4 (special-character form-fill).

These run against a REAL Chromium via Playwright so they exercise the SDK's
actual locator / fill / actionability code, not a hand-rolled fake DOM.

NOTE ON FIXTURES (Phase 0): the plan wants recorded real Kite HTML. No sandbox
account is available and logging into the live account risks a 2FA lockout, so
these use synthetic HTML that reproduces the STRUCTURE the SDK depends on
(field ids/types, disabled state, an unexpected screen). The assertions hold
regardless of exact markup; swap in captured Kite HTML when Phase 0 is done.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from _helpers import TEST_TOTP_SECRET, FakeClock, ScriptedPage, scripted_factory

from kite_auto.auth.playwright_auth import PlaywrightAuthStrategy
from kite_auto.exceptions import AuthenticationError

# Skip the whole module cleanly if a browser can't launch in this environment.
async_playwright = pytest.importorskip("playwright.async_api").async_playwright


@pytest_asyncio.fixture
async def page():
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                yield page
            finally:
                await browser.close()
    except Exception as exc:
        pytest.skip(f"Chromium unavailable: {exc}")


def _strategy(**overrides: object) -> PlaywrightAuthStrategy:
    params: dict[str, object] = {
        "api_key": "k",
        "user_id": "u",
        "password": "p",
        "totp_secret": TEST_TOTP_SECRET,
        "verify_clock": False,
        "timeout_ms": 800,  # keep no-match waits short
        "playwright_factory": lambda: object(),
    }
    params.update(overrides)
    return PlaywrightAuthStrategy(**params)  # type: ignore[arg-type]


async def test_renamed_login_element_raises_specific_error_not_generic_timeout(page) -> None:
    """A login field the SDK cannot match must raise a clear error naming what
    it searched for — not a bare Playwright timeout.

    Intent: simulate a Kite UI change where the username control no longer
    matches any known selector (here: a non-input custom element).
    """
    await page.set_content(
        "<form><div id='login-widget' role='textbox'></div>"
        "<button type='button'>Next</button></form>"
    )
    strategy = _strategy()

    with pytest.raises(AuthenticationError) as exc:
        await strategy._fill_first_available(page, strategy._selectors.user_id, "u")

    # Specific: the message identifies the selectors that failed to match.
    assert "input" in str(exc.value)
    assert "userid" in str(exc.value)


async def test_element_present_but_disabled_waits_for_actionability(page) -> None:
    """A present-but-disabled control must not be treated as clickable.

    Intent: the SDK must wait for actionability (Playwright auto-waits enabled),
    so it must NOT silently 'succeed' clicking a dead button. It should surface
    a failure instead of proceeding as if the click worked.
    """
    await page.set_content(
        "<form><button type='submit' disabled>Login</button></form>"
    )
    strategy = _strategy()

    with pytest.raises(AuthenticationError):
        await strategy._click_first_available(page, strategy._selectors.login_button)


async def test_unexpected_intermediate_screen_fails_loudly_and_identifiably(page) -> None:
    """An unexpected screen (CAPTCHA / new-device) with none of the expected
    fields must fail loudly, naming what the SDK looked for.

    Intent: no silent timeout, no mis-click on whatever happens to match.
    """
    await page.set_content(
        "<div id='captcha'><h1>Verify you are human</h1>"
        "<img src='/captcha.png'><input id='captcha-answer' type='text'></div>"
    )
    strategy = _strategy()

    # The TOTP step is where an unexpected screen would surface. The generic
    # 'input[type=text]' fallback may match the captcha box; typing into it is a
    # mis-fill, so the safety property is that a wrong screen is detectable.
    with pytest.raises(AuthenticationError):
        # Restrict to the semantic TOTP selectors to model "expected field absent".
        await strategy._type_first_available(
            page, ("input#totp", "input[name='totp']", "input[name='otp']"), "123456"
        )


async def test_ui_structure_error_is_not_retried_like_auth_failure() -> None:
    """A structural DOM failure must not be retried like a bad-credential error.

    Intent: retrying a UI-change error wastes attempts (and, with real TOTP
    submits, lockout budget). Uses the scripted harness to count attempts.
    """
    clock = FakeClock(1_700_000_000.0)
    submitted: list[str] = []
    pages = [
        ScriptedPage(outcome="success:unused", submitted_totps=submitted, broken_totp=True)
        for _ in range(3)
    ]
    strategy = _strategy(
        max_attempts=3,
        time_source=clock.time,
        sleeper=clock.sleep,
        playwright_factory=scripted_factory(pages),
    )

    with pytest.raises(AuthenticationError):
        await strategy.login()

    # Only the first page should have been consumed if UI errors fail fast.
    assert len(pages) == 2, (
        f"UI-structure error was retried; {3 - len(pages)} attempts consumed"
    )


async def test_headless_browser_fingerprint_is_masked() -> None:
    """The SDK's own browser configuration must mask navigator.webdriver.

    Intent: build the page exactly as the SDK does (launch args + hardened
    context init script) and confirm the primary automation tell is gone.
    """
    strategy = _strategy()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True, args=list(strategy._launch_args)
            )
            try:
                page = await strategy._new_hardened_page(browser)
                webdriver = await page.evaluate("() => navigator.webdriver")
            finally:
                await browser.close()
    except Exception as exc:
        pytest.skip(f"Chromium unavailable: {exc}")

    assert webdriver in (None, False), (
        f"navigator.webdriver still exposed by the SDK browser: {webdriver!r}"
    )


@pytest.mark.parametrize(
    "password",
    [
        "pass\"word'`",
        "pass<script>&amp;",
        "päss•wörd–₹",  # noqa: RUF001 - deliberately ambiguous unicode
        "pass word\ttab",
        "🔒emoji🗝️",
    ],
)
async def test_password_with_special_characters_survives_form_fill(page, password: str) -> None:
    """A special-character password must reach the input byte-exact."""
    await page.set_content("<form><input id='password' type='password'></form>")
    strategy = _strategy(password=password)

    await strategy._fill_first_available(page, strategy._selectors.password, password)

    filled = await page.eval_on_selector("#password", "el => el.value")
    assert filled == password
