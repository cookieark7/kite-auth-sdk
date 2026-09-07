"""Additional edge coverage for Prompt 9 coverage gates."""

import struct
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from kite_auto.auth.manual_auth import ManualAuthStrategy
from kite_auto.auth.playwright_auth import PlaywrightAuthStrategy
from kite_auto.auth.session_store import JsonSessionStore, KiteSession, parse_datetime
from kite_auto.auth.token_manager import TokenExchangeClient, TokenManager
from kite_auto.client.rest_client import RestClient
from kite_auto.client.websocket_client import (
    WebSocketClient,
    WebSocketMode,
    maybe_await,
    parse_binary_ticks,
    parse_depth,
    parse_tick_packet,
    parse_websocket_message,
    timestamp_from_packet,
)
from kite_auto.exceptions import (
    AuthenticationError,
    ConfigurationError,
    DuplicateOrderError,
    KiteApiError,
    KiteWebSocketError,
    NotImplementedInScaffoldError,
    SessionStoreError,
    TokenExchangeError,
)
from kite_auto.models.market import OHLC, Quote, optional_datetime, optional_float, optional_int
from kite_auto.models.orders import Order
from kite_auto.models.positions import PositionsResponse
from kite_auto.utils import logger
from kite_auto.utils.rate_limit import DuplicateOrderGuard, RateLimiter, normalize_order_payload
from kite_auto.utils.retry import CircuitBreaker, RetryConfig, retry_async

TEST_ACCESS_TOKEN = "access-token"  # noqa: S105
TEST_API_SECRET = "api-secret"  # noqa: S105


class EmptyLocator:
    @property
    def first(self) -> "EmptyLocator":
        return self

    async def count(self) -> int:
        return 0

    async def wait_for(self, **kwargs: object) -> None:
        # No matching element: behave like a real locator timing out.
        raise PlaywrightTimeoutError("no matching selector")

    async def fill(self, value: str, **kwargs: object) -> None:
        return None

    async def press_sequentially(self, value: str, **kwargs: object) -> None:
        return None

    async def click(self, **kwargs: object) -> None:
        return None


class TimeoutLocator(EmptyLocator):
    async def count(self) -> int:
        raise PlaywrightTimeoutError("slow selector")


class EmptyPage:
    url = "https://example.com/callback?status=success"

    def locator(self, selector: str) -> EmptyLocator:
        return EmptyLocator()


class TimeoutPage(EmptyPage):
    def locator(self, selector: str) -> EmptyLocator:
        return TimeoutLocator()

    async def wait_for_url(self, predicate: object, **kwargs: object) -> None:
        raise PlaywrightTimeoutError("slow redirect")


class IterWebSocket:
    def __init__(self, messages: list[bytes | str]) -> None:
        self.messages = messages
        self.sent: list[str] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> bytes | str:
        return self.messages.pop(0)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> AsyncIterator[bytes | str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes | str]:
        for message in self.messages:
            yield message


class LegacyProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        return TEST_ACCESS_TOKEN


async def async_value(value: Any) -> Any:
    return value


def u16(value: int) -> bytes:
    return struct.pack(">H", value)


def u32(value: int) -> bytes:
    return struct.pack(">I", value)


def price(value: float) -> bytes:
    return u32(round(value * 100))


def packet_frame(*packets: bytes) -> bytes:
    frame = u16(len(packets))
    for packet in packets:
        frame += u16(len(packet)) + packet
    return frame


@pytest.mark.asyncio
async def test_manual_auth_placeholder_and_logger_export() -> None:
    assert logger is not None
    with pytest.raises(NotImplementedInScaffoldError):
        await ManualAuthStrategy().login()


