"""Step 6 — outcomes measured after 09:20, which is what makes them outcomes."""

import pandas as pd

from scanner.clean import add_session_date
from scanner.config import EVAL_END, EVAL_START, SESSION_END, SESSION_START

SESSION_KEYS = ["tradingsymbol", "session_date"]


def build_metrics(bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """Add ``opportunity`` and ``vwap_side_frac`` to a features frame."""
    outcomes = _session_outcomes(bars)
    merged = features.merge(outcomes, on=SESSION_KEYS, how="left")

    missing = merged[merged["eval_high"].isna()]
    if not missing.empty:
        offenders = missing[SESSION_KEYS].head(3).to_dict("records")
        raise ValueError(
            f"{len(missing)} feature rows have no bars in the evaluation window: {offenders}"
        )

    up = (merged["eval_high"] - merged["close_0920"]) / merged["close_0920"]
    down = (merged["close_0920"] - merged["eval_low"]) / merged["close_0920"]
    merged["opportunity"] = up.where(up > down, down) / merged["adr_pct"]

    return merged.drop(columns=["eval_high", "eval_low"])


def _session_outcomes(bars: pd.DataFrame) -> pd.DataFrame:
    frame = add_session_date(bars).sort_values(["tradingsymbol", "timestamp"], kind="stable")
    bar_times = frame["timestamp"].dt.time
    session = frame[(bar_times >= SESSION_START) & (bar_times < SESSION_END)]

    vwap = _running_vwap(session)
    bar_times = session["timestamp"].dt.time
    evaluated = pd.DataFrame(
        {
            "tradingsymbol": session["tradingsymbol"],
            "session_date": session["session_date"],
            "high": session["high"],
            "low": session["low"],
            "above": session["close"] > vwap,
            "below": session["close"] < vwap,
        }
    )[(bar_times >= EVAL_START) & (bar_times <= EVAL_END)]

    outcomes = (
        evaluated.groupby(SESSION_KEYS, sort=False)
        .agg(
            eval_high=("high", "max"),
            eval_low=("low", "min"),
            above=("above", "sum"),
            below=("below", "sum"),
            eval_bars=("high", "size"),
        )
        .reset_index()
    )
    outcomes["vwap_side_frac"] = (
        outcomes[["above", "below"]].max(axis=1) / outcomes["eval_bars"]
    )
    trimmed: pd.DataFrame = outcomes.drop(columns=["above", "below", "eval_bars"])
    return trimmed


def _running_vwap(session: pd.DataFrame) -> pd.Series:
    """Cumulative VWAP from 09:15, typical price, reset each session.

    The opening bar is included in the accumulation even though it is never part
    of the evaluation window — VWAP that starts at 09:20 is a different number.
    """
    keys = [session["tradingsymbol"], session["session_date"]]
    typical = (session["high"] + session["low"] + session["close"]) / 3.0
    traded_value = (typical * session["volume"]).groupby(keys, sort=False).cumsum()
    traded_volume = session["volume"].groupby(keys, sort=False).cumsum()
    return traded_value / traded_volume
