# SPEC.md — scanner validation test

Consumes `kite_auto`. Never modifies it. Seven steps, in order, one per session.

## What the SDK already gives you

```python
from kite_auto import KiteClient
from kite_auto.models import CandleInterval
from kite_auto.models.historical import Candle
from kite_auto.models.instruments import Instrument

async with KiteClient() as client:
    await client.login()                          # session reuse + auto re-auth
    candles = await client.historical_data(       # -> tuple[Candle, ...]
        instrument_token, CandleInterval.MINUTE_5,
        from_=datetime(...), to=datetime(...),
    )
    instruments = await client.instruments("NSE") # -> tuple[Instrument, ...]
```

`Candle`: `timestamp, open, high, low, close, volume, oi, raw` — **every field is `| None`** because the SDK tolerates malformed rows.

## What the SDK does NOT do — this is your work

| Gap | Consequence |
|---|---|
| `historical_data` does **not chunk** | 5-minute data caps at **100 days per request**. 12 months = 4 chunks per symbol. You chunk. |
| `RestClient` does **not rate-limit by default** | `rate_limiter` is an optional constructor injection. **You must pass `RateLimiter(rate=3.0)`** or you will hammer the API. |
| No symbol↔token index | `instruments()` returns parsed rows. Indexing/caching is the app's job (SDK_GUIDE §6.2). |
| No `/quote/ltp`, no `/quote/ohlc` | Not needed here — this test is historical only. Don't add them. |

---

## Step 0 — `verify_volume.py` (standalone, throwaway, ~40 lines)

**Before anything else. It can end the project.**

One live call. Fetch `5minute` bars for RELIANCE for one recent trading day. Sum `volume` across bars, compare to the `day` candle's volume for the same date.

- Sum ≈ day total → **per-bar**. Correct. Proceed.
- Last bar ≈ day total → **cumulative**. Every RVOL number downstream is meaningless. **Stop and report.**

Print both numbers and the verdict. Delete the file afterwards.

> This is one of only two live operations in the whole project. The user runs it.

---

## Step 1 — `scanner/config.py`

Constants only. No logic.

```python
SESSION_START   = time(9, 15)
SESSION_END     = time(15, 30)
OPEN_WINDOW_END = time(9, 20)    # close of the first 5-min bar
EVAL_START      = time(9, 20)
EVAL_END        = time(15, 15)

INTERVAL         = CandleInterval.MINUTE_5
BARS_PER_DAY     = 75
MIN_BARS_PER_DAY = 70

RVOL_LOOKBACK   = 14
ADR_LOOKBACK    = 20
WARMUP_SESSIONS = 20
MIN_PRIOR_SESSIONS = 10

SPLIT_GAP_THRESHOLD  = 0.20
MAX_DAYS_PER_REQUEST = 100      # Kite cap for 5minute
RATE_LIMIT_PER_SEC   = 3.0      # historical endpoint

IST      = ZoneInfo("Asia/Kolkata")
DATA_DIR = Path("data")
```

---

## Step 2 — `scanner/fetch.py`

```python
def make_rate_limited_client() -> KiteClient:
    """KiteClient whose RestClient has RateLimiter(rate=RATE_LIMIT_PER_SEC)
    injected. The SDK does NOT do this by default."""

def candles_to_frame(candles: tuple[Candle, ...]) -> pd.DataFrame:
    """-> [timestamp, open, high, low, close, volume], tz-aware IST.
    Raises if any required field is None — Candle tolerates malformed rows,
    the scanner must not."""

def date_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Split into spans of at most MAX_DAYS_PER_REQUEST days."""

async def fetch_symbol(client, token: int, start: date, end: date) -> pd.DataFrame:
    """All chunks for one symbol, concatenated, sorted by timestamp,
    duplicate timestamps dropped at chunk boundaries."""

async def fetch_universe(client, universe: pd.DataFrame,
                         start: date, end: date) -> None:
    """Writes data/raw/<tradingsymbol>.parquet per symbol.
    Skips symbols already written (resume). Sequential — the rate limiter
    makes concurrency pointless at 3 req/sec."""
```

Retry on transient network errors only, via the SDK's `AsyncRetryPolicy`. Auth errors must raise immediately.

---

## Step 3 — `scanner/universe.py`