@pytest.mark.asyncio
async def test_playwright_private_edge_paths() -> None:
    with pytest.raises(ValueError):
        PlaywrightAuthStrategy(max_attempts=0)
    with pytest.raises(ValueError):
        PlaywrightAuthStrategy(timeout_ms=0)

    strategy = PlaywrightAuthStrategy(
        api_key="api-key",
        user_id="user",
        password="password",  # noqa: S106
        totp_secret="JBSWY3DPEHPK3PXP",  # noqa: S106
        login_url="https://example.com/login?x=1",
        max_attempts=1,
    )

    assert strategy._build_login_url() == "https://example.com/login?x=1&v=3&api_key=api-key"
    assert PlaywrightAuthStrategy._secret_value(None) is None
    assert PlaywrightAuthStrategy._secret_value("plain") == "plain"

    with pytest.raises(AuthenticationError, match="input"):
        await strategy._fill_first_available(EmptyPage(), ["missing"], "value")
    with pytest.raises(AuthenticationError, match="button"):
        await strategy._click_first_available(EmptyPage(), ["missing"])
    assert await strategy._click_first_available(EmptyPage(), ["missing"], required=False) is False

    with pytest.raises(AuthenticationError, match="input"):
        await strategy._fill_first_available(TimeoutPage(), ["slow"], "value")
    with pytest.raises(AuthenticationError, match="input"):
        await strategy._type_first_available(EmptyPage(), ["missing"], "value")


@pytest.mark.asyncio
async def test_token_exchange_edge_paths_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        TokenExchangeClient(timeout=0)
    with pytest.raises(ValueError):
        TokenExchangeClient(max_attempts=0)

    client = TokenExchangeClient(api_key="api-key", api_secret=TEST_API_SECRET)
    with pytest.raises(ConfigurationError, match="request_token"):
        await client.exchange_request_token("")

    assert TokenExchangeClient._secret_value(None) is None
    assert TokenExchangeClient._secret_value("plain") == "plain"
    assert client._parse_login_time(None) is None
    assert client._parse_login_time("not-time") is None
    assert (
        client._error_message({"error_type": "TokenException"}, httpx.Response(400))
        == "Kite token exchange failed with TokenException."
    )
    assert (
        client._error_message({}, httpx.Response(418))
        == "Kite token exchange failed with HTTP 418."
    )

    with pytest.raises(TokenExchangeError, match="JSON must be an object"):
        client._response_json(httpx.Response(200, json=[]))
    with pytest.raises(TokenExchangeError, match="data object"):
        client._parse_response(httpx.Response(200, json={"status": "success", "data": []}))
    with pytest.raises(TokenExchangeError, match="TokenException"):
        client._parse_response(
            httpx.Response(200, json={"status": "error", "error_type": "TokenException"})
        )

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.response = httpx.Response(
                200,
                json={"status": "success", "data": {"access_token": TEST_ACCESS_TOKEN}},
            )

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def post(self, path: str, data: object) -> httpx.Response:
            return self.response

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = await TokenExchangeClient(
        api_key="api-key",
        api_secret=TEST_API_SECRET,
    ).exchange_request_token("request-token")
    assert response.access_token == TEST_ACCESS_TOKEN


@pytest.mark.asyncio
async def test_token_manager_validation_and_naive_clock() -> None:
    with pytest.raises(ValueError):
        TokenManager(session_ttl=timedelta(0))
    with pytest.raises(ValueError):
        TokenManager(expiry_skew=timedelta(seconds=-1))

    manager = TokenManager(clock=lambda: datetime(2026, 5, 19, 9, 30))
    assert manager._now().tzinfo is UTC


