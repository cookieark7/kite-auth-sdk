"""Step 4 — the four cleaning rules. T3 lives here."""

from datetime import date, datetime, time, timedelta

import pandas as pd
from _fixtures import make_bars, make_session, session_dates

from scanner.clean import clean
from scanner.config import BARS_PER_DAY, IST, SESSION_START


def extra_bar(symbol: str, day: date, at: time) -> pd.DataFrame:
    """One out-of-session bar, appended to an otherwise normal fixture."""
    return pd.DataFrame(
        {
            "tradingsymbol": [symbol],
            "timestamp": [pd.Timestamp(datetime.combine(day, at, tzinfo=IST))],
            "open": [100.0],
            "high": [100.0],
            "low": [100.0],
            "close": [100.0],
            "volume": [10],
        }
    )


def test_session_mask() -> None:
    days = session_dates(21)
    bars = make_bars(
        [make_session("AAA", day) for day in days]
        + [extra_bar("AAA", days[-1], time(9, 7)), extra_bar("AAA", days[-1], time(15, 45))]
    )

    cleaned, dropped = clean(bars)

    assert dropped["session_window"] == 2
    bar_times = cleaned["timestamp"].dt.time
    assert bar_times.min() >= SESSION_START
    assert bar_times.max() == time(15, 25)


def test_short_day_dropped() -> None:
    days = session_dates(22)
    bars = make_bars(
        [make_session("AAA", day) for day in days[:20]]
        + [make_session("AAA", days[20], bars=60), make_session("AAA", days[21], bars=74)]
    )

    cleaned, dropped = clean(bars)

    assert dropped["short_day"] == 60
    assert cleaned["session_date"].unique().tolist() == [days[21]]


def test_warmup_dropped() -> None:
    days = session_dates(21)
    bars = make_bars([make_session("AAA", day) for day in days])

    cleaned, dropped = clean(bars)

    assert dropped["warmup"] == 20 * BARS_PER_DAY
    assert cleaned["session_date"].unique().tolist() == [days[20]]


def test_split_dropped() -> None:
    """T3 — a 1:5 split takes its own session and the two that follow it."""
    days = session_dates(26)
    bars = make_bars(
        [make_session("AAA", day, level=1000.0) for day in days[:22]]
        + [make_session("AAA", day, level=200.0) for day in days[22:]]
    )

    cleaned, dropped = clean(bars)

    assert dropped["split_gap"] == 3 * BARS_PER_DAY
    assert cleaned["session_date"].unique().tolist() == [days[20], days[21], days[25]]


def test_drop_counts() -> None:
    days = session_dates(26)
    bars = make_bars(
        [make_session("AAA", day, level=1000.0) for day in days[:22]]
        + [make_session("AAA", day, level=200.0) for day in days[22:]]
        + [extra_bar("AAA", days[0], time(9, 7))]
    )

    cleaned, dropped = clean(bars)

    assert sum(dropped.values()) == len(bars) - len(cleaned)


def test_split_quarantine_is_per_symbol() -> None:
    days = session_dates(26)
    split = make_bars(
        [make_session("AAA", day, level=1000.0) for day in days[:22]]
        + [make_session("AAA", day, level=200.0) for day in days[22:]]
    )
    calm = make_bars([make_session("BBB", day) for day in days])

    cleaned, _ = clean(make_bars([split, calm]))

    surviving = cleaned.groupby("tradingsymbol")["session_date"].nunique()
    assert surviving["AAA"] == 3
    assert surviving["BBB"] == 6


def test_first_session_without_prior_close_is_kept() -> None:
    """No prev_close means no gap to judge — the session is not a split candidate."""
    days = session_dates(21)
    bars = make_bars([make_session("AAA", day, level=100.0) for day in days])

    _, dropped = clean(bars)

    assert dropped["split_gap"] == 0


def test_session_date_spans_the_whole_day() -> None:
    days = session_dates(21)
    bars = make_bars([make_session("AAA", day) for day in days])

    cleaned, _ = clean(bars)

    last = cleaned[cleaned["session_date"] == days[20]]
    assert len(last) == BARS_PER_DAY
    assert last["timestamp"].max() - last["timestamp"].min() == timedelta(minutes=370)
