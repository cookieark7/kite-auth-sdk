"""Host clock drift verification against a trusted external time source.

TOTP codes are bound to a 30-second window derived from the *server's* clock. If
the host clock drifts far enough, every generated code lands in the wrong window
and is rejected no matter how many times we retry. This module measures local
drift against a trusted source so that condition is diagnosable instead of looking
like "TOTP randomly stopped working".
"""

import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime

import httpx
from loguru import logger

from kite_auto.exceptions import ClockDriftError

# The Date header from the host that validates the TOTP is the most relevant
# reference: it measures drift against the exact clock domain we must agree with.
DEFAULT_TIME_URL = "https://kite.zerodha.com"

type TrustedTimeSource = Callable[[], Awaitable[float | None]]
type LocalTimeSource = Callable[[], float]


async def fetch_trusted_epoch(
    url: str = DEFAULT_TIME_URL,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 - forwarded to httpx, not an asyncio cancel scope
) -> float | None:
    """Return trusted epoch seconds from an HTTP ``Date`` header, or ``None``.

    Best-effort: any network failure or missing header returns ``None`` so the
    drift check can degrade to a warning rather than blocking login.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.head(url)
        date_header = response.headers.get("Date")
        if not date_header:
            return None
        return parsedate_to_datetime(date_header).timestamp()
    except Exception as exc:  # network/parse errors must not break login
        logger.debug("Trusted time fetch failed: {}", exc)
        return None


async def measure_clock_drift(
    *,
    trusted_time_source: TrustedTimeSource = fetch_trusted_epoch,
    local_time_source: LocalTimeSource = time.time,
) -> float | None:
    """Return absolute host-vs-trusted drift in seconds, or ``None`` if unknown."""
    trusted = await trusted_time_source()
    if trusted is None:
        return None
    return abs(local_time_source() - trusted)


async def assert_clock_sync(
    *,
    max_drift_seconds: float,
    hard_fail: bool,
    trusted_time_source: TrustedTimeSource = fetch_trusted_epoch,
    local_time_source: LocalTimeSource = time.time,
) -> float | None:
    """Verify host clock against a trusted source.

    Returns the measured drift (or ``None`` if the trusted source was
    unavailable). When drift exceeds ``max_drift_seconds`` this either raises
    :class:`ClockDriftError` (``hard_fail=True``) or logs a loud warning.
    """
    drift = await measure_clock_drift(
        trusted_time_source=trusted_time_source,
        local_time_source=local_time_source,
    )
    if drift is None:
        logger.warning(
            "Could not verify host clock against trusted time source; "
            "skipping drift check (TOTP may fail if the clock is wrong)."
        )
        return None

    if drift > max_drift_seconds:
        message = (
            f"Host clock drift {drift:.1f}s exceeds tolerance {max_drift_seconds:.1f}s. "
            "TOTP codes will likely be rejected; sync the system clock (NTP)."
        )
        if hard_fail:
            raise ClockDriftError(message)
        logger.warning(message)
    else:
        logger.debug(
            "Host clock drift {:.1f}s within {:.1f}s tolerance", drift, max_drift_seconds
        )
    return drift
