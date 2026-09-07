# Kite Auto SDK

Production-oriented Python SDK scaffold for abstracting Zerodha Kite Connect
authentication, token lifecycle management, session handling, streaming, and trading APIs.

> Automated login can violate broker or exchange rules. This project is designed to keep
> manual and automated authentication strategies pluggable so users can choose a compliant
> flow for their environment.

## Status

This repository currently implements:

- **Prompt 1:** project scaffolding
- **Prompt 2:** centralized configuration management with `pydantic-settings`
- **Prompt 3:** Playwright-based Zerodha login automation returning `request_token`
- **Prompt 4:** access-token exchange with SHA256 checksum, `httpx`, typed parsing, retries
- **Prompt 5:** JSON session persistence, expiry detection, automatic reauthentication
- **Prompt 6:** high-level `KiteClient` with automatic login, token reuse, and typed REST methods
- **Prompt 7:** WebSocket streaming with subscriptions, reconnects, tick parsing, shutdown
- **Prompt 8:** retry/backoff, circuit breaker, rate limiting, duplicate-order guard
- **Prompt 9:** comprehensive async/API/Playwright-mock tests with coverage gate >90%

Production documentation and Docker support are implemented in the final roadmap phase.

## Requirements

- Python 3.12+
- Poetry

## Setup

```bash
poetry install
cp .env.example .env
poetry run pytest
poetry run ruff check .
poetry run mypy kite_auto
```

Playwright browser binaries will be required in later phases:

```bash
poetry run playwright install chromium
```

## Package Layout

```text
kite_auto/
  auth/          # Auth strategy contracts and future implementations
  client/        # Public KiteClient plus REST/WebSocket transports
  config/        # Pydantic settings loader
  exceptions/    # SDK exception hierarchy
  models/        # Typed domain models
  utils/         # Logging, retry, and rate-limit utilities
tests/
examples/
docs/
```

## Intended Usage

The final SDK should expose a simple async interface:

```python
from kite_auto import KiteClient

client = KiteClient()

access_token = await client.login()
positions = await client.positions()
quote = await client.quote("NSE:RELIANCE")
orders = await client.orders()
```

Order placement is available with SDK-side duplicate submission protection.

## Development

```bash
poetry run ruff check .
poetry run mypy kite_auto tests
poetry run pytest --cov
```

Coverage is gated at 90% through `pyproject.toml`.

## Configuration

Settings are loaded from environment variables or a local `.env` file:

```bash
cp .env.example .env
```

The singleton loader is available through `kite_auto.config.get_settings()` and returns a cached
`Settings` instance. Blank secret values in `.env` are treated as unset so the package can import
cleanly in development and CI.

## Playwright Login

`PlaywrightAuthStrategy` automates the Kite Connect login page with Chromium, generates a TOTP,
waits for the registered redirect URL, and returns only the `request_token`.

```python
from kite_auto.auth import PlaywrightAuthStrategy

auth = PlaywrightAuthStrategy()
request_token = await auth.login()
```

Required environment variables:

- `KITE_API_KEY`
- `KITE_USER_ID`
- `KITE_PASSWORD`
- `KITE_TOTP_SECRET`

The selector bundle is injectable through `PlaywrightLoginSelectors` so future Kite form markup
changes can be handled without reshaping the auth strategy.

## Token Exchange

`TokenExchangeClient` exchanges a Kite `request_token` for an `AccessTokenResponse` using the
official checksum formula: `SHA256(api_key + request_token + api_secret)`.

```python
from kite_auto.auth import TokenExchangeClient

exchange = TokenExchangeClient()
session = await exchange.exchange_request_token(request_token)
access_token = session.access_token
```

Transient transport errors and retryable HTTP responses are retried with exponential backoff.
Permanent Kite errors raise `TokenExchangeError`.

## Session Persistence

`JsonSessionStore` persists `KiteSession` data to `~/.kite_auto/session.json` by default using
owner-only file permissions. `TokenManager.get_access_token()` reuses a valid stored token and
reauthenticates through the configured auth strategy when the session is missing, expired, or
force-refreshed.

```python
from kite_auto.auth import PlaywrightAuthStrategy, TokenManager

manager = TokenManager(auth_strategy=PlaywrightAuthStrategy())
access_token = await manager.get_access_token()
```

## High-Level Client

`KiteClient` composes authentication, session reuse, token exchange, and authenticated REST calls.
REST calls automatically request an access token from `TokenManager`; if Kite returns an auth
failure, the transport refreshes once and retries the request.

Implemented methods:

- `login(force_refresh=False) -> str`
- `positions() -> PositionsResponse`
- `quote(instrument) -> Quote`
- `quote_many(instruments) -> dict[str, Quote]`
- `orders() -> tuple[Order, ...]`
- `place_order(variety="regular", **order) -> Order`

## Resilience

The SDK includes reusable resilience primitives:

- `AsyncRetryPolicy` with exponential backoff
- `CircuitBreaker`
- `RateLimiter`
- `DuplicateOrderGuard`

`RestClient` can apply retry, rate-limit, and circuit-breaker policies. Retries are conservative:
idempotent REST methods are retryable by default, while order placement is guarded by a stable
payload fingerprint to prevent duplicate submissions inside a configurable time window.

```python
order = await client.place_order(
    exchange="NSE",
    tradingsymbol="INFY",
    transaction_type="BUY",
    quantity=1,
    product="CNC",
    order_type="MARKET",
)
```

## WebSocket Streaming

`WebSocketClient` connects to Kite's streaming endpoint, replays subscriptions after reconnects,
parses binary tick packets, and ignores heartbeat frames.

```python
from kite_auto import KiteClient
from kite_auto.client import WebSocketMode

async def on_ticks(ticks):
    for tick in ticks:
        print(tick.instrument_token, tick.last_price)

client = KiteClient()
await client.stream([408065], mode=WebSocketMode.FULL, on_ticks=on_ticks)
```

Lower-level usage is also available:

```python
ws = client.websocket()
await ws.subscribe([408065], mode="quote")
await ws.listen(on_ticks=on_ticks)
```

## License

MIT
