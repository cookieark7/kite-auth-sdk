"""Tests for high-level KiteClient."""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from kite_auto.auth.base import AuthStrategy
from kite_auto.auth.session_store import KiteSession, SessionStore
from kite_auto.auth.token_manager import AccessTokenResponse, TokenManager
from kite_auto.client.kite_client import KiteClient
from kite_auto.exceptions import DuplicateOrderError, KiteApiError

TEST_ACCESS_TOKEN = "access-token"  # noqa: S105
TEST_REQUEST_TOKEN = "request-token"  # noqa: S105


class MemorySessionStore(SessionStore):
    def __init__(self, session: KiteSession | None = None) -> None:
        self.session = session

    async def save(self, session: KiteSession) -> None:
        self.session = session

    async def load(self) -> KiteSession | None:
        return self.session

    async def clear(self) -> None:
        self.session = None


class FakeAuthStrategy(AuthStrategy):
    async def login(self) -> str:
        return TEST_REQUEST_TOKEN


class FakeExchangeClient:
    async def exchange_request_token(self, request_token: str) -> AccessTokenResponse:
        return AccessTokenResponse(access_token=f"access-for-{request_token}")


class FakeRestClient:
    def __init__(
        self,
        responses: Mapping[str, Any],
        *,
        text_responses: Mapping[str, str] | None = None,
    ) -> None:
        self.responses = responses
        self.text_responses = text_responses or {}
        self.calls: list[tuple[str, Mapping[str, Any] | list[tuple[str, Any]] | None]] = []
        self.closed = False

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> Any:
        self.calls.append((path, params))
        return self.responses[path]

    async def get_text(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> str:
        self.calls.append((path, params))
        return self.text_responses[path]

    async def close(self) -> None:
        self.closed = True

    async def post(
        self,
        path: str,
        *,
        data: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        self.calls.append((path, data or json))
        return self.responses[path]


class FakeWebSocketClient:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[list[int], str]] = []
        self.listen_count = 0
        self.closed = False

    async def subscribe(self, tokens: Sequence[int], *, mode: str = "quote") -> None:
        self.subscriptions.append((list(tokens), mode))

    async def listen(self, *, on_ticks: object = None) -> None:
        self.listen_count += 1

    async def close(self) -> None:
        self.closed = True


def fixed_clock() -> datetime:
    return datetime(2026, 5, 19, 9, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_kite_client_login_uses_token_manager() -> None:
    store = MemorySessionStore()
    token_manager = TokenManager(
        FakeExchangeClient(),
        auth_strategy=FakeAuthStrategy(),
        session_store=store,
        clock=fixed_clock,
    )
    rest = FakeRestClient({})
    client = KiteClient(token_manager=token_manager, rest_client=rest)

    access_token = await client.login()

    assert access_token == "access-for-request-token"  # noqa: S105
    assert store.session is not None
    assert store.session.access_token == access_token


@pytest.mark.asyncio
async def test_kite_client_positions_returns_typed_response() -> None:
    rest = FakeRestClient(
        {
            "/portfolio/positions": {
                "net": [
                    {
                        "tradingsymbol": "INFY",
                        "exchange": "NSE",
                        "instrument_token": 408065,
                        "product": "CNC",
                        "quantity": 3,
                        "last_price": 1412.95,
                        "pnl": 12.5,
                    }
                ],
                "day": [],
            }
        }
    )
    client = KiteClient(token_manager=valid_token_manager(), rest_client=rest)

    positions = await client.positions()

    assert len(positions.net) == 1
    assert positions.net[0].tradingsymbol == "INFY"
    assert positions.net[0].exchange == "NSE"
    assert positions.net[0].quantity == 3
    assert positions.net[0].last_price == 1412.95
    assert positions.day == ()


@pytest.mark.asyncio
async def test_kite_client_quote_returns_single_typed_quote() -> None:
    rest = FakeRestClient(
        {
            "/quote": {
                "NSE:INFY": {
                    "instrument_token": 408065,
                    "timestamp": "2026-05-19 15:30:00",
                    "last_price": 1412.95,
                    "ohlc": {"open": 1400, "high": 1420, "low": 1395, "close": 1389.65},
                }
            }
        }
    )
    client = KiteClient(token_manager=valid_token_manager(), rest_client=rest)

    quote = await client.quote("NSE:INFY")

    assert quote.instrument == "NSE:INFY"
    assert quote.instrument_token == 408065
    assert quote.timestamp == datetime(2026, 5, 19, 15, 30)
    assert quote.last_price == 1412.95
    assert quote.ohlc is not None
    assert quote.ohlc.close == 1389.65
    assert rest.calls == [("/quote", [("i", "NSE:INFY")])]


@pytest.mark.asyncio
async def test_kite_client_quote_many_returns_available_quotes() -> None:
    rest = FakeRestClient(
        {
            "/quote": {
                "NSE:INFY": {"last_price": 1412.95},
                "NSE:RELIANCE": {"last_price": 2840.5},
            }
        }
    )
    client = KiteClient(token_manager=valid_token_manager(), rest_client=rest)

    quotes = await client.quote_many(["NSE:INFY", "NSE:RELIANCE"])

    assert set(quotes) == {"NSE:INFY", "NSE:RELIANCE"}
    assert quotes["NSE:RELIANCE"].last_price == 2840.5


@pytest.mark.asyncio
async def test_kite_client_quote_raises_when_requested_quote_missing() -> None:
    client = KiteClient(
        token_manager=valid_token_manager(),
        rest_client=FakeRestClient({"/quote": {}}),
    )

    with pytest.raises(KiteApiError, match="NSE:INFY"):
        await client.quote("NSE:INFY")


@pytest.mark.asyncio
async def test_kite_client_orders_returns_typed_orders() -> None:
    rest = FakeRestClient(
        {
            "/orders": [
                {
                    "order_id": "order-1",
                    "tradingsymbol": "INFY",
                    "exchange": "NSE",
                    "status": "COMPLETE",
                    "quantity": 1,
                    "price": 1412.95,
                    "order_timestamp": "2026-05-19 09:31:00",
                }
            ]
        }
    )
    client = KiteClient(token_manager=valid_token_manager(), rest_client=rest)

    orders = await client.orders()

    assert len(orders) == 1
    assert orders[0].order_id == "order-1"
    assert orders[0].status == "COMPLETE"
    assert orders[0].order_timestamp == datetime(2026, 5, 19, 9, 31)


@pytest.mark.asyncio
async def test_kite_client_place_order_blocks_duplicate_payload() -> None:
    rest = FakeRestClient({"/orders/regular": {"order_id": "order-1"}})
    client = KiteClient(token_manager=valid_token_manager(), rest_client=rest)

    order = await client.place_order(
        exchange="NSE",
        tradingsymbol="INFY",
        transaction_type="BUY",
        quantity=1,
        product="CNC",
        order_type="MARKET",
    )

    assert order.order_id == "order-1"
    assert rest.calls[-1] == (
        "/orders/regular",
        {
            "exchange": "NSE",
            "tradingsymbol": "INFY",
            "transaction_type": "BUY",
            "quantity": 1,
            "product": "CNC",
            "order_type": "MARKET",
        },
    )

    with pytest.raises(DuplicateOrderError, match="Duplicate order"):
        await client.place_order(
            exchange="NSE",
            tradingsymbol="INFY",
            transaction_type="BUY",
            quantity=1,
            product="CNC",
            order_type="MARKET",
        )


@pytest.mark.asyncio
async def test_kite_client_close_delegates_to_rest_client() -> None:
    rest = FakeRestClient({})
    websocket = FakeWebSocketClient()
    client = KiteClient(
        token_manager=valid_token_manager(),
        rest_client=rest,
        websocket_client=websocket,
    )

    await client.close()

    assert rest.closed is True
    assert websocket.closed is True


@pytest.mark.asyncio
async def test_kite_client_stream_subscribes_and_listens() -> None:
    websocket = FakeWebSocketClient()
    client = KiteClient(
        token_manager=valid_token_manager(),
        rest_client=FakeRestClient({}),
        websocket_client=websocket,
    )

    await client.stream([408065], mode="full")

    assert websocket.subscriptions == [([408065], "full")]
    assert websocket.listen_count == 1


def valid_token_manager() -> TokenManager:
    now = fixed_clock()
    return TokenManager(
        FakeExchangeClient(),
        auth_strategy=FakeAuthStrategy(),
        session_store=MemorySessionStore(
            KiteSession(
                access_token=TEST_ACCESS_TOKEN,
                created_at=now - timedelta(hours=1),
                expires_at=now + timedelta(hours=1),
            )
        ),
        clock=fixed_clock,
    )


@pytest.mark.asyncio
async def test_kite_client_historical_data_returns_typed_candles() -> None:
    from kite_auto.models import CandleInterval

    path = "/instruments/historical/738561/5minute"
    rest = FakeRestClient(
        {
            path: {
                "candles": [
                    ["2026-06-26T09:15:00+05:30", 2975.0, 2992.8, 2968.1, 2975.05, 4821233],
                    ["2026-06-26T09:20:00+05:30", 2975.1, 2980.0, 2970.0, 2978.0, 120000],
                ]
            }
        }
    )
    client = KiteClient(token_manager=valid_token_manager(), rest_client=rest)

    candles = await client.historical_data(
        738561,
        CandleInterval.MINUTE_5,
        from_=datetime(2026, 6, 26, 9, 15, 0),
        to=datetime(2026, 6, 26, 15, 30, 0),
    )

    assert len(candles) == 2
    assert candles[0].open == 2975.0
    assert candles[1].close == 2978.0
    # Path + query params built correctly.
    called_path, params = rest.calls[0]
    assert called_path == path
    assert params == {
        "from": "2026-06-26 09:15:00",
        "to": "2026-06-26 15:30:00",
        "continuous": "0",
        "oi": "0",
    }


@pytest.mark.asyncio
async def test_kite_client_historical_data_sets_continuous_and_oi_flags() -> None:
    path = "/instruments/historical/123/day"
    rest = FakeRestClient({path: {"candles": []}})
    client = KiteClient(token_manager=valid_token_manager(), rest_client=rest)

    await client.historical_data(
        123, "day", from_="2026-06-01", to="2026-06-26", continuous=True, oi=True
    )

    _, params = rest.calls[0]
    assert isinstance(params, dict)
    assert params["continuous"] == "1"
    assert params["oi"] == "1"


@pytest.mark.asyncio
async def test_kite_client_historical_data_rejects_missing_candles() -> None:
    path = "/instruments/historical/123/day"
    rest = FakeRestClient({path: {"status": "ok"}})
    client = KiteClient(token_manager=valid_token_manager(), rest_client=rest)

    with pytest.raises(KiteApiError, match="candles"):
        await client.historical_data(123, "day", from_="2026-06-01", to="2026-06-26")


@pytest.mark.asyncio
async def test_kite_client_instruments_parses_csv() -> None:
    csv_text = (
        "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,"
        "strike,tick_size,lot_size,instrument_type,segment,exchange\n"
        "738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.05,1,EQ,NSE,NSE\n"
        "256265,1001,NIFTY26JUN24000CE,NIFTY,0,2026-06-25,24000,0.05,50,CE,NFO-OPT,NFO\n"
    )
    rest = FakeRestClient({}, text_responses={"/instruments/NSE": csv_text})
    client = KiteClient(token_manager=valid_token_manager(), rest_client=rest)

    instruments = await client.instruments("NSE")

    assert rest.calls[0][0] == "/instruments/NSE"
    assert len(instruments) == 2
    reliance = instruments[0]
    assert reliance.tradingsymbol == "RELIANCE"
    assert reliance.instrument_token == 738561
    assert reliance.tick_size == 0.05
    assert reliance.lot_size == 1
    assert reliance.expiry is None
    option = instruments[1]
    assert option.strike == 24000.0
    assert option.expiry == date(2026, 6, 25)
    assert option.instrument_type == "CE"


@pytest.mark.asyncio
async def test_kite_client_instruments_defaults_to_full_dump() -> None:
    rest = FakeRestClient({}, text_responses={"/instruments": "tradingsymbol\nINFY\n"})
    client = KiteClient(token_manager=valid_token_manager(), rest_client=rest)

    instruments = await client.instruments()

    assert rest.calls[0][0] == "/instruments"
    assert instruments[0].tradingsymbol == "INFY"
