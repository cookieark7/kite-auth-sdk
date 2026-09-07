"""High-level Kite SDK client."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, Self

from kite_auto.auth.base import AuthStrategy
from kite_auto.auth.playwright_auth import PlaywrightAuthStrategy
from kite_auto.auth.session_store import SessionStore
from kite_auto.auth.token_manager import TokenExchangeProtocol, TokenManager
from kite_auto.client.rest_client import RestClient
from kite_auto.client.websocket_client import TickHandler, WebSocketClient, WebSocketMode
from kite_auto.config import Settings, get_settings
from kite_auto.exceptions import KiteApiError
from kite_auto.models import Order, PositionsResponse, Quote
from kite_auto.models.historical import Candle, CandleInterval, format_candle_time
from kite_auto.models.instruments import Instrument, parse_instruments_csv
from kite_auto.utils.rate_limit import DuplicateOrderGuard


class RestTransportProtocol(Protocol):
    """Protocol for Kite REST transports used by the high-level client."""

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> Any:
        """Send a GET request."""

    async def get_text(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> str:
        """Send a GET request and return the raw response body."""

    async def close(self) -> None:
        """Close any transport resources."""

    async def post(
        self,
        path: str,
        *,
        data: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        """Send a POST request."""


class WebSocketTransportProtocol(Protocol):
    """Protocol for Kite WebSocket transports used by the high-level client."""

    async def subscribe(
        self,
        tokens: Sequence[int],
        *,
        mode: WebSocketMode | str = WebSocketMode.QUOTE,
    ) -> None:
        """Subscribe to instrument tokens."""

    async def listen(self, *, on_ticks: TickHandler | None = None) -> None:
        """Listen to streaming ticks."""

    async def close(self) -> None:
        """Close streaming resources."""


class KiteClient:
    """Primary public SDK entry point."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str | None = None,
        auth_strategy: AuthStrategy | None = None,
        token_manager: TokenManager | None = None,
        session_store: SessionStore | None = None,
        exchange_client: TokenExchangeProtocol | None = None,
        rest_client: RestTransportProtocol | None = None,
        websocket_client: WebSocketTransportProtocol | None = None,
        order_guard: DuplicateOrderGuard | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._api_key = api_key or self._settings.kite_api_key
        self._auth_strategy = auth_strategy or (
            None if token_manager is not None else PlaywrightAuthStrategy(self._settings)
        )
        self._token_manager = token_manager or TokenManager(
            exchange_client,
            auth_strategy=self._auth_strategy,
            session_store=session_store,
        )
        self._rest_client = rest_client or RestClient(
            self._settings,
            api_key=self._api_key,
            access_token_provider=self._token_manager.get_access_token,
        )
        self._websocket_client = websocket_client
        self._order_guard = order_guard or DuplicateOrderGuard()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close underlying client resources."""
        await self._rest_client.close()
        if self._websocket_client is not None:
            await self._websocket_client.close()

    async def login(self, *, force_refresh: bool = False) -> str:
        """Authenticate if needed and return a valid access token."""
        return await self._token_manager.get_access_token(force_refresh=force_refresh)

    async def positions(self) -> PositionsResponse:
        """Return net and day positions."""
        data = await self._rest_client.get("/portfolio/positions")
        if not isinstance(data, dict):
            raise KiteApiError("Kite positions response data must be an object.")
        return PositionsResponse.from_mapping(data)

    async def quote(self, instrument: str) -> Quote:
        """Return a full market quote for one instrument."""
        quotes = await self.quote_many([instrument])
        quote = quotes.get(instrument)
        if quote is None:
            raise KiteApiError(f"Kite quote response did not contain {instrument}.")
        return quote

    async def quote_many(self, instruments: list[str] | tuple[str, ...]) -> dict[str, Quote]:
        """Return full market quotes for one or more instruments."""
        if not instruments:
            raise ValueError("At least one instrument is required.")

        params = [("i", instrument) for instrument in instruments]
        data = await self._rest_client.get("/quote", params=params)
        if not isinstance(data, dict):
            raise KiteApiError("Kite quote response data must be an object.")

        return {
            instrument: Quote.from_mapping(instrument, quote_data)
            for instrument, quote_data in data.items()
            if isinstance(instrument, str) and isinstance(quote_data, dict)
        }

    async def historical_data(
        self,
        instrument_token: int,
        interval: CandleInterval | str,
        *,
        from_: datetime | str,
        to: datetime | str,
        continuous: bool = False,
        oi: bool = False,
    ) -> tuple[Candle, ...]:
        """Return historical OHLCV candles for an instrument token.

        ``from_``/``to`` accept a ``datetime`` (formatted to Kite's expected
        ``yyyy-mm-dd HH:MM:SS``) or a pre-formatted string. Set ``continuous`` for
        continuous futures data and ``oi`` to include open interest.
        """
        interval_value = interval.value if isinstance(interval, CandleInterval) else interval
        params = {
            "from": format_candle_time(from_),
            "to": format_candle_time(to),
            "continuous": "1" if continuous else "0",
            "oi": "1" if oi else "0",
        }
        data = await self._rest_client.get(
            f"/instruments/historical/{instrument_token}/{interval_value}",
            params=params,
        )
        if not isinstance(data, dict):
            raise KiteApiError("Kite historical response data must be an object.")

        candles = data.get("candles")
        if not isinstance(candles, list):
            raise KiteApiError("Kite historical response did not contain candles.")

        return tuple(
            Candle.from_sequence(row) for row in candles if isinstance(row, list | tuple)
        )

    async def instruments(self, exchange: str | None = None) -> tuple[Instrument, ...]:
        """Fetch and parse the Kite instrument master (CSV).

        Pass ``exchange`` (e.g. ``"NSE"``) to scope the dump. Returns parsed,
        typed instruments; building a symbol↔token index and caching the result
        is the application's responsibility.
        """
        path = "/instruments" if exchange is None else f"/instruments/{exchange}"
        text = await self._rest_client.get_text(path)
        return parse_instruments_csv(text)

    async def orders(self) -> tuple[Order, ...]:
        """Return order history for the day."""
        data = await self._rest_client.get("/orders")
        if not isinstance(data, list):
            raise KiteApiError("Kite orders response data must be a list.")
        return tuple(Order.from_mapping(item) for item in data if isinstance(item, dict))

    async def place_order(self, *, variety: str = "regular", **order: Any) -> Order:
        """Place an order with duplicate-submission protection."""
        if not order:
            raise ValueError("Order payload cannot be empty.")

        payload = {"variety": variety, **order}
        await self._order_guard.reserve(payload)
        data = await self._rest_client.post(f"/orders/{variety}", data=order)
        if not isinstance(data, dict):
            raise KiteApiError("Kite place-order response data must be an object.")

        order_id = data.get("order_id")
        if not isinstance(order_id, str) or not order_id:
            raise KiteApiError("Kite place-order response did not contain order_id.")

        return Order(order_id=order_id, raw=data)

    def websocket(self) -> WebSocketTransportProtocol:
        """Return the lazily-created WebSocket streaming client."""
        if self._websocket_client is None:
            self._websocket_client = WebSocketClient(
                self._settings,
                api_key=self._api_key,
                token_manager=self._token_manager,
            )
        return self._websocket_client

    async def stream(
        self,
        tokens: list[int] | tuple[int, ...],
        *,
        mode: WebSocketMode | str = WebSocketMode.QUOTE,
        on_ticks: TickHandler | None = None,
    ) -> None:
        """Subscribe to tokens and listen to the WebSocket stream."""
        websocket = self.websocket()
        await websocket.subscribe(tokens, mode=mode)
        await websocket.listen(on_ticks=on_ticks)
