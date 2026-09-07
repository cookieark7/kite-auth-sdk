# Kite Auto SDK
## Production Architecture & Codex Build Blueprint

### Objective

Build a production-grade Python SDK that fully abstracts Zerodha Kite Connect authentication, session handling, token lifecycle management, WebSocket streaming, and trading APIs behind a clean reusable interface.

This document is intended for:
- Codex
- Cursor
- Claude Code
- GPT-based coding agents
- Human contributors

---

# Core Philosophy

The SDK should allow developers to do:

```python
from kite_auto import KiteClient

client = KiteClient()

await client.login()

positions = await client.positions()
quote = await client.quote("NSE:RELIANCE")
```

without manually handling:
- request tokens
- access tokens
- session persistence
- retries
- browser automation
- TOTP generation
- websocket reconnections

---

# Compliance Warning

Automated headless login may violate Zerodha terms or exchange regulations.

The SDK MUST support:

1. Manual login flow
2. Automated headless login flow

Authentication strategies must remain pluggable.

---

# Required Skills / Competencies

## Python Engineering
- Asyncio
- Context managers
- Type hints
- Packaging
- Dependency injection
- Exception architecture

## Networking
- HTTP lifecycle
- WebSockets
- Rate limiting
- Retries
- Backoff algorithms

## Browser Automation
- Playwright
- DOM selectors
- Headless Chromium
- Session handling

## Security
- Credential isolation
- Secure token storage
- Environment variable handling
- Secret management

## Trading Infrastructure
- Zerodha Kite APIs
- Market data systems
- Streaming architectures
- Order lifecycle handling

---

# Recommended Tech Stack

| Area | Technology |
|---|---|
| Language | Python 3.12+ |
| Browser Automation | Playwright |
| HTTP Client | httpx |
| Async Runtime | asyncio |
| WebSockets | websockets |
| TOTP | pyotp |
| Config | pydantic-settings |
| Logging | loguru |
| Retry | tenacity |
| Testing | pytest |
| Packaging | poetry |
| Type Checking | mypy |
| Linting | ruff |

---

# Repository Structure

```text
kite-auto-sdk/
│
├── kite_auto/
│   ├── auth/
│   │   ├── playwright_auth.py
│   │   ├── manual_auth.py
│   │   ├── token_manager.py
│   │   ├── checksum.py
│   │   └── session_store.py
│   │
│   ├── client/
│   │   ├── rest_client.py
│   │   ├── websocket_client.py
│   │   └── kite_client.py
│   │
│   ├── models/
│   │   ├── orders.py
│   │   ├── market.py
│   │   └── positions.py
│   │
│   ├── utils/
│   │   ├── retry.py
│   │   ├── logger.py
│   │   └── rate_limit.py
│   │
│   ├── config/
│   │   └── settings.py
│   │
│   └── exceptions/
│
├── tests/
├── examples/
├── docs/
├── pyproject.toml
├── README.md
└── .env.example
```

---

# Architecture Principles

## 1. Async-First

All internal systems should be asynchronous.

The SDK may expose optional synchronous wrappers later.

---

## 2. Authentication Must Be Pluggable

Define:

```python
class AuthStrategy(ABC):
    async def login(self) -> str:
        pass
```

Implementations:
- ManualAuthStrategy
- PlaywrightAuthStrategy

---

## 3. Session Storage Must Be Abstracted

Define:

```python
class SessionStore(ABC):
    async def save(self):
        pass

    async def load(self):
        pass
```

Implementations:
- JSON store
- Redis store
- SQLite store
- PostgreSQL store

---

## 4. Trading Logic Must Be Separate

Never tightly couple:
- auth
- broker transport
- order execution
- strategy engine

Future brokers:
- Zerodha
- Upstox
- AngelOne
- Interactive Brokers

should work behind the same interface.

---

# Authentication Lifecycle

```text
User Login
    ↓
Playwright Browser
    ↓
Zerodha Login Page
    ↓
TOTP Submission
    ↓
Redirect URL
    ↓
Extract request_token
    ↓
Generate checksum
    ↓
Exchange for access_token
    ↓
Persist session
    ↓
Trading APIs
```

---

# Environment Variables

```env
KITE_API_KEY=
KITE_API_SECRET=
KITE_USER_ID=
KITE_PASSWORD=
KITE_TOTP_SECRET=

HEADLESS=true
SESSION_STORE=json
LOG_LEVEL=INFO
```

---

# Codex Build Roadmap

## Phase 1
Project scaffolding

## Phase 2
Configuration management

## Phase 3
Playwright auth engine

## Phase 4
Access token exchange

## Phase 5
Session persistence

## Phase 6
High-level trading SDK

## Phase 7
WebSocket streaming

## Phase 8
Retries and rate limiting

## Phase 9
Testing infrastructure

## Phase 10
Documentation and GitHub packaging

---

# Prompt 1

Create a production-grade Python SDK scaffold named `kite-auto-sdk`.

Requirements:
- Python 3.12+
- Poetry
- Async-first architecture
- Ruff
- Mypy
- Pytest
- Loguru
- Pydantic settings
- GitHub Actions
- README
- MIT License

Do not implement business logic yet.

---

# Prompt 2

Implement centralized configuration management using pydantic-settings.

Requirements:
- .env support
- Validation
- Typed settings
- Singleton config loader

---

# Prompt 3

Implement Playwright-based Zerodha login automation.

Requirements:
- Async Playwright
- Chromium
- TOTP generation
- request_token extraction
- Retry handling
- Structured logging

Return only request_token.

---

# Prompt 4

Implement access token exchange.

Requirements:
- SHA256 checksum
- httpx async client
- Parse responses
- Typed exceptions
- Retry transient failures

---

# Prompt 5

Implement session persistence.

Requirements:
- Abstract SessionStore
- JSON implementation
- Token expiry detection
- Automatic reauthentication

---

# Prompt 6

Implement high-level KiteClient abstraction.

Requirements:
- Automatic login
- Token reuse
- Reauthentication
- Trading methods
- Typed responses

---

# Prompt 7

Implement WebSocket streaming.

Requirements:
- Automatic reconnect
- Subscription handling
- Tick parsing
- Graceful shutdown

---

# Prompt 8

Implement retry and rate limiting infrastructure.

Requirements:
- Exponential backoff
- Circuit breaker
- Prevent duplicate orders

---

# Prompt 9

Create comprehensive tests.

Requirements:
- Async tests
- Playwright mocks
- API mocks
- Coverage >90%

---

# Prompt 10

Create production-grade documentation.

Requirements:
- Architecture diagrams
- Setup instructions
- Troubleshooting
- Docker support

---

# Recommended Future Extensions

- Redis session store
- Multi-account support
- Risk management engine
- Strategy engine
- AI signal integration
- Kafka streaming
- Telegram alerts
- Prometheus metrics

---

# Final Engineering Rule

Never expose raw Zerodha complexity to end users.

The SDK exists to:
- simplify infrastructure
- stabilize automation
- abstract broker complexity
- accelerate strategy development

Because humans would rather spend six hours debugging browser selectors than designing resilient systems. A magnificent misuse of consciousness.
