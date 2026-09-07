# CLAUDE.md

## Repo

`kite-auto-sdk` — async Python SDK for Zerodha Kite Connect. Auth (Playwright + TOTP), token lifecycle, REST, WebSocket, resilience primitives. Python 3.12+, Poetry, strict mypy, ruff, pytest with a 90% coverage gate on `kite_auto`.

Read `docs/SDK_GUIDE.md` before building anything on top of it. It documents the real surface and the real gaps.

## Current work: the scanner validation test

We're building a **new sibling package `scanner/`** that consumes the SDK. It answers one question:

> On NSE, does relative volume in the first 5 minutes (09:15–09:20 IST) rank stocks by how much they move for the rest of the day?

Output is **one table**, ten rows. If it shows the effect we build a real scanner later. If not, we've saved months. Spec is in `scanner_docs/SPEC.md`.

## Hard rules

1. **Never modify `kite_auto/`.** The scanner is a consumer. If you hit a missing SDK method, stop and report it — do not patch the SDK mid-task.
2. **Never break the existing test suite or the 90% gate on `kite_auto`.**
3. **No feature creep.** No CLI framework, no dashboards, no live polling, no order placement, no backtest engine. If SPEC.md doesn't ask for it, don't write it.
4. **Tests first.** Write the test from `scanner_docs/TESTS.md`, watch it fail, then write code.
5. **One step per session.** Build the step named in the prompt, then stop.
6. **Never print a DataFrame.** `df.shape` or one value. Loading data into the conversation burns tokens for nothing.
7. **Ask before adding a dependency.** `pandas` and `pyarrow` are the only new ones planned.

## ⚠️ Live-call safety

`.env` holds **real broker credentials**. Automated login is a real Zerodha login and **the account locks after a few failed 2FA attempts**.

- Never run `test.py` or any live login as a casual check.
- The scanner test suite must be **fully mocked** — no network, ever.
- Exactly two live operations are planned: the volume-semantics check (Step 0) and the one real 12-month fetch. Both are run by the user deliberately, not by you.
- If a task seems to need a live call, stop and ask.

## Conventions (match the existing repo)

- **async/await throughout.** `pytest.ini` has `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed.
- ruff, line-length 100, target py312. Run `poetry run ruff check .`
- mypy strict. Run `poetry run mypy kite_auto scanner`
- Type hints on everything. Dataclasses `frozen=True, slots=True` where the SDK does.
- Functions take and return DataFrames. No hidden state, no globals.
- Comments explain *why*, never *what*.
- Constants in `scanner/config.py`. No magic numbers in logic.

## Layout being added

```
scanner/
  config.py       constants only
  fetch.py        chunking + RateLimiter injection + Candle -> DataFrame
  universe.py     symbol -> instrument_token index (SDK_GUIDE §6.2: app's job)
  clean.py        the 4 cleaning rules
  features.py     RVOL, ADR%, gap   (09:20 knowledge only)
  metrics.py      opportunity       (post-09:20 outcome)
  analyze.py      decile table
run_scanner.py    thin CLI: fetch | build | analyze
tests/scanner/    mirrors scanner/, tiny synthetic fixtures
data/             gitignored
```

## Non-negotiables — how this fails silently

- **No lookahead.** Nothing computed at 09:20 may read a bar timestamped after 09:19. `test_no_lookahead` enforces it. Never weaken, skip, or xfail that test.
- **Volume semantics.** Kite's bar `volume` must be per-bar, not cumulative. Verified once in Step 0. Until then nothing downstream is trustworthy.
- **Rate limiting is opt-in.** `RestClient` does **not** apply a `RateLimiter` unless you inject one. Every fetch path must inject `RateLimiter(rate=3.0)`.
- **Fail loudly.** No `except: pass`. No `fillna()` to hide a problem. `Candle` fields are all `| None` because the SDK tolerates malformed rows — the scanner must assert non-null and raise.

## Definition of done

Tests pass, ruff clean, mypy clean, the `run_scanner.py` step runs end to end, and nothing outside the step's module changed.