@pytest.mark.asyncio
async def test_session_store_error_edges(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SessionStoreError, match="access_token"):
        KiteSession.from_dict({"created_at": "2026-05-19T09:30:00+00:00", "expires_at": "x"})
    with pytest.raises(SessionStoreError, match="timestamp"):
        KiteSession.from_dict({"access_token": TEST_ACCESS_TOKEN})
    with pytest.raises(SessionStoreError, match="invalid created_at"):
        parse_datetime("not-time", field_name="created_at")

    session_path = tmp_path / "session.json"
    store = JsonSessionStore(session_path)
    session = KiteSession(
        access_token=TEST_ACCESS_TOKEN,
        created_at=datetime(2026, 5, 19, tzinfo=UTC),
        expires_at=datetime(2026, 5, 20, tzinfo=UTC),
    )

    def fail_replace(self: object, path: object) -> None:
        raise OSError("nope")

    monkeypatch.setattr(type(session_path), "replace", fail_replace)
    with pytest.raises(SessionStoreError, match="save"):
        await store.save(session)

    monkeypatch.undo()
    await store.save(session)

    def fail_read_text(*args: object, **kwargs: object) -> str:
        raise OSError("nope")

    monkeypatch.setattr(type(session_path), "read_text", fail_read_text)
    with pytest.raises(SessionStoreError, match="load"):
        await store.load()

    monkeypatch.undo()

    def fail_unlink(*args: object, **kwargs: object) -> None:
        raise OSError("nope")

    monkeypatch.setattr(type(session_path), "unlink", fail_unlink)
    with pytest.raises(SessionStoreError, match="clear"):
        await store.clear()


