"""Thin CLI for the scanner validation test: fetch | build | analyze."""

import asyncio
import sys
from datetime import date, timedelta

import pandas as pd

from scanner.analyze import decile_table
from scanner.clean import clean
from scanner.config import FEATURES_FILE, MAX_DROP_FRACTION, RAW_DIR
from scanner.features import build_features
from scanner.fetch import fetch_universe, make_rate_limited_client
from scanner.metrics import build_metrics
from scanner.universe import build_universe

FETCH_WINDOW = timedelta(days=365)

RANKINGS = (
    ("opening_rvol", "the hypothesis"),
    ("or5_vol", "raw opening volume — if this ranks as well, the idea is dead"),
    ("abs_ret_0920", "top movers at 09:20 — the free list you'd otherwise use"),
)


async def run_fetch() -> None:
    """The one real 12-month fetch. ~1,200 requests at 3/sec, about 7 minutes."""
    end = date.today()
    start = end - FETCH_WINDOW
    async with make_rate_limited_client() as client:
        await client.login()
        universe = await build_universe(client)
        print(f"universe: {len(universe)} symbols, {start} to {end}")
        await fetch_universe(client, universe, start, end)


def run_build() -> None:
    bars = load_raw_bars()
    cleaned, dropped = clean(bars)
    report_drops(bars, cleaned, dropped)

    features = build_features(cleaned)
    metrics = build_metrics(cleaned, features)
    FEATURES_FILE.parent.mkdir(parents=True, exist_ok=True)
    metrics.to_parquet(FEATURES_FILE, index=False)
    print(f"features:      {len(metrics)} stock-days -> {FEATURES_FILE}")


def run_analyze() -> None:
    features = pd.read_parquet(FEATURES_FILE)
    features["abs_ret_0920"] = features["ret_0920"].abs()
    for rank_col, note in RANKINGS:
        print(f"\nranked by {rank_col} — {note}")
        print(decile_table(features, rank_col).to_string(index=False))


def load_raw_bars() -> pd.DataFrame:
    files = sorted(RAW_DIR.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"{RAW_DIR} holds no bars — run `fetch` first.")
    frames = []
    for path in files:
        frame = pd.read_parquet(path)
        frame["tradingsymbol"] = path.stem
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def report_drops(bars: pd.DataFrame, cleaned: pd.DataFrame, dropped: dict[str, int]) -> None:
    for rule, rows in dropped.items():
        print(f"  {rule:<15} {rows:>9,} bars dropped")
    print(f"  stock-days     {stock_days(bars):>9,} -> {stock_days(cleaned):,}")

    # Warmup removes a deliberate constant — 20 sessions per symbol — so including
    # it would trip the threshold on a perfectly healthy fetch. Only the three
    # data-quality rules say anything about whether the fetch is broken.
    quality_bars = sum(rows for rule, rows in dropped.items() if rule != "warmup")
    removed = quality_bars / len(bars) if len(bars) else 0.0
    if removed > MAX_DROP_FRACTION:
        print(
            f"  WARNING: the session/short-day/split rules removed {removed:.1%} of bars. "
            f"Above {MAX_DROP_FRACTION:.0%} usually means the fetch is broken, not the market."
        )


def stock_days(bars: pd.DataFrame) -> int:
    sessions = pd.DataFrame(
        {
            "tradingsymbol": bars["tradingsymbol"],
            "session_date": bars["timestamp"].dt.date,
        }
    )
    return len(sessions.drop_duplicates())


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "fetch":
        asyncio.run(run_fetch())
    elif command == "build":
        run_build()
    elif command == "analyze":
        run_analyze()
    else:
        print("usage: python run_scanner.py fetch|build|analyze", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
