"""Step 7 — the decile table. Ranking is within date, never pooled."""

import pandas as pd
from _fixtures import session_dates

from scanner.analyze import decile_table


def metrics_frame(
    session_date: object,
    rank_values: list[float],
    opportunities: list[float],
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tradingsymbol": [f"S{index:03d}" for index in range(len(rank_values))],
            "session_date": session_date,
            "opening_rvol": rank_values,
            "opportunity": opportunities,
            "vwap_side_frac": 0.75,
        }
    )


def test_ranks_within_date() -> None:
    """Pooled ranking would fill decile 1 with the low-scale date; per-date does not."""
    days = session_dates(2)
    opportunities = [float(rank) for rank in range(1, 11)]
    metrics = pd.concat(
        [
            metrics_frame(days[0], [float(value) for value in range(1, 11)], opportunities),
            metrics_frame(days[1], [float(value) for value in range(101, 111)], opportunities),
        ],
        ignore_index=True,
    )

    table = decile_table(metrics, "opening_rvol")

    assert table["n"].tolist() == [2] * 10
    # Per-date: decile k holds rank k from both dates, so its mean opportunity is k.
    # Pooled: decile 1 would be the first date's two weakest rows, mean 1.5.
    assert table["mean_opportunity"].tolist() == opportunities


def test_decile_counts() -> None:
    day = session_dates(1)[0]
    values = [float(value) for value in range(100)]
    metrics = metrics_frame(day, values, values)

    table = decile_table(metrics, "opening_rvol")

    assert table["decile"].tolist() == list(range(1, 11))
    assert table["n"].tolist() == [10] * 10


def test_ties() -> None:
    day = session_dates(1)[0]
    values = [5.0] * 20
    metrics = metrics_frame(day, values, [float(index) for index in range(20)])

    table = decile_table(metrics, "opening_rvol")

    assert table["decile"].tolist() == list(range(1, 11))
    assert table["n"].tolist() == [2] * 10


def test_reports_vwap_side_frac() -> None:
    day = session_dates(1)[0]
    values = [float(value) for value in range(10)]
    metrics = metrics_frame(day, values, values)

    table = decile_table(metrics, "opening_rvol")

    assert list(table.columns) == [
        "decile",
        "n",
        "mean_opportunity",
        "median_opportunity",
        "mean_vwap_side_frac",
    ]
    assert table["mean_vwap_side_frac"].tolist() == [0.75] * 10
