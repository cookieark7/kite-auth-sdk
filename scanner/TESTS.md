# TESTS.md — write these before the code

## How

`poetry run pytest tests/scanner`. `asyncio_mode = "auto"` is already set — no `@pytest.mark.asyncio`.

Every test uses a **hand-built synthetic DataFrame**: 2 symbols × 5 sessions, ~10 bars each. Small enough to reason about by hand.

**No network, ever.** Mock `KiteClient` — it accepts injected `rest_client`, `token_manager`, `auth_strategy`, which is exactly the seam for this (SDK_GUIDE §3). `.env` holds live credentials and the account locks on failed 2FA. A scanner test that logs in is a bug.

Fixtures in `tests/scanner/conftest.py`, built from explicit numbers you chose. No random generators.

Don't touch `tests/` for `kite_auto` — that suite and its 90% gate must keep passing untouched.

---

## The three that matter

Everything else here is hygiene. These three catch failures that produce a *confidently wrong table* rather than a crash.

### T1 — Volume semantics (manual, live, once, before anything)

Step 0's `verify_volume.py`. Sum of 5-min bar volumes ≈ the `day` candle volume.

If volume is cumulative, every RVOL number is meaningless and the project stops. **The user runs this. Not you.**

### T2 — No lookahead · `tests/scanner/test_features.py::test_no_lookahead`

The most important automated test here.

```
Fixture where every bar after 09:19 has absurd values
(volume = 10**9, close = 10**6).

Compute features.

Assert identical to features computed on the same fixture with all
post-09:19 bars deleted.
```

Usual cause of failure: `.rolling()` without a preceding `.shift(1)`.

**Never relax, skip, or xfail this test.** If it fails, the bug is in the code.

### T3 — Corporate action removal · `test_clean.py::test_split_dropped`

```
Fixture: prev_close = 1000, next session opens at 200 (1:5 split).
Assert that session is dropped, and the 2 sessions after it.
```

Without this, split days sit at the top of the ranked list forever.

---

## The rest

### `test_fetch.py`

- `test_rate_limiter_injected` — the client built by `make_rate_limited_client()` has a non-None `RateLimiter` on its `RestClient`. **The SDK default is `None`; this test is what stops a silent regression to un-throttled fetching.**
- `test_date_chunks` — a 365-day range → 4 chunks, none over 100 days; a 30-day range → 1 chunk
- `test_candles_to_frame` — a `tuple[Candle, ...]` with known values → correct frame, tz-aware IST
- `test_candle_none_raises` — a `Candle` with `volume=None` raises, not silently coerced. The SDK tolerates malformed rows on purpose; the scanner must not.
- `test_chunk_boundary_dedup` — overlapping chunks produce no duplicate timestamps

### `test_universe.py`

- `test_resolves_tokens` — mocked `instruments("NSE")` → correct symbol→token map
- `test_filters_eq_nse` — rows with `instrument_type != "EQ"` or `segment != "NSE"` are excluded
- `test_unresolved_raises` — a symbol absent from the dump raises, and the message names it

### `test_clean.py`

- `test_session_mask` — bars at 09:07 and 15:45 removed
- `test_short_day_dropped` — 60 bars dropped whole; 74 survives
- `test_warmup_dropped` — first 20 sessions per symbol gone, 21st survives
- `test_drop_counts` — dict values sum to rows actually removed

### `test_features.py`

- `test_rvol_arithmetic` — 14 prior sessions at or5_vol=100, today=300 → `opening_rvol == 3.0`
- `test_rvol_excludes_today` — changing today's or5_vol must not move the denominator
- `test_insufficient_history` — 9 prior sessions → dropped; 10 → kept
- `test_adr_pct` — hand-computed over 20 known sessions
- `test_gap_in_adr` — gap 2%, adr_pct 1.5% → `1.333...`

### `test_metrics.py`

- `test_opportunity_up` — price only rises after 09:20 → `opportunity == up / adr_pct`
- `test_opportunity_down` — price only falls → the down leg wins. **Direction-agnostic: both must pass.**
- `test_eval_window` — a spike at 15:25 (after `EVAL_END`) is excluded
- `test_vwap_side_frac` — all bars above VWAP → `1.0`; alternating → `0.5`

### `test_analyze.py`

- `test_ranks_within_date` — two dates with different volume scales. Construct it so pooled ranking gives a visibly different answer, then assert you got the per-date one.
- `test_decile_counts` — 100 symbols × 1 date → 10 per decile
- `test_ties` — identical rank values don't crash or collapse into one decile

---

## Rules

- A test needing network, credentials, or >50 lines of fixture is the wrong test.
- Test the arithmetic, not pandas. No assertions on dtypes or index names.
- When a test fails, fix the code. Editing the assertion to match the output is how you ship a validated bug.
- Coverage targets aren't the goal. T1–T3 passing matters more than the other twenty combined.