```python
async def build_universe(client) -> pd.DataFrame:
    """-> [tradingsymbol, instrument_token]

    Reads data/universe_symbols.txt (one symbol per line).
    Calls client.instruments("NSE"), filters to
    instrument_type == "EQ" and segment == "NSE", builds the index.
    Raises listing every symbol that failed to resolve — never silently drop.
    Caches to data/universe.parquet."""
```

`universe_symbols.txt` is user-supplied: NSE F&O list + ~80 liquid cash names, ~300 lines. Raise clearly if missing.

---

## Step 4 — `scanner/clean.py`

```python
def clean(bars: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
```

Four rules, in order, returning rows dropped per rule:

1. Keep bars where `SESSION_START <= time < SESSION_END`
2. Drop whole stock-days with `< MIN_BARS_PER_DAY` bars
3. Drop stock-days where `|first_bar_open / prev_close - 1| > SPLIT_GAP_THRESHOLD`, **plus the 2 following sessions for that symbol**
4. Drop the first `WARMUP_SESSIONS` sessions per symbol

`run_scanner.py` prints the counts. Warn if total drops exceed 5% of stock-days — that usually means the fetch is broken, not the market.

---

## Step 5 — `scanner/features.py`

**Every value uses only bars timestamped ≤ 09:19.** The lookahead test lives here.

```python
def build_features(bars: pd.DataFrame) -> pd.DataFrame:
    """One row per (tradingsymbol, session_date):
      or5_vol       volume of the 09:15 bar
      opening_rvol  or5_vol / mean(or5_vol over prior RVOL_LOOKBACK sessions)
      adr_pct       mean of (high-low)/close over prior ADR_LOOKBACK sessions
      gap_pct       (open_0915 - prev_close) / prev_close
      gap_in_adr    |gap_pct| / adr_pct
      ret_0920      (close_0920 - prev_close) / prev_close
      close_0920
    """
```

- Trailing windows **exclude the current session**: `.shift(1)` before `.rolling()`, never after.
- Skip missing sessions; never forward-fill.
- Drop the stock-day if fewer than `MIN_PRIOR_SESSIONS` valid prior sessions.

---

## Step 6 — `scanner/metrics.py`

Outcomes. These *do* use post-09:20 data — that is their job.

```python
def build_metrics(bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """Adds:
      opportunity     max(up, down) / adr_pct        <- THE metric
      vwap_side_frac  fraction of eval bars on the dominant side of VWAP

    Over bars in [EVAL_START, EVAL_END]:
      up   = (max high - close_0920) / close_0920
      down = (close_0920 - min low)  / close_0920
    """
```

VWAP: cumulative from 09:15 (including the opening bar), typical price `(h+l+c)/3`, reset daily. Only *evaluated* over the eval window.

`vwap_side_frac` is a spare column for later. It decides nothing.

---

## Step 7 — `scanner/analyze.py`

```python
def decile_table(features: pd.DataFrame, rank_col: str) -> pd.DataFrame:
    """Rank by rank_col WITHIN each session_date, cut into deciles 1-10,
    pool across dates. One row per decile:
    [decile, n, mean_opportunity, median_opportunity, mean_vwap_side_frac]"""
```

Print three tables:

| rank_col | what it tests |
|---|---|
| `opening_rvol` | **the hypothesis** |
| `or5_vol` | raw opening volume — if this ranks as well, the normalisation is pointless and the idea is dead |
| `ret_0920` (abs) | "top movers at 09:20" — the free list you'd otherwise use |

**Rank within date, never pooled.** Pooling measures which months were volatile, not whether RVOL predicts anything.

No plots, no bootstrap, no significance tests. Read the numbers.

---

## Reading the result

**PASS** — D10 mean opportunity clearly above the D1–D5 mean, roughly monotonic by eye, beats both `or5_vol` and `|ret_0920|`.

**FAIL** — no monotonic pattern, or `or5_vol` ranks as well.

**Neither** — one retry: opening window becomes 09:15–09:30 (three 5-min bars). Re-run. That's it, not thirty variants.

---

## run_scanner.py

```
poetry run python run_scanner.py fetch     # universe -> data/raw/
poetry run python run_scanner.py build     # clean -> features -> metrics -> data/features.parquet
poetry run python run_scanner.py analyze   # three decile tables to stdout
```

Three subcommands off `sys.argv`, wrapped in `asyncio.run`. Not argparse, not click.
