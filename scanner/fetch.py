"""Step 2 — chunking, rate limiting, and Candle -> DataFrame.

The SDK deliberately leaves all three to the caller: ``historical_data`` does not
chunk, and ``RestClient`` applies no ``RateLimiter`` unless one is injected.
"""

from datetime import date, datetime, timedelta
from typing import Protocol

import pandas as pd
from loguru import logger

from kite_auto import KiteClient
from kite_auto.auth.playwright_auth import PlaywrightAuthStrategy
from kite_auto.auth.token_manager import TokenManager
from kite_auto.client.rest_client import RestClient
from kite_auto.config import get_settings
from kite_auto.models import CandleInterval
from kite_auto.models.historical import Candle
from kite_auto.utils.rate_limit import RateLimiter
from kite_auto.utils.retry import AsyncRetryPolicy
from scanner.config import (
    INTERVAL,
    IST,
    MAX_DAYS_PER_REQUEST,
    RATE_LIMIT_PER_SEC,
    RAW_DIR,
    SESSION_END,
    SESSION_START,
)

FRAME_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class HistoricalSource(Protocol):
    """The one SDK method this module needs — the seam tests inject through."""

    async def historical_data(
        self,
        instrument_token: int,
        interval: CandleInterval | str,
        *,
        from_: datetime | str,
        to: datetime | str,
        continuous: bool = False,
        oi: bool = False,
    ) -> tuple[Candle, ...]:
        """Return historical candles for an instrument token."""


def make_rate_limited_client() -> KiteClient:
    """A ``KiteClient`` whose ``RestClient`` carries a ``RateLimiter``.

    The SDK default is ``None``. Building the client by hand is the only way to
    inject one, because ``KiteClient`` constructs its own ``RestClient`` otherwise.
    The retry policy retries ``TransientKiteApiError`` only, so authentication
    failures still raise on the first attempt instead of burning lockout budget.
    """
    settings = get_settings()
    token_manager = TokenManager(auth_strategy=PlaywrightAuthStrategy(settings))
    rest_client = RestClient(
        settings,
        api_key=settings.kite_api_key,
        access_token_provider=token_manager.get_access_token,
        rate_limiter=RateLimiter(rate=RATE_LIMIT_PER_SEC),
        retry_policy=AsyncRetryPolicy(),
    )
    return KiteClient(settings, token_manager=token_manager, rest_client=rest_client)


def candles_to_frame(candles: tuple[Candle, ...]) -> pd.DataFrame:
    """Convert SDK candles into a tz-aware IST bar frame.

    Every ``Candle`` field is ``| None`` because the SDK tolerates malformed rows.
    The scanner must not: a silently missing volume is a wrong RVOL, not a crash.
    """
    rows = []
    for index, candle in enumerate(candles):
        row = {
            "timestamp": candle.timestamp,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        missing = [name for name, value in row.items() if value is None]
        if missing:
            raise ValueError(
                f"Candle {index} is missing {', '.join(missing)}: {candle.raw!r}"
            )
        rows.append(row)

    frame = pd.DataFrame(rows, columns=FRAME_COLUMNS)
    frame["timestamp"] = _to_ist(frame["timestamp"], naive=bool(rows) and _is_naive(candles[0]))
    frame["volume"] = frame["volume"].astype("int64")
    return frame


def date_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Split an inclusive date range into spans of at most MAX_DAYS_PER_REQUEST days."""
    if end < start:
        raise ValueError(f"end {end} precedes start {start}")

    chunks: list[tuple[date, date]] = []
    span = timedelta(days=MAX_DAYS_PER_REQUEST - 1)
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + span, end)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return chunks


async def fetch_symbol(
    client: HistoricalSource,
    token: int,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Every chunk for one symbol, concatenated, sorted, boundary duplicates removed."""
    frames = [
        candles_to_frame(
            await client.historical_data(
                token,
                INTERVAL,
                from_=datetime.combine(chunk_start, SESSION_START, tzinfo=IST),
                to=datetime.combine(chunk_end, SESSION_END, tzinfo=IST),
            )
        )
        for chunk_start, chunk_end in date_chunks(start, end)
    ]
    frame = pd.concat(frames, ignore_index=True)
    frame = frame.sort_values("timestamp", kind="stable")
    frame = frame.drop_duplicates(subset="timestamp", keep="first")
    return frame.reset_index(drop=True)


async def fetch_universe(
    client: HistoricalSource,
    universe: pd.DataFrame,
    start: date,
    end: date,
) -> None:
    """Write ``data/raw/<tradingsymbol>.parquet`` per symbol, resuming where it left off.

    Sequential on purpose: at 3 requests/second the rate limiter is the bottleneck,
    so concurrency buys nothing and only makes a partial run harder to reason about.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    symbols: list[str] = universe["tradingsymbol"].astype(str).tolist()
    tokens: list[int] = universe["instrument_token"].astype("int64").tolist()
    for symbol, token in zip(symbols, tokens, strict=True):
        destination = RAW_DIR / f"{symbol}.parquet"
        if destination.exists():
            continue
        frame = await fetch_symbol(client, token, start, end)
        frame.to_parquet(destination, index=False)
        logger.info("Fetched {} bars for {}", len(frame), symbol)


def _is_naive(candle: Candle) -> bool:
    return candle.timestamp is not None and candle.timestamp.tzinfo is None


def _to_ist(timestamps: pd.Series, *, naive: bool) -> pd.Series:
    if naive:
        return pd.to_datetime(timestamps).dt.tz_localize(IST)
    return pd.to_datetime(timestamps, utc=True).dt.tz_convert(IST)
