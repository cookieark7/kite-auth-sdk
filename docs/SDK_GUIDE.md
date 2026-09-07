# Kite Auto SDK — Capabilities, Data Shapes & Integration Guide

> Status reference for building on top of this SDK (data source → scanner →
> backtester → realtime execution). Everything below is grounded in the current
> code, with a clear split between **what exists today** and **what you must add**
> for the roadmap.

---

## 1. What the SDK offers today

| Layer | Component | What it does |
|---|---|---|
| **Auth** | `PlaywrightAuthStrategy` | Automated browser login → `request_token`. Hardened: TOTP freshness gate, no-replay capped retry, host clock-drift check. |
| | `ManualAuthStrategy` | Pluggable manual flow (scaffold) for compliant/manual token entry. |
| | `TokenExchangeClient` | `request_token` → `access_token` via SHA256 checksum, retries on transient errors. |
| | `TokenManager` | Reuses a valid token, detects expiry, reauthenticates automatically. |
| | `JsonSessionStore` | Persists session to `~/.kite_auto/session.json` (owner-only perms). |
| **REST** | `KiteClient` | High-level entry point (see §3). Auto-auth + refresh-once-on-401. |
| | `RestClient` | Low-level transport with retry + rate-limit + circuit-breaker hooks. |
| **Streaming** | `WebSocketClient` | Subscribe (LTP/QUOTE/FULL), binary tick parsing, auto-reconnect + resubscribe, heartbeat handling. |
| **Resilience** | `AsyncRetryPolicy`, `CircuitBreaker`, `RateLimiter`, `DuplicateOrderGuard` | Reusable primitives; order placement is fingerprint-guarded against duplicates. |
| **Config** | `Settings` / `get_settings()` | `pydantic-settings`, `.env`-driven, cached singleton. |

### Public `KiteClient` methods (the whole REST surface today)

```python
await client.login(force_refresh=False) -> str
await client.positions()                -> PositionsResponse
await client.quote(instrument)          -> Quote
await client.quote_many([instruments])  -> dict[str, Quote]
await client.orders()                   -> tuple[Order, ...]
await client.place_order(variety="regular", **order) -> Order
await client.historical_data(token, interval, from_=, to=, continuous=, oi=) -> tuple[Candle, ...]
await client.instruments(exchange=None)  -> tuple[Instrument, ...]   # parses /instruments CSV
client.websocket()                      -> WebSocketClient
await client.stream(tokens, mode=..., on_ticks=...) -> None
```

That is the **entire** implemented REST API. Note what is **not** here yet — see §6.

---

## 2. Sample data shapes

These mirror the dataclasses in `kite_auto/models/`. Every model also carries a
`raw` field with the untouched Kite payload, so nothing is lost in parsing.

### Quote (`client.quote("NSE:RELIANCE")`)
```python
Quote(
    instrument="NSE:RELIANCE",
    instrument_token=738561,
    timestamp=datetime(2026, 6, 26, 15, 25, 0),
    last_trade_time=datetime(2026, 6, 26, 15, 24, 59),
    last_price=2987.4,
    last_quantity=5,
    buy_quantity=312044,
    sell_quantity=289110,
    volume=4821233,
    average_price=2980.7,
    oi=0.0,
    net_change=12.35,
    lower_circuit_limit=2688.7,
    upper_circuit_limit=3286.1,
    ohlc=OHLC(open=2975.0, high=2992.8, low=2968.1, close=2975.05),
    raw={...},  # full Kite dict
)
```

### Position / PositionsResponse (`client.positions()`)
```python
PositionsResponse(
    net=(Position(tradingsymbol="INFY", exchange="NSE", product="CNC",
                  quantity=10, average_price=1450.2, last_price=1467.8,
                  pnl=176.0, m2m=176.0, unrealised=176.0, realised=0.0, ...),),
    day=( ... ),
    raw={...},
)
```

### Order (`client.orders()` / `client.place_order(...)`)
```python
Order(
    order_id="240626000000001",
    tradingsymbol="INFY", exchange="NSE", status="COMPLETE",
    transaction_type="BUY", order_type="MARKET", product="CNC",
    quantity=1, filled_quantity=1, pending_quantity=0,
    price=0.0, average_price=1467.8,
    order_timestamp=datetime(2026, 6, 26, 9, 20, 11),
    raw={...},
)
```

### Tick (streaming `on_ticks` callback)
```python
Tick(
    instrument_token=738561,
    mode=WebSocketMode.FULL,      # ltp | quote | full
    last_price=2987.4,
    last_traded_quantity=5,
    average_traded_price=2980.7,
    volume_traded=4821233,
    total_buy_quantity=312044,
    total_sell_quantity=289110,
    ohlc=OHLC(open=2975.0, high=2992.8, low=2968.1, close=2975.05),
    change=0.41,
    last_trade_time=datetime(...),
    exchange_timestamp=datetime(...),
    oi=0, oi_day_high=0, oi_day_low=0,
    depth=MarketDepth(
        buy=(DepthEntry(quantity=50, price=2987.3, orders=3), ...),   # 5 levels
        sell=(DepthEntry(quantity=40, price=2987.5, orders=2), ...),  # 5 levels
    ),
    raw=b"...",  # original binary packet
)
```
Packet→mode mapping in `parse_binary_ticks`: 8 bytes → LTP, 32/44 → QUOTE,
184 → FULL (with depth). Prices are paise→rupees divided by `DEFAULT_PRICE_DIVISOR`.

---

## 3. Usage guide

### Minimal
```python
import asyncio
from kite_auto import KiteClient

async def main():
    async with KiteClient() as client:          # context-manages cleanup
        await client.login()                      # reuses session or re-auths
        print(await client.quote("NSE:RELIANCE"))
        print(await client.positions())

asyncio.run(main())
```

