"""Historical OHLCV candle models."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Self


class CandleInterval(StrEnum):
    """Supported Kite historical-data candle intervals."""

    MINUTE = "minute"
    MINUTE_3 = "3minute"
    MINUTE_5 = "5minute"
    MINUTE_10 = "10minute"
    MINUTE_15 = "15minute"
    MINUTE_30 = "30minute"
    MINUTE_60 = "60minute"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class Candle:
    """A single historical OHLCV candle.

    Kite returns candles as positional arrays:
    ``[timestamp, open, high, low, close, volume, oi?]``.
    """

    timestamp: datetime | None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None
    oi: int | None = None
    raw: tuple[Any, ...] | None = None

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> Self:
        """Parse a positional Kite candle row, tolerating short/malformed rows."""
        values = list(row)

        def at(index: int) -> Any:
            return values[index] if len(values) > index else None

        return cls(
            timestamp=parse_candle_timestamp(at(0)),
            open=_as_float(at(1)),
            high=_as_float(at(2)),
            low=_as_float(at(3)),
            close=_as_float(at(4)),
            volume=_as_int(at(5)),
            oi=_as_int(at(6)),
            raw=tuple(values),
        )


def parse_candle_timestamp(value: Any) -> datetime | None:
    """Parse a Kite candle timestamp (e.g. ``2026-06-26T09:15:00+0530``)."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    for candidate in (value, value.replace(" ", "T")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def format_candle_time(value: datetime | str) -> str:
    """Format a from/to bound as Kite's ``yyyy-mm-dd HH:MM:SS`` query value."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
