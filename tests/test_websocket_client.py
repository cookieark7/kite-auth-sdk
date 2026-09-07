"""Tests for Kite WebSocket streaming."""

import json
import struct
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from kite_auto.client.websocket_client import (
    Tick,
    WebSocketClient,
    WebSocketMode,
    parse_binary_ticks,
    parse_websocket_message,
)
from kite_auto.exceptions import ConfigurationError, KiteWebSocketError

TEST_API_KEY = "api-key"
TEST_ACCESS_TOKEN = "access-token"  # noqa: S105


class FakeWebSocket:
    def __init__(self, messages: list[bytes | str] | None = None) -> None:
        self.messages = messages or []
        self.sent: list[str] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> bytes | str:
        if not self.messages:
            raise RuntimeError("No messages available")
        return self.messages.pop(0)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> AsyncIterator[bytes | str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes | str]:
        for message in self.messages:
            yield message


def packet_frame(*packets: bytes) -> bytes:
    frame = struct.pack(">H", len(packets))
    for packet in packets:
        frame += struct.pack(">H", len(packet)) + packet
    return frame


def u32(value: int) -> bytes:
    return struct.pack(">I", value)


def u16(value: int) -> bytes:
    return struct.pack(">H", value)


def price(value: float) -> bytes:
    return u32(round(value * 100))


def test_parse_binary_ticks_ignores_heartbeat() -> None:
    assert parse_binary_ticks(b"\x00") == ()


def test_parse_ltp_tick_packet() -> None:
    frame = packet_frame(u32(408065) + price(1412.95))

    ticks = parse_binary_ticks(frame)

    assert ticks == (
        Tick(
            instrument_token=408065,
            mode=WebSocketMode.LTP,
            last_price=1412.95,
            raw=u32(408065) + price(1412.95),
        ),
    )


def test_parse_quote_tick_packet() -> None:
    packet = b"".join(
        [
            u32(408065),
            price(1412.95),
            u32(10),
            price(1410.25),
            u32(1000),
            u32(500),
            u32(600),
            price(1400),
            price(1420),
            price(1395),
            price(1389.65),
        ]
    )

    ticks = parse_binary_ticks(packet_frame(packet))

    assert len(ticks) == 1
    tick = ticks[0]
    assert tick.mode is WebSocketMode.QUOTE
    assert tick.instrument_token == 408065
    assert tick.last_price == 1412.95
    assert tick.last_traded_quantity == 10
    assert tick.average_traded_price == 1410.25
    assert tick.volume_traded == 1000
    assert tick.total_buy_quantity == 500
    assert tick.total_sell_quantity == 600
    assert tick.ohlc is not None
    assert tick.ohlc.open == 1400
    assert tick.ohlc.close == 1389.65


def test_parse_full_tick_packet_with_depth() -> None:
    timestamp = int(datetime(2026, 5, 19, 9, 30, tzinfo=UTC).timestamp())
    quote_part = b"".join(
        [
            u32(408065),
            price(1412.95),
            u32(10),
            price(1410.25),
            u32(1000),
            u32(500),
            u32(600),
            price(1400),
            price(1420),
            price(1395),
            price(1389.65),
            u32(timestamp),
            u32(123),
            u32(150),
            u32(100),
            u32(timestamp + 5),
        ]
    )
    depth = b"".join(
        u32(index + 1) + price(1400 + index) + u16(index + 2) + b"\x00\x00"
        for index in range(10)
    )

    tick = parse_binary_ticks(packet_frame(quote_part + depth))[0]

    assert tick.mode is WebSocketMode.FULL
    assert tick.last_trade_time == datetime(2026, 5, 19, 9, 30, tzinfo=UTC)
    assert tick.exchange_timestamp == datetime(2026, 5, 19, 9, 30, 5, tzinfo=UTC)
    assert tick.oi == 123
    assert tick.depth is not None
    assert len(tick.depth.buy) == 5
    assert len(tick.depth.sell) == 5
    assert tick.depth.buy[0].quantity == 1
    assert tick.depth.buy[0].price == 1400
    assert tick.depth.sell[0].orders == 7


