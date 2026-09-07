"""Step 5 — 09:20 knowledge only. T2 lives here."""

from datetime import time

import pandas as pd
import pytest
from _fixtures import LAST_BAR, OPEN_BAR, make_bars, make_session, session_dates

from scanner.features import build_features

# A session whose full-day range is 1% of its close — enough for adr_pct to be
# non-degenerate, and invisible to anything that only reads the 09:15 bar.
RANGE_1_PCT = {LAST_BAR: {"high": 101.0}}


def features_for(bars: pd.DataFrame) -> pd.DataFrame:
    return build_features(bars).reset_index(drop=True)


def row_for(features: pd.DataFrame, session_date: object) -> pd.Series:
    rows = features[features["session_date"] == session_date]
    assert len(rows) == 1
    return rows.iloc[0]


def test_rvol_arithmetic() -> None:
    days = session_dates(15)
    bars = make_bars(
        [make_session("AAA", day, or5_vol=100, overrides=RANGE_1_PCT) for day in days[:14]]
        + [make_session("AAA", days[14], or5_vol=300, overrides=RANGE_1_PCT)]
    )

    features = features_for(bars)

    assert row_for(features, days[14])["opening_rvol"] == 3.0


def test_rvol_excludes_today() -> None:
    days = session_dates(15)
    prior = [make_session("AAA", day, or5_vol=100, overrides=RANGE_1_PCT) for day in days[:14]]

    def rvol(today_volume: int) -> float:
        bars = make_bars(
            [*prior, make_session("AAA", days[14], or5_vol=today_volume, overrides=RANGE_1_PCT)]
        )
        return float(row_for(features_for(bars), days[14])["opening_rvol"])

    assert rvol(300) == 3.0
    assert rvol(600) == 6.0


def test_insufficient_history() -> None:
    days = session_dates(11)
    bars = make_bars([make_session("AAA", day, overrides=RANGE_1_PCT) for day in days])

    features = features_for(bars)

    assert features["session_date"].tolist() == [days[10]]


def test_adr_pct() -> None:
    days = session_dates(21)
    bars = make_bars(
        [make_session("AAA", day, overrides={LAST_BAR: {"high": 101.0}}) for day in days[:10]]
        + [make_session("AAA", day, overrides={LAST_BAR: {"high": 102.0}}) for day in days[10:20]]
        + [make_session("AAA", days[20], overrides=RANGE_1_PCT)]
    )

    features = features_for(bars)

    # ten prior sessions at (101-100)/100, ten at (102-100)/100.
    assert row_for(features, days[20])["adr_pct"] == pytest.approx(0.015)


def test_gap_in_adr() -> None:
    days = session_dates(21)
    gap_open = {OPEN_BAR: {"open": 102.0, "high": 102.0, "low": 102.0, "close": 102.0}}
    bars = make_bars(
        [make_session("AAA", day, overrides={LAST_BAR: {"high": 101.5}}) for day in days[:20]]
        + [make_session("AAA", days[20], overrides={**gap_open, **RANGE_1_PCT})]
    )

    today = row_for(features_for(bars), days[20])

    assert today["adr_pct"] == pytest.approx(0.015)
    assert today["gap_pct"] == pytest.approx(0.02)
    assert today["gap_in_adr"] == pytest.approx(0.02 / 0.015)
    assert today["ret_0920"] == pytest.approx(0.02)
    assert today["close_0920"] == 102.0


def test_no_lookahead() -> None:
    """T2 — nothing computed at 09:20 may read a bar timestamped after 09:19.

    Prior sessions' full-day bars are not lookahead: adr_pct and prev_close are
    yesterday-knowledge by definition. So the deletion arm removes post-09:19
    bars from the session under test, while the poisoning arm covers *every*
    session for volume — the channel no feature may read past 09:19 at all.
    """
    days = session_dates(15)
    baseline = make_bars([make_session("AAA", day, overrides=RANGE_1_PCT) for day in days])
    expected = features_for(baseline)

    after_open = baseline["timestamp"].dt.time > time(9, 19)

    volume_poisoned = baseline.copy()
    volume_poisoned.loc[after_open, "volume"] = 10**9
    assert_same(features_for(volume_poisoned), expected)

    today = after_open & (baseline["timestamp"].dt.date == days[14])

    poisoned = baseline.copy()
    poisoned.loc[today, "volume"] = 10**9
    poisoned.loc[today, ["close", "high"]] = 10**6
    assert_same(features_for(poisoned), expected)

    deleted = baseline[~today].reset_index(drop=True)
    assert_same(features_for(deleted), expected)


def assert_same(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False)
