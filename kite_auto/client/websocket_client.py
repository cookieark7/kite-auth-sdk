"""WebSocket streaming client for Kite Connect."""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from urllib.parse import urlencode

from loguru import logger
from websockets.asyncio.client import connect as websockets_connect
from websockets.exceptions import ConnectionClosed

from kite_auto.auth.token_manager import TokenManager
from kite_auto.config import Settings, get_settings
from kite_auto.exceptions import ConfigurationError, KiteWebSocketError
from kite_auto.models.market import OHLC

KITE_WEBSOCKET_URL = "wss://ws.kite.trade"
TICK_PACKET_HEADER_SIZE = 2
TICK_PACKET_LENGTH_SIZE = 2
DEFAULT_PRICE_DIVISOR = 100.0

type AccessTokenProvider = Callable[..., Awaitable[str]]
type WebSocketFactory = Callable[..., Any]
type TickHandler = Callable[[tuple["Tick", ...]], Awaitable[None] | None]
type MessageHandler = Callable[[Mapping[str, Any]], Awaitable[None] | None]
type ErrorHandler = Callable[[Exception], Awaitable[None] | None]


class WebSocketMode(StrEnum):
    """Supported Kite WebSocket subscription modes."""

    LTP = "ltp"
    QUOTE = "quote"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class DepthEntry:
    """One market-depth level."""

    quantity: int
    price: float
    orders: int