@pytest.mark.asyncio
async def test_rest_client_edge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        RestClient(timeout=0)

    legacy = LegacyProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"ok": True}})

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = RestClient(
            api_key="api-key",
            access_token_provider=legacy,
            http_client=http_client,
        )
        async with client as entered:
            assert entered is client
        assert await client.get("/quote") == {"ok": True}

    with pytest.raises(TypeError):
        await RestClient(api_key="api-key", access_token_provider=legacy)._access_token(
            force_refresh=True
        )

    with pytest.raises(KiteApiError, match="JSON must be an object"):
        RestClient._response_json(httpx.Response(200, json=[]))
    assert (
        RestClient._error_message({"error_type": "InputException"}, httpx.Response(400))
        == "Kite API request failed with InputException."
    )
    assert (
        RestClient._error_message({}, httpx.Response(418))
        == "Kite API request failed with HTTP 418."
    )
    assert (
        RestClient._error_message_from_response(httpx.Response(500, content=b"nope"))
        == "Kite API request failed with HTTP 500."
    )

    async def boom_request(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("down")

    class FakeClient:
        async def request(self, *args: object, **kwargs: object) -> httpx.Response:
            return await boom_request(*args, **kwargs)

    client = RestClient(api_key="api-key", access_token_provider=legacy, http_client=FakeClient())  # type: ignore[arg-type]
    with pytest.raises(KiteApiError, match="network"):
        await client.post("/orders/regular", data={"tradingsymbol": "INFY"})


def test_model_edge_parsing() -> None:
    assert OHLC.from_mapping(None) == OHLC()
    assert optional_int({"value": True}, "value") is None
    assert optional_int({"value": 2.0}, "value") == 2
    assert optional_float({"value": True}, "value") is None
    assert optional_datetime({"value": "not-time"}, "value") is None

    quote = Quote.from_mapping(
        "NSE:INFY",
        {
            "instrument_token": True,
            "timestamp": "",
            "ohlc": [],
        },
    )
    assert quote.instrument_token is None
    assert quote.ohlc is None

    assert (
        PositionsResponse.from_mapping({"net": "bad", "day": [{"tradingsymbol": "INFY"}]}).net
        == ()
    )
    assert Order.from_mapping({}).order_id == ""


@pytest.mark.asyncio
async def test_websocket_edge_paths() -> None:
    with pytest.raises(ValueError):
        WebSocketClient(price_divisor=0)
    with pytest.raises(ValueError):
        WebSocketClient(reconnect_base_delay=-1)

    fake = IterWebSocket([b"\x00", '{"type":"message"}'])
    legacy = LegacyProvider()
    client = WebSocketClient(
        api_key="api-key",
        access_token_provider=legacy,
        websocket_factory=lambda uri: async_value(fake),
        websocket_url="wss://example.test/ws?x=1",
    )
    async with client as entered:
        assert entered.connected
        assert entered is client
        await client.connect()
        assert await client.receive_once() == ()

    assert fake.closed
    assert client._reconnect_delay(10) == 30.0
    assert client._should_reconnect(1)
    no_reconnect = WebSocketClient(
        api_key="api-key",
        access_token_provider=legacy,
        reconnect=False,
    )
    assert not no_reconnect._should_reconnect(1)

    with pytest.raises(TypeError):
        await WebSocketClient(api_key="api-key", access_token_provider=legacy)._access_token(
            force_refresh=True
        )
    with pytest.raises(KiteWebSocketError, match="connected"):
        await WebSocketClient(api_key="api-key", access_token_provider=legacy)._send_json({})
    with pytest.raises(ValueError, match="positive"):
        WebSocketClient._normalize_tokens([0])

    ticks_seen: list[Any] = []
    messages_seen: list[Any] = []
    await maybe_await(lambda value: ticks_seen.append(value), "sync")

    async def async_handler(value: object) -> None:
        messages_seen.append(value)

    await maybe_await(async_handler, "async")
    await maybe_await(None, "none")
    assert ticks_seen == ["sync"]
    assert messages_seen == ["async"]


def test_websocket_parser_error_edges() -> None:
    with pytest.raises(KiteWebSocketError, match="JSON object"):
        parse_websocket_message("[]")
    with pytest.raises(KiteWebSocketError, match="packet length"):
        parse_binary_ticks(u16(1) + b"\x00")
    with pytest.raises(KiteWebSocketError, match="packet payload"):
        parse_binary_ticks(u16(1) + u16(8) + b"\x00")
    with pytest.raises(KiteWebSocketError, match="too short"):
        parse_tick_packet(b"\x00")
    with pytest.raises(KiteWebSocketError, match="Unsupported"):
        parse_tick_packet(b"\x00" * 12)
    with pytest.raises(KiteWebSocketError, match="120 bytes"):
        parse_depth(b"bad", price_divisor=100)

    assert timestamp_from_packet(u32(0), 0) is None

    index_packet = b"".join(
        [
            u32(256265),
            price(19500.5),
            price(19600),
            price(19400),
            price(19510),
            price(19450),
            price(50.5),
        ]
    )
    tick = parse_binary_ticks(packet_frame(index_packet))[0]
    assert tick.mode is WebSocketMode.QUOTE
    assert tick.ohlc is not None
    assert tick.ohlc.high == 19600


@pytest.mark.asyncio
async def test_retry_and_rate_limit_validation_edges() -> None:
    for kwargs in (
        {"max_attempts": 0},
        {"initial_delay": -1},
        {"max_delay": -1},
        {"multiplier": 0},
        {"jitter": 2},
    ):
        with pytest.raises(ValueError):
            RetryConfig(**kwargs)

    assert RetryConfig(initial_delay=1, jitter=0.5).delay_for_attempt(1) >= 0
    assert RetryConfig(initial_delay=0, jitter=0.5).delay_for_attempt(1) == 0

    calls = 0

    async def flaky_impl() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("again")
        return "ok"

    flaky = retry_async(flaky_impl, config=RetryConfig(max_attempts=2, initial_delay=0))

    assert await flaky() == "ok"

    with pytest.raises(ValueError):
        RateLimiter(rate=0)
    with pytest.raises(ValueError):
        RateLimiter(capacity=0)
    limiter = RateLimiter(rate=1, capacity=1)
    with pytest.raises(ValueError):
        await limiter.acquire(0)
    with pytest.raises(ValueError):
        await limiter.acquire(2)

    with pytest.raises(ValueError):
        DuplicateOrderGuard(ttl_seconds=0)
    guard = DuplicateOrderGuard()
    fingerprint = await guard.reserve({"tags": ["a", "b"]})
    with pytest.raises(DuplicateOrderError):
        await guard.reserve({"tags": ["a", "b"]})
    await guard.release(fingerprint)
    assert await guard.reserve({"tags": ["a", "b"]}) == fingerprint
    assert normalize_order_payload(("a", {"b": 1})) == ["a", {"b": 1}]

    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(recovery_timeout=-1)
