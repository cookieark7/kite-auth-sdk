"""Step 5 — features knowable at 09:20, and nothing else.

Every trailing window shifts before it rolls. A ``.rolling()`` that has not been
preceded by ``.shift(1)`` includes the current session in its own baseline, which
produces a confidently wrong table rather than a crash.
"""

import pandas as pd

from scanner.clean import add_session_date
from scanner.config import (
    ADR_LOOKBACK,
    MIN_PRIOR_SESSIONS,
    OPEN_WINDOW_END,
    RVOL_LOOKBACK,
    SESSION_END,
    SESSION_START,
)

FEATURE_COLUMNS = [
    "tradingsymbol",
    "session_date",
    "or5_vol",
    "opening_rvol",
    "adr_pct",
    "gap_pct",
    "gap_in_adr",
    "ret_0920",
    "close_0920",
]


def build_features(bars: pd.DataFrame) -> pd.DataFrame:
    """One row per (tradingsymbol, session_date) of 09:20 knowledge."""
    sessions = _session_frame(bars)
    symbol = sessions["tradingsymbol"]

    prior_sessions = sessions.groupby("tradingsymbol", sort=False).cumcount()
    prev_close = sessions.groupby("tradingsymbol", sort=False)["day_close"].shift(1)

    day_range_pct = (sessions["day_high"] - sessions["day_low"]) / sessions["day_close"]
    adr_pct = _trailing_mean(day_range_pct, symbol, ADR_LOOKBACK)
    rvol_base = _trailing_mean(sessions["or5_vol"], symbol, RVOL_LOOKBACK)

    # Skip missing sessions rather than forward-filling them: a stock that did not
    # trade has no opening bar, and inventing one invents an RVOL.
    keep = prior_sessions >= MIN_PRIOR_SESSIONS
    _require_usable_divisors(sessions[keep], adr_pct[keep], rvol_base[keep])

    gap_pct = (sessions["open_0915"] - prev_close) / prev_close
    features = pd.DataFrame(
        {
            "tradingsymbol": symbol,
            "session_date": sessions["session_date"],
            "or5_vol": sessions["or5_vol"],
            "opening_rvol": sessions["or5_vol"] / rvol_base,
            "adr_pct": adr_pct,
            "gap_pct": gap_pct,
            "gap_in_adr": gap_pct.abs() / adr_pct,
            "ret_0920": (sessions["close_0920"] - prev_close) / prev_close,
            "close_0920": sessions["close_0920"],
        },
        columns=FEATURE_COLUMNS,
    )
    return features[keep].reset_index(drop=True)


def _session_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """Per-session aggregates, split by what is knowable at 09:20 and what is not.

    ``day_high``/``day_low``/``day_close`` describe the whole session and are only
    ever read through a ``.shift(1)``, i.e. as yesterday's knowledge.
    """
    frame = add_session_date(bars).sort_values(["tradingsymbol", "timestamp"], kind="stable")
    bar_times = frame["timestamp"].dt.time
    session = frame[(bar_times >= SESSION_START) & (bar_times < SESSION_END)]
    opening = frame[(bar_times >= SESSION_START) & (bar_times < OPEN_WINDOW_END)]

    whole_day = (
        session.groupby(["tradingsymbol", "session_date"], sort=True)
        .agg(day_high=("high", "max"), day_low=("low", "min"), day_close=("close", "last"))
        .reset_index()
    )
    open_window = (
        opening.groupby(["tradingsymbol", "session_date"], sort=True)
        .agg(
            or5_vol=("volume", "sum"),
            open_0915=("open", "first"),
            close_0920=("close", "last"),
        )
        .reset_index()
    )
    sessions: pd.DataFrame = whole_day.merge(
        open_window, on=["tradingsymbol", "session_date"], how="inner"
    )
    return sessions


def _trailing_mean(values: pd.Series, symbol: pd.Series, window: int) -> pd.Series:
    """Mean over the prior ``window`` sessions of the same symbol, excluding today."""
    return values.groupby(symbol, sort=False).transform(
        lambda group: group.shift(1).rolling(window, min_periods=MIN_PRIOR_SESSIONS).mean()
    )


def _require_usable_divisors(
    sessions: pd.DataFrame,
    adr_pct: pd.Series,
    rvol_base: pd.Series,
) -> None:
    """Fail loudly instead of emitting inf/NaN that would rank at the top of the table."""
    broken = ~(adr_pct > 0) | ~(rvol_base > 0)
    if not bool(broken.any()):
        return
    offenders = sessions[broken][["tradingsymbol", "session_date"]]
    raise ValueError(
        f"{len(offenders)} stock-days have a non-positive adr_pct or RVOL baseline, "
        f"starting with {offenders.iloc[0].to_dict()}"
    )
