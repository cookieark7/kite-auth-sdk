"""Synthetic fixtures for the scanner suite.

Every frame here is built from explicit numbers chosen by hand — no random
generators, no network, no credentials. A scanner test that logs in is a bug.

Named ``_fixtures`` rather than ``conftest`` (matching ``tests/adversarial``):
mypy rejects a second module named ``conftest`` under the same source root.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta

import pandas as pd

from scanner.config import BARS_PER_DAY, IST, SESSION_START

FIRST_SESSION = date(2026, 1, 5)  # a Monday

OPEN_BAR = time(9, 15)  # the only bar a 09:20 feature may read
LAST_BAR = time(15, 25)  # bar 75 of 75


def session_dates(count: int, *, start: date = FIRST_SESSION) -> list[date]:
    """Return ``count`` consecutive weekdays starting at ``start``."""
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def make_session(
    symbol: str,
    day: date,
    *,
    level: float = 100.0,
    or5_vol: int = 1_000,
    rest_vol: int = 10,
    bars: int = BARS_PER_DAY,
    overrides: Mapping[time, Mapping[str, float]] | None = None,
) -> pd.DataFrame:
    """Build one flat session of 5-minute bars, then apply per-bar overrides.

    Every bar sits at ``level`` (o=h=l=c) so any deviation a test asserts on is
    one it put there itself. ``overrides`` maps a bar's IST wall-clock time to the
    column values to set on it.
    """
    start = datetime.combine(day, SESSION_START, tzinfo=IST)
    frame = pd.DataFrame(
        {
            "tradingsymbol": symbol,
            "timestamp": [pd.Timestamp(start + timedelta(minutes=5 * i)) for i in range(bars)],
            "open": level,
            "high": level,
            "low": level,
            "close": level,
            "volume": [or5_vol if i == 0 else rest_vol for i in range(bars)],
        }
    )
    for at, values in (overrides or {}).items():
        mask = frame["timestamp"].dt.time == at
        if not bool(mask.any()):
            raise AssertionError(f"fixture has no bar at {at}")
        for column, value in values.items():
            frame.loc[mask, column] = value
    return frame


def make_bars(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate session frames into one bar frame."""
    return pd.concat(list(frames), ignore_index=True)


def flat_history(
    symbol: str,
    days: Sequence[date],
    *,
    level: float = 100.0,
    or5_vol: int = 1_000,
) -> pd.DataFrame:
    """A run of identical flat sessions — the neutral prior history."""
    return make_bars(
        [make_session(symbol, day, level=level, or5_vol=or5_vol) for day in days]
    )
