"""Step 4 — the four cleaning rules, applied in order."""

from datetime import date

import pandas as pd

from scanner.config import (
    MIN_BARS_PER_DAY,
    SESSION_END,
    SESSION_START,
    SPLIT_GAP_THRESHOLD,
    SPLIT_QUARANTINE_SESSIONS,
    WARMUP_SESSIONS,
)

SessionKey = tuple[str, date]


def add_session_date(bars: pd.DataFrame) -> pd.DataFrame:
    """Attach the IST calendar date each bar belongs to."""
    frame = bars.copy()
    frame["session_date"] = frame["timestamp"].dt.date
    return frame


def clean(bars: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Apply the four cleaning rules, returning the rows each one removed."""
    frame = add_session_date(bars)
    dropped: dict[str, int] = {}

    before = len(frame)
    bar_times = frame["timestamp"].dt.time
    frame = frame[(bar_times >= SESSION_START) & (bar_times < SESSION_END)]
    dropped["session_window"] = before - len(frame)

    before = len(frame)
    bars_per_session = frame.groupby(["tradingsymbol", "session_date"])["timestamp"].transform(
        "size"
    )
    frame = frame[bars_per_session >= MIN_BARS_PER_DAY]
    dropped["short_day"] = before - len(frame)

    before = len(frame)
    frame = _drop_sessions(frame, _corporate_action_sessions(frame))
    dropped["split_gap"] = before - len(frame)

    before = len(frame)
    frame = _drop_sessions(frame, _warmup_sessions(frame))
    dropped["warmup"] = before - len(frame)

    return frame.reset_index(drop=True), dropped


def session_table(bars: pd.DataFrame) -> pd.DataFrame:
    """One row per (tradingsymbol, session_date) with the day's open and close."""
    ordered = bars.sort_values(["tradingsymbol", "timestamp"], kind="stable")
    return (
        ordered.groupby(["tradingsymbol", "session_date"], sort=True)
        .agg(first_open=("open", "first"), last_close=("close", "last"))
        .reset_index()
    )


def _corporate_action_sessions(bars: pd.DataFrame) -> set[SessionKey]:
    """Sessions gapping past the threshold, plus the ones quarantined behind them.

    A 1:5 split looks like an 80% overnight move. Left in, split days sit at the
    top of every ranked list forever.
    """
    quarantined: set[SessionKey] = set()
    sessions = session_table(bars)

    for symbol, group in sessions.groupby("tradingsymbol", sort=False):
        ordered = group.sort_values("session_date", kind="stable")
        gap = ordered["first_open"] / ordered["last_close"].shift(1) - 1.0
        # The first session of a symbol has no prior close, so gap is NaN and the
        # comparison is False — no prior close means no gap to judge.
        flagged = (gap.abs() > SPLIT_GAP_THRESHOLD).tolist()
        session_dates = ordered["session_date"].tolist()

        for position, is_split in enumerate(flagged):
            if not is_split:
                continue
            for offset in range(SPLIT_QUARANTINE_SESSIONS + 1):
                if position + offset < len(session_dates):
                    quarantined.add((str(symbol), session_dates[position + offset]))

    return quarantined


def _warmup_sessions(bars: pd.DataFrame) -> set[SessionKey]:
    sessions = (
        bars[["tradingsymbol", "session_date"]]
        .drop_duplicates()
        .sort_values(["tradingsymbol", "session_date"], kind="stable")
    )
    position = sessions.groupby("tradingsymbol", sort=False).cumcount()
    warmup = sessions[position < WARMUP_SESSIONS]
    return set(zip(warmup["tradingsymbol"], warmup["session_date"], strict=True))


def _drop_sessions(bars: pd.DataFrame, sessions: set[SessionKey]) -> pd.DataFrame:
    if not sessions:
        return bars
    keys = zip(bars["tradingsymbol"], bars["session_date"], strict=True)
    keep = pd.Series([key not in sessions for key in keys], index=bars.index)
    return bars[keep]
