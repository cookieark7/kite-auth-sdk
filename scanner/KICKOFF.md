# KICKOFF.md — running the build

## Setup, once

```bash
cd kite-auth-sdk
poetry add pandas pyarrow          # the only new deps
poetry run pytest                  # existing suite must be green before you start
```

Create `data/universe_symbols.txt` — NSE F&O list + ~80 liquid cash names, one symbol per line, ~300 lines.

`.env` already has your credentials. `CLAUDE.md` at the repo root loads automatically.

---

## The pattern

**One step per session. `/clear` between every one.**

Biggest token lever you have. Without it, session 7 carries the transcript of 1–6 on every message. With it, each session starts at roughly the size of the three MD files.

Paste, filling in the step:

```
Read scanner_docs/SPEC.md and scanner_docs/TESTS.md.

Build Step <N> only: <module>.

Order:
1. Write the tests from TESTS.md for this module. Run them. They must fail.
2. Write the minimum code to pass them.
3. Run: poetry run pytest tests/scanner && poetry run ruff check . && poetry run mypy kite_auto scanner
4. Show me the diff summary and stop.

Do not modify kite_auto/. Do not build other steps.
Do not add anything SPEC.md doesn't ask for.
```

Then read the diff, `/clear`, next step.

---

## Order

| Session | Step | Note |
|---|---|---|
| 0 | `verify_volume.py` | **You run this, live, once.** Can end the project. |
| 1+2 | `config.py` + `fetch.py` | config is trivial, do both together |
| 3 | `universe.py` | then run the real fetch in the background (~7 min) |
| 4 | `clean.py` | T3 lives here |
| 5 | `features.py` | **T2 lives here — the important one** |
| 6 | `metrics.py` | |
| 7 | `analyze.py` | |
| — | `fetch` → `build` → `analyze` | read the table |

---

## ⚠️ Live calls

Exactly two in the whole project, both run by you:

1. **Step 0** — one day of RELIANCE bars, to check volume semantics.
2. **The 12-month fetch** — ~1,200 requests at 3/sec, about 7 minutes.

Everything else is mocked. Zerodha locks the account after a few failed 2FA attempts, so never re-run a failed login reflexively — read the `DIAG[...]` output first. Never run `test.py` as a casual check.

---

## Which model

**Sonnet for all of it.** ~600 lines of pandas plus an adapter over an SDK that already exists, against a spec that's already written. Opus buys nothing here and costs several times more. The hard thinking — what to measure, what invalidates it — is done and sitting in the MD files.

Opus only if the final table comes back ambiguous and you need help reasoning about *why*. That's a thinking problem, not a coding one.

Haiku is fine for renames, docstrings, formatting.

---

## Token discipline

Ranked by actual impact:

1. **`/clear` between steps.** Dominates everything else.
2. **Never print a DataFrame.** `df.head()` on a wide frame is thousands of tokens of nothing.
3. **Don't paste raw tracebacks.** The relevant line plus the function, not 200 lines of pandas internals.
4. **Don't ask it to re-read files it wrote this session.**
5. **Let it run its own tests** — one `pytest` call beats a paste-and-diagnose round trip.
6. **Resist "while you're here, could you also…"** — how a 600-line project becomes 3,000.

---

## Stop and think if

- **Step 0 says cumulative volume** → stop. Nothing downstream works.
- **T2 (lookahead) fails and the fix isn't obvious** → stop. Don't relax the test.
- **Cleaning drops >5% of stock-days** → the fetch is probably broken, not the market.
- **Claude wants to modify `kite_auto/`** → stop. Report the gap, decide separately.
- **The table is ambiguous** → one retry (09:15–09:30), then stop.

---

## After the table exists

Write three lines before deciding anything: what the numbers were, pass or fail against SPEC.md, what you'd do next.

Then decide whether to build the scanner. Not before.
