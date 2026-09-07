"""Step 6 — outcomes. These read post-09:20 bars, which is their job."""

from datetime import date, time

import pandas as pd
import pytest
from _fixtures import LAST_BAR, make_session, session_dates

from scanner.config import EVAL_START
from scanner.metrics import build_metrics

DAY = session_dates(1)[0]


def features_frame(day: date, *, close_0920: float = 100.0, adr_pct: float = 0.02) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tradingsymbol": ["AAA"],
            "session_date": [day],
            "close_0920": [close_0920],
            "adr_pct": [adr_pct],
        }
    )


def test_opportunity_up() -> None:
    bars = make_session("AAA", DAY, overrides={time(10, 0): {"high": 110.0}})

    metrics = build_metrics(bars, features_frame(DAY))

    assert metrics["opportunity"].iloc[0] == pytest.approx(0.10 / 0.02)


def test_opportunity_down() -> None:
    bars = make_session("AAA", DAY, overrides={time(10, 0): {"low": 90.0}})

    metrics = build_metrics(bars, features_frame(DAY))

    assert metrics["opportunity"].iloc[0] == pytest.approx(0.10 / 0.02)


def test_eval_window() -> None:
    """A spike after EVAL_END is not an opportunity you could have taken."""
    bars = make_session(
        "AAA",
        DAY,
        overrides={time(10, 0): {"high": 110.0}, LAST_BAR: {"high": 200.0}},
    )

    metrics = build_metrics(bars, features_frame(DAY))

    assert metrics["opportunity"].iloc[0] == pytest.approx(0.10 / 0.02)


def test_vwap_side_frac_all_above() -> None:
    bars = make_session(
        "AAA",
        DAY,
        level=110.0,
        overrides={time(9, 15): {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}},
    )

    metrics = build_metrics(bars, features_frame(DAY, close_0920=100.0))

    assert metrics["vwap_side_frac"].iloc[0] == 1.0


def test_vwap_side_frac_alternating() -> None:
    bars = make_session("AAA", DAY, or5_vol=10, rest_vol=10)
    after_open = bars.index[bars["timestamp"].dt.time >= EVAL_START]
    alternating = [110.0 if position % 2 == 0 else 90.0 for position in range(len(after_open))]
    for column in ("open", "high", "low", "close"):
        bars.loc[after_open, column] = alternating

    metrics = build_metrics(bars, features_frame(DAY))

    assert metrics["vwap_side_frac"].iloc[0] == 0.5


def test_features_without_bars_raise() -> None:
    bars = make_session("AAA", DAY)

    with pytest.raises(ValueError, match="BBB"):
        build_metrics(
            bars,
            pd.DataFrame(
                {
                    "tradingsymbol": ["BBB"],
                    "session_date": [DAY],
                    "close_0920": [100.0],
                    "adr_pct": [0.02],
                }
            ),
        )
