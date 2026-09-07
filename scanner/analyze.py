"""Step 7 — the decile table. One table, ten rows, read the numbers."""

import pandas as pd

from scanner.config import DECILES

TABLE_COLUMNS = [
    "decile",
    "n",
    "mean_opportunity",
    "median_opportunity",
    "mean_vwap_side_frac",
]


def decile_table(features: pd.DataFrame, rank_col: str) -> pd.DataFrame:
    """Rank by ``rank_col`` within each session_date, cut into deciles, pool across dates.

    Ranking within date is the whole point. Pooled ranks measure which months were
    volatile, not whether the ranking column predicts anything.
    """
    columns = [rank_col, "opportunity", "vwap_side_frac"]
    frame = features[["session_date", *columns]]

    gaps = [column for column in columns if bool(frame[column].isna().any())]
    if gaps:
        raise ValueError(f"decile_table cannot rank with missing values in {', '.join(gaps)}")

    return (
        frame.assign(decile=_deciles(frame, rank_col))
        .groupby("decile", sort=True)
        .agg(
            n=("opportunity", "size"),
            mean_opportunity=("opportunity", "mean"),
            median_opportunity=("opportunity", "median"),
            mean_vwap_side_frac=("vwap_side_frac", "mean"),
        )
        .reset_index()[TABLE_COLUMNS]
    )


def _deciles(frame: pd.DataFrame, rank_col: str) -> pd.Series:
    """Decile 1-10 of each row within its own session_date.

    ``method="first"`` breaks ties by position rather than collapsing them into a
    shared rank, so a day of identical values still spreads across all ten deciles.
    Integer arithmetic throughout — a float percentile lands rows on the wrong side
    of a boundary often enough to unbalance the counts.
    """
    per_date = frame.groupby("session_date", sort=False)[rank_col]
    ordinal = per_date.rank(method="first").astype("int64")
    session_size = per_date.transform("size").astype("int64")
    return (ordinal * DECILES - 1) // session_size + 1
