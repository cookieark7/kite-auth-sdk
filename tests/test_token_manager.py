"""Tests for token lifecycle management."""

from datetime import UTC, datetime, timedelta

import pytest

from kite_auto.auth.base import AuthStrategy
from kite_auto.auth.session_store import KiteSession, SessionStore
from kite_auto.auth.token_manager import AccessTokenResponse, TokenManager
from kite_auto.exceptions import AuthenticationError

TEST_ACCESS_TOKEN = "access-token"  # noqa: S105
TEST_PUBLIC_TOKEN = "public-token"  # noqa: S105
TEST_REFRESHED_TOKEN = "refreshed-token"  # noqa: S105
TEST_REQUEST_TOKEN = "request-token"  # noqa: S105


class MemorySessionStore(SessionStore):
    def __init__(self, session: KiteSession | None = None) -> None:
        self.session = session
        self.saved: list[KiteSession] = []
        self.load_count = 0
        self.clear_count = 0

    async def save(self, session: KiteSession) -> None:
        self.session = session
        self.saved.append(session)

    async def load(self) -> KiteSession | None:
        self.load_count += 1
        return self.session

    async def clear(self) -> None:
        self.clear_count += 1
        self.session = None


class FakeAuthStrategy(AuthStrategy):
    def __init__(self, request_token: str = TEST_REQUEST_TOKEN) -> None:
        self.request_token = request_token
        self.login_count = 0

    async def login(self) -> str:
        self.login_count += 1
        return self.request_token


class FakeExchangeClient:
    def __init__(self, access_token: str = TEST_REFRESHED_TOKEN) -> None:
        self.access_token = access_token
        self.request_tokens: list[str] = []

    async def exchange_request_token(self, request_token: str) -> AccessTokenResponse:
        self.request_tokens.append(request_token)
        return AccessTokenResponse(
            access_token=self.access_token,
            public_token=TEST_PUBLIC_TOKEN,
            user_id="AB1234",
            raw={"access_token": self.access_token},
        )


def fixed_clock() -> datetime:
    return datetime(2026, 5, 19, 9, 30, tzinfo=UTC)


def make_session(
    *,
    access_token: str = TEST_ACCESS_TOKEN,
    expires_delta: timedelta = timedelta(hours=1),
) -> KiteSession:
    now = fixed_clock()
    return KiteSession(
        access_token=access_token,
        created_at=now - timedelta(hours=1),
        expires_at=now + expires_delta,
    )


@pytest.mark.asyncio
async def test_get_access_token_reuses_valid_session() -> None:
    store = MemorySessionStore(make_session())
    auth = FakeAuthStrategy()
    exchange = FakeExchangeClient()
    manager = TokenManager(
        exchange,
        auth_strategy=auth,
        session_store=store,
        clock=fixed_clock,
    )

    token = await manager.get_access_token()

    assert token == TEST_ACCESS_TOKEN
    assert auth.login_count == 0
    assert exchange.request_tokens == []
    assert store.load_count == 1
    assert store.saved == []


@pytest.mark.asyncio
async def test_get_access_token_reauthenticates_when_session_missing() -> None:
    store = MemorySessionStore()
    auth = FakeAuthStrategy()
    exchange = FakeExchangeClient()
    manager = TokenManager(
        exchange,
        auth_strategy=auth,
        session_store=store,
        session_ttl=timedelta(hours=12),
        clock=fixed_clock,
    )

    token = await manager.get_access_token()

    assert token == TEST_REFRESHED_TOKEN
    assert auth.login_count == 1
    assert exchange.request_tokens == [TEST_REQUEST_TOKEN]
    assert len(store.saved) == 1
    assert store.saved[0].access_token == TEST_REFRESHED_TOKEN
    assert store.saved[0].created_at == fixed_clock()
    assert store.saved[0].expires_at == fixed_clock() + timedelta(hours=12)


@pytest.mark.asyncio
async def test_get_access_token_reauthenticates_when_session_expired() -> None:
    store = MemorySessionStore(make_session(expires_delta=timedelta(minutes=-1)))
    auth = FakeAuthStrategy()
    exchange = FakeExchangeClient()
    manager = TokenManager(
        exchange,
        auth_strategy=auth,
        session_store=store,
        clock=fixed_clock,
    )

    token = await manager.get_access_token()

    assert token == TEST_REFRESHED_TOKEN
    assert auth.login_count == 1
    assert exchange.request_tokens == [TEST_REQUEST_TOKEN]
    assert store.session is not None
    assert store.session.access_token == TEST_REFRESHED_TOKEN


@pytest.mark.asyncio
async def test_get_access_token_force_refresh_bypasses_valid_session() -> None:
    store = MemorySessionStore(make_session())
    auth = FakeAuthStrategy()
    exchange = FakeExchangeClient()
    manager = TokenManager(
        exchange,
        auth_strategy=auth,
        session_store=store,
        clock=fixed_clock,
    )

    token = await manager.get_access_token(force_refresh=True)

    assert token == TEST_REFRESHED_TOKEN
    assert auth.login_count == 1
    assert store.load_count == 0


@pytest.mark.asyncio
async def test_get_access_token_requires_auth_strategy_to_refresh() -> None:
    manager = TokenManager(
        FakeExchangeClient(),
        session_store=MemorySessionStore(),
        clock=fixed_clock,
    )

    with pytest.raises(AuthenticationError, match="auth_strategy"):
        await manager.get_access_token()


@pytest.mark.asyncio
async def test_clear_session_delegates_to_store() -> None:
    store = MemorySessionStore(make_session())
    manager = TokenManager(
        FakeExchangeClient(),
        session_store=store,
        clock=fixed_clock,
    )

    await manager.clear_session()

    assert store.session is None
    assert store.clear_count == 1