@dataclass(frozen=True, slots=True)
class MarketDepth:
    """Buy/sell market depth."""

    buy: tuple[DepthEntry, ...] = ()
    sell: tuple[DepthEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class Tick:
    """Parsed Kite binary tick packet."""

    instrument_token: int
    mode: WebSocketMode
    last_price: float | None = None
    last_traded_quantity: int | None = None
    average_traded_price: float | None = None
    volume_traded: int | None = None
    total_buy_quantity: int | None = None
    total_sell_quantity: int | None = None
    ohlc: OHLC | None = None
    change: float | None = None
    last_trade_time: datetime | None = None
    exchange_timestamp: datetime | None = None
    oi: int | None = None
    oi_day_high: int | None = None
    oi_day_low: int | None = None
    depth: MarketDepth | None = None
    raw: bytes | None = None


class WebSocketClient:
    """Async Kite WebSocket client with subscriptions and reconnect support."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str | None = None,
        access_token_provider: AccessTokenProvider | None = None,
        token_manager: TokenManager | None = None,
        websocket_url: str = KITE_WEBSOCKET_URL,
        websocket_factory: WebSocketFactory = websockets_connect,
        price_divisor: float = DEFAULT_PRICE_DIVISOR,
        reconnect: bool = True,
        reconnect_max_attempts: int | None = None,
        reconnect_base_delay: float = 1.0,
    ) -> None:
        self._settings = settings or get_settings()
        self._api_key = api_key or self._settings.kite_api_key
        self._access_token_provider = access_token_provider
        self._token_manager = token_manager
        self._websocket_url = websocket_url
        self._websocket_factory = websocket_factory
        self._price_divisor = price_divisor
        self._reconnect = reconnect
        self._reconnect_max_attempts = reconnect_max_attempts
        self._reconnect_base_delay = reconnect_base_delay
        self._websocket: Any | None = None
        self._subscriptions: set[int] = set()
        self._mode_by_token: dict[int, WebSocketMode] = {}
        self._closed = False

        if self._price_divisor <= 0:
            raise ValueError("price_divisor must be greater than 0")
        if self._reconnect_base_delay < 0:
            raise ValueError("reconnect_base_delay cannot be negative")

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def connected(self) -> bool:
        """Return whether a WebSocket connection is currently open."""
        return self._websocket is not None

    async def connect(self, *, force_refresh: bool = False) -> None:
        """Open the WebSocket connection and replay active subscriptions."""
        if self._websocket is not None:
            return

        self._closed = False
        uri = await self._build_uri(force_refresh=force_refresh)
        logger.info("Opening Kite WebSocket connection")
        self._websocket = await self._websocket_factory(uri)
        await self._replay_subscriptions()

    async def close(self) -> None:
        """Close the WebSocket connection and stop reconnect loops."""
        self._closed = True
        websocket = self._websocket
        self._websocket = None
        if websocket is not None:
            await websocket.close()

    async def subscribe(
        self,
        tokens: Sequence[int],
        *,
        mode: WebSocketMode | str = WebSocketMode.QUOTE,
    ) -> None:
        """Subscribe to instrument tokens and optionally set their streaming mode."""
        parsed_mode = WebSocketMode(mode)
        token_list = self._normalize_tokens(tokens)
        self._subscriptions.update(token_list)
        for token in token_list:
            self._mode_by_token[token] = parsed_mode

        if self._websocket is not None:
            await self._send_action("subscribe", token_list)
            await self._send_mode(parsed_mode, token_list)

    async def unsubscribe(self, tokens: Sequence[int]) -> None:
        """Unsubscribe from instrument tokens."""
        token_list = self._normalize_tokens(tokens)
        self._subscriptions.difference_update(token_list)
        for token in token_list:
            self._mode_by_token.pop(token, None)

        if self._websocket is not None:
            await self._send_action("unsubscribe", token_list)

    async def set_mode(self, mode: WebSocketMode | str, tokens: Sequence[int]) -> None:
        """Set the streaming mode for subscribed instrument tokens."""
        parsed_mode = WebSocketMode(mode)
        token_list = self._normalize_tokens(tokens)
        for token in token_list:
            if token not in self._subscriptions:
                raise KiteWebSocketError(f"Token {token} is not subscribed.")
            self._mode_by_token[token] = parsed_mode

        if self._websocket is not None:
            await self._send_mode(parsed_mode, token_list)

    async def listen(
        self,
        *,
        on_ticks: TickHandler | None = None,
        on_message: MessageHandler | None = None,
        on_error: ErrorHandler | None = None,
    ) -> None:
        """Listen forever, reconnecting when configured until `close()` is called."""
        attempt = 0
        while True:
            if self._closed:
                return
            try:
                await self.connect(force_refresh=attempt > 0)
                attempt = 0
                await self._consume_messages(on_ticks=on_ticks, on_message=on_message)
            except Exception as exc:
                self._websocket = None
                await self._handle_stream_error(exc, on_error=on_error)
                attempt += 1
                if not self._should_reconnect(attempt):
                    if isinstance(exc, ConnectionClosed):
                        raise KiteWebSocketError(
                            "Kite WebSocket reconnect attempts exhausted."
                        ) from exc
                    raise
                await asyncio.sleep(self._reconnect_delay(attempt))

    async def receive_once(self) -> tuple[Tick, ...] | Mapping[str, Any] | None:
        """Receive and parse one WebSocket message."""
        await self.connect()
        if self._websocket is None:
            raise KiteWebSocketError("WebSocket is not connected.")
        message = await self._websocket.recv()
        return parse_websocket_message(message, price_divisor=self._price_divisor)

    async def _consume_messages(
        self,
        *,
        on_ticks: TickHandler | None,
        on_message: MessageHandler | None,
    ) -> None:
        if self._websocket is None:
            raise KiteWebSocketError("WebSocket is not connected.")

        async for message in self._websocket:
            parsed = parse_websocket_message(message, price_divisor=self._price_divisor)
            if parsed is None:
                continue
            if isinstance(parsed, tuple):
                await maybe_await(on_ticks, parsed)
            else:
                await maybe_await(on_message, parsed)

    async def _build_uri(self, *, force_refresh: bool) -> str:
        access_token = await self._access_token(force_refresh=force_refresh)
        query = urlencode({"api_key": self._api_key_value, "access_token": access_token})
        separator = "&" if "?" in self._websocket_url else "?"
        return f"{self._websocket_url}{separator}{query}"

    async def _access_token(self, *, force_refresh: bool) -> str:
        provider = self._access_token_provider
        if provider is None and self._token_manager is not None:
            provider = self._token_manager.get_access_token
        if provider is None:
            raise ConfigurationError("WebSocketClient requires an access token provider.")
        try:
            return await provider(force_refresh=force_refresh)
        except TypeError:
            if force_refresh:
                raise
            return await provider()

    @property
    def _api_key_value(self) -> str:
        if self._api_key is None:
            raise ConfigurationError("KITE_API_KEY is required for WebSocket streaming.")
        return self._api_key

    async def _replay_subscriptions(self) -> None:
        if not self._subscriptions:
            return
        tokens = sorted(self._subscriptions)
        await self._send_action("subscribe", tokens)
        for mode in WebSocketMode:
            mode_tokens = [token for token in tokens if self._mode_by_token.get(token) == mode]
            if mode_tokens:
                await self._send_mode(mode, mode_tokens)

    async def _send_action(self, action: str, tokens: Sequence[int]) -> None:
        await self._send_json({"a": action, "v": list(tokens)})

    async def _send_mode(self, mode: WebSocketMode, tokens: Sequence[int]) -> None:
        await self._send_json({"a": "mode", "v": [mode.value, list(tokens)]})

    async def _send_json(self, payload: Mapping[str, Any]) -> None:
        if self._websocket is None:
            raise KiteWebSocketError("WebSocket is not connected.")
        await self._websocket.send(json.dumps(payload, separators=(",", ":")))

    def _should_reconnect(self, attempt: int) -> bool:
        if not self._reconnect:
            return False
        return self._reconnect_max_attempts is None or attempt <= self._reconnect_max_attempts

    def _reconnect_delay(self, attempt: int) -> float:
        return float(min(self._reconnect_base_delay * (2 ** max(attempt - 1, 0)), 30.0))

    @staticmethod
    async def _handle_stream_error(
        error: Exception,
        *,
        on_error: ErrorHandler | None,
    ) -> None:
        logger.warning("Kite WebSocket stream error: {}", error)
        await maybe_await(on_error, error)

    @staticmethod
    def _normalize_tokens(tokens: Sequence[int]) -> list[int]:
        if not tokens:
            raise ValueError("At least one instrument token is required.")
        normalized = [int(token) for token in tokens]
        if any(token <= 0 for token in normalized):
            raise ValueError("Instrument tokens must be positive integers.")
        return normalized


def parse_websocket_message(
    message: bytes | str,
    *,
    price_divisor: float = DEFAULT_PRICE_DIVISOR,
) -> tuple[Tick, ...] | Mapping[str, Any] | None:
    """Parse one Kite WebSocket message."""
    if isinstance(message, bytes):
        return parse_binary_ticks(message, price_divisor=price_divisor)

    try:
        payload = json.loads(message)
    except json.JSONDecodeError as exc:
        raise KiteWebSocketError("Kite WebSocket text message was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise KiteWebSocketError("Kite WebSocket text message must be a JSON object.")
    return payload


def parse_binary_ticks(
    frame: bytes,
    *,
    price_divisor: float = DEFAULT_PRICE_DIVISOR,
) -> tuple[Tick, ...]:
    """Parse Kite binary tick frames."""
    if len(frame) <= 1:
        return ()
    if len(frame) < TICK_PACKET_HEADER_SIZE:
        raise KiteWebSocketError("Kite binary frame is too short.")

    packet_count = read_uint16(frame, 0)
    offset = TICK_PACKET_HEADER_SIZE
    ticks: list[Tick] = []

    for _ in range(packet_count):
        if offset + TICK_PACKET_LENGTH_SIZE > len(frame):
            raise KiteWebSocketError("Kite binary frame ended before packet length.")
        packet_length = read_uint16(frame, offset)
        offset += TICK_PACKET_LENGTH_SIZE
        if offset + packet_length > len(frame):
            raise KiteWebSocketError("Kite binary frame ended before packet payload.")

        packet = frame[offset : offset + packet_length]
        offset += packet_length
        ticks.append(parse_tick_packet(packet, price_divisor=price_divisor))

    return tuple(ticks)


def parse_tick_packet(packet: bytes, *, price_divisor: float = DEFAULT_PRICE_DIVISOR) -> Tick:
    """Parse one Kite binary tick packet."""
    if len(packet) < 8:
        raise KiteWebSocketError("Kite tick packet is too short.")

    instrument_token = read_uint32(packet, 0)
    last_price = read_price(packet, 4, price_divisor=price_divisor)

    if len(packet) == 8:
        return Tick(
            instrument_token=instrument_token,
            mode=WebSocketMode.LTP,
            last_price=last_price,
            raw=packet,
        )

    if len(packet) in {28, 32}:
        return parse_index_packet(packet, price_divisor=price_divisor)

    if len(packet) in {44, 184}:
        return parse_instrument_packet(packet, price_divisor=price_divisor)

    raise KiteWebSocketError(f"Unsupported Kite tick packet length: {len(packet)}.")


def parse_index_packet(packet: bytes, *, price_divisor: float) -> Tick:
    """Parse index quote/full packets."""
    return Tick(
        instrument_token=read_uint32(packet, 0),
        mode=WebSocketMode.FULL if len(packet) == 32 else WebSocketMode.QUOTE,
        last_price=read_price(packet, 4, price_divisor=price_divisor),
        ohlc=OHLC(
            high=read_price(packet, 8, price_divisor=price_divisor),
            low=read_price(packet, 12, price_divisor=price_divisor),
            open=read_price(packet, 16, price_divisor=price_divisor),
            close=read_price(packet, 20, price_divisor=price_divisor),
        ),
        change=read_price(packet, 24, price_divisor=price_divisor),
        exchange_timestamp=timestamp_from_packet(packet, 28) if len(packet) == 32 else None,
        raw=packet,
    )


def parse_instrument_packet(packet: bytes, *, price_divisor: float) -> Tick:
    """Parse standard instrument quote/full packets."""
    tick = Tick(
        instrument_token=read_uint32(packet, 0),
        mode=WebSocketMode.FULL if len(packet) == 184 else WebSocketMode.QUOTE,
        last_price=read_price(packet, 4, price_divisor=price_divisor),
        last_traded_quantity=read_uint32(packet, 8),
        average_traded_price=read_price(packet, 12, price_divisor=price_divisor),
        volume_traded=read_uint32(packet, 16),
        total_buy_quantity=read_uint32(packet, 20),
        total_sell_quantity=read_uint32(packet, 24),
        ohlc=OHLC(
            open=read_price(packet, 28, price_divisor=price_divisor),
            high=read_price(packet, 32, price_divisor=price_divisor),
            low=read_price(packet, 36, price_divisor=price_divisor),
            close=read_price(packet, 40, price_divisor=price_divisor),
        ),
        raw=packet,
    )
    if len(packet) == 44:
        return tick

    return Tick(
        instrument_token=tick.instrument_token,
        mode=WebSocketMode.FULL,
        last_price=tick.last_price,
        last_traded_quantity=tick.last_traded_quantity,
        average_traded_price=tick.average_traded_price,
        volume_traded=tick.volume_traded,
        total_buy_quantity=tick.total_buy_quantity,
        total_sell_quantity=tick.total_sell_quantity,
        ohlc=tick.ohlc,
        last_trade_time=timestamp_from_packet(packet, 44),
        oi=read_uint32(packet, 48),
        oi_day_high=read_uint32(packet, 52),
        oi_day_low=read_uint32(packet, 56),
        exchange_timestamp=timestamp_from_packet(packet, 60),
        depth=parse_depth(packet[64:184], price_divisor=price_divisor),
        raw=packet,
    )


def parse_depth(depth_packet: bytes, *, price_divisor: float) -> MarketDepth:
    """Parse the 10 depth entries in full mode packets."""
    if len(depth_packet) != 120:
        raise KiteWebSocketError("Kite depth packet must be 120 bytes.")
    entries = [
        DepthEntry(
            quantity=read_uint32(depth_packet, offset),
            price=read_price(depth_packet, offset + 4, price_divisor=price_divisor),
            orders=read_uint16(depth_packet, offset + 8),
        )
        for offset in range(0, len(depth_packet), 12)
    ]
    return MarketDepth(buy=tuple(entries[:5]), sell=tuple(entries[5:]))


def read_uint16(data: bytes, offset: int) -> int:
    """Read an unsigned 16-bit big-endian integer."""
    return int(struct.unpack_from(">H", data, offset)[0])


def read_uint32(data: bytes, offset: int) -> int:
    """Read an unsigned 32-bit big-endian integer."""
    return int(struct.unpack_from(">I", data, offset)[0])


def read_price(data: bytes, offset: int, *, price_divisor: float) -> float:
    """Read a Kite integer price and convert to decimal price."""
    return read_uint32(data, offset) / price_divisor


def timestamp_from_packet(data: bytes, offset: int) -> datetime | None:
    """Read an optional epoch timestamp from a tick packet."""
    value = read_uint32(data, offset)
    if value == 0:
        return None
    return datetime.fromtimestamp(value, tz=UTC)


async def maybe_await[T](handler: Callable[[T], Awaitable[None] | None] | None, value: T) -> None:
    """Call a possibly-async event handler."""
    if handler is None:
        return
    result = handler(value)
    if result is not None:
        await result