def test_parse_websocket_text_message() -> None:
    message = parse_websocket_message('{"type":"message","data":"connected"}')

    assert message == {"type": "message", "data": "connected"}


def test_parse_websocket_rejects_bad_text_message() -> None:
    with pytest.raises(KiteWebSocketError, match="valid JSON"):
        parse_websocket_message("not-json")


@pytest.mark.asyncio
async def test_websocket_client_connects_and_replays_subscriptions() -> None:
    fake = FakeWebSocket()
    uris: list[str] = []

    async def factory(uri: str) -> FakeWebSocket:
        uris.append(uri)
        return fake

    async def token_provider(*, force_refresh: bool = False) -> str:
        assert force_refresh is False
        return TEST_ACCESS_TOKEN

    client = WebSocketClient(
        api_key=TEST_API_KEY,
        access_token_provider=token_provider,
        websocket_factory=factory,
    )

    await client.subscribe([408065], mode=WebSocketMode.FULL)
    await client.connect()

    assert uris == [
        "wss://ws.kite.trade?api_key=api-key&access_token=access-token",
    ]
    assert [json.loads(payload) for payload in fake.sent] == [
        {"a": "subscribe", "v": [408065]},
        {"a": "mode", "v": ["full", [408065]]},
    ]

    await client.close()

    assert fake.closed is True


@pytest.mark.asyncio
async def test_websocket_client_subscribe_unsubscribe_after_connect() -> None:
    fake = FakeWebSocket()

    async def factory(uri: str) -> FakeWebSocket:
        return fake

    async def token_provider(*, force_refresh: bool = False) -> str:
        return TEST_ACCESS_TOKEN

    client = WebSocketClient(
        api_key=TEST_API_KEY,
        access_token_provider=token_provider,
        websocket_factory=factory,
    )

    await client.connect()
    await client.subscribe([408065])
    await client.set_mode("ltp", [408065])
    await client.unsubscribe([408065])

    assert [json.loads(payload) for payload in fake.sent] == [
        {"a": "subscribe", "v": [408065]},
        {"a": "mode", "v": ["quote", [408065]]},
        {"a": "mode", "v": ["ltp", [408065]]},
        {"a": "unsubscribe", "v": [408065]},
    ]


@pytest.mark.asyncio
async def test_websocket_client_receive_once_parses_message() -> None:
    frame = packet_frame(u32(408065) + price(1412.95))
    fake = FakeWebSocket([frame])

    async def factory(uri: str) -> FakeWebSocket:
        return fake

    async def token_provider(*, force_refresh: bool = False) -> str:
        return TEST_ACCESS_TOKEN

    client = WebSocketClient(
        api_key=TEST_API_KEY,
        access_token_provider=token_provider,
        websocket_factory=factory,
    )

    message = await client.receive_once()

    assert isinstance(message, tuple)
    assert message[0].last_price == 1412.95


@pytest.mark.asyncio
async def test_websocket_client_requires_api_key_and_token_provider() -> None:
    client = WebSocketClient()

    with pytest.raises(ConfigurationError, match="access token provider"):
        await client.connect()

    async def token_provider(*, force_refresh: bool = False) -> str:
        return TEST_ACCESS_TOKEN

    client = WebSocketClient(access_token_provider=token_provider)

    with pytest.raises(ConfigurationError, match="KITE_API_KEY"):
        await client.connect()


@pytest.mark.asyncio
async def test_websocket_client_rejects_invalid_tokens_and_modes() -> None:
    async def token_provider(*, force_refresh: bool = False) -> str:
        return TEST_ACCESS_TOKEN

    client = WebSocketClient(api_key=TEST_API_KEY, access_token_provider=token_provider)

    with pytest.raises(ValueError, match="At least one"):
        await client.subscribe([])

    with pytest.raises(ValueError):
        await client.subscribe([408065], mode="invalid")

    with pytest.raises(KiteWebSocketError, match="not subscribed"):
        await client.set_mode(WebSocketMode.FULL, [408065])