### Streaming
```python
from kite_auto.client import WebSocketMode

async def on_ticks(ticks):
    for t in ticks:
        print(t.instrument_token, t.last_price)

async with KiteClient() as client:
    await client.stream([738561, 408065], mode=WebSocketMode.FULL, on_ticks=on_ticks)
```

### Placing an order (duplicate-guarded)
```python
order = await client.place_order(
    exchange="NSE", tradingsymbol="INFY", transaction_type="BUY",
    quantity=1, product="CNC", order_type="MARKET",
)
```

### Dependency injection (key for testing + your tooling)
`KiteClient` accepts injected `auth_strategy`, `token_manager`, `rest_client`,
`websocket_client`, `session_store`, `exchange_client`, `order_guard`. This is the
seam you'll use to plug in mocks, a paper-trading transport, or a recorded data
source without touching the SDK internals.

---

## 4. How to test it further

1. **Mocked suite (safe, fast):** `poetry run pytest --cov` — 95 tests, isolated
   from `.env` via `tests/conftest.py`, never touches the network. This is your
   primary regression gate. Add cases here as you extend.
2. **Lint/types:** `poetry run ruff check .` and `poetry run mypy kite_auto tests`.
3. **One careful live smoke test:** `HEADLESS=false poetry run python test.py`.
   ⚠️ This is a **real broker login** — Zerodha locks the account after a few
   failed 2FA attempts. Run it deliberately, once, and read the `DIAG[...]` output
   instead of re-running on failure.
4. **Record/replay fixtures:** capture real `quote`/`positions`/`orders` JSON
   (the `raw` field) once, save as fixtures, and build offline tests against the
   `*.from_mapping()` parsers — gives you broad coverage with zero live calls.
5. **Paper-trading transport:** implement `RestTransportProtocol` (4 methods) that
   simulates fills, inject it into `KiteClient(rest_client=...)`. Lets you test the
   execution tool end-to-end without placing real orders.

---

## 5. Integration guide for your roadmap

The SDK is the **auth + transport + streaming substrate**. Build each tool as a
layer that consumes it through the injectable seams.

```
┌──────────────────────────────────────────────────────────┐
│  Your apps:  Scanner   Backtester   Realtime Execution     │
├──────────────────────────────────────────────────────────┤
│  Your layer: DataSource (cache/normalize)  •  Strategy API │
├──────────────────────────────────────────────────────────┤
│  kite-auto-sdk:  KiteClient (REST) │ WebSocketClient (ticks)│
│                  TokenManager • Resilience • Models         │
└──────────────────────────────────────────────────────────┘
```

- **Data source:** wrap `quote_many` + `stream`. Add a thin cache and a
  symbol↔token map (you must build this — see §6). Normalize `Quote`/`Tick`
  `raw` into your own columnar/event schema once, at this boundary.
- **Scanner:** consume the `on_ticks` stream (live) and/or poll `quote_many`
  (snapshot). Run your filters on the normalized events. The `RateLimiter` and
  `CircuitBreaker` primitives protect your polling loops.
- **Backtester:** needs historical candles — **not provided yet** (§6.1). Once you
  add a `historical_data()` REST method, feed the same normalized schema into the
  backtester so live and backtest paths share one strategy interface.
- **Realtime execution:** drive `place_order` from strategy signals. You'll want
  to add `modify`/`cancel`/`holdings`/`margins` (§6). Keep `DuplicateOrderGuard`
  in the path; consider a paper transport behind a feature flag.

**Golden rule:** define **one** internal event/candle/order schema and convert at
the SDK boundary. Then live (`Tick`), snapshot (`Quote`), and backtest (candles)
all feed the same strategy code.

---

## 6. Gaps you'll need to fill (important)

The current REST surface is intentionally small. For your roadmap you will need to
add these (all are standard Kite Connect endpoints; the `RestClient` already gives
you authenticated `get`/`post`):

1. ~~Historical candle data~~ — **DONE.** `client.historical_data(token, interval,
   from_, to, continuous, oi)` returns `tuple[Candle, ...]`. Intervals via
   `CandleInterval`. Candle: `timestamp, open, high, low, close, volume, oi, raw`.
   ```python
   from kite_auto.models import CandleInterval
   candles = await client.historical_data(
       738561, CandleInterval.MINUTE_5,
       from_=datetime(2026, 6, 26, 9, 15), to=datetime(2026, 6, 26, 15, 30),
   )
   ```
2. ~~Instrument master~~ — **DONE (fetch only).** `client.instruments(exchange=None)`
   fetches + parses the `/instruments` CSV into `tuple[Instrument, ...]`. Building
   the symbol↔token **index, caching, and daily refresh is your app's job**, not
   the SDK's — the SDK just returns parsed rows.
3. **Order lifecycle** — `modify` (`PUT /orders/{variety}/{id}`),
   `cancel` (`DELETE`), plus `holdings`, `margins`, `trades` — for execution.
4. **Lightweight endpoints** — `/quote/ltp`, `/quote/ohlc` — cheaper polling than
   full `/quote` when scanning many symbols.
5. **GTT / SL-management** — if your execution tool needs server-side stops.

Each is a small addition following the existing `positions()`/`orders()` pattern
(call `self._rest_client.get/post`, parse into a model). I can scaffold any of
these with tests when you're ready.

---

## 7. Safety notes
- `.env` holds **live** credentials; automated login can violate broker terms and
  risks account lockout. Keep a compliant flow and rotate any exposed secret.
- Order placement is real money. Gate the execution tool behind explicit config
  and a paper-trading default.
