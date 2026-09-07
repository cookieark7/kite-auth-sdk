"""Position models."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self

from kite_auto.models.market import optional_float, optional_int


@dataclass(frozen=True, slots=True)
class Position:
    """Kite portfolio position."""

    tradingsymbol: str
    exchange: str
    instrument_token: int | None = None
    product: str | None = None
    quantity: int | None = None
    overnight_quantity: int | None = None
    multiplier: float | None = None
    average_price: float | None = None
    close_price: float | None = None
    last_price: float | None = None
    value: float | None = None
    pnl: float | None = None
    m2m: float | None = None
    unrealised: float | None = None
    realised: float | None = None
    buy_quantity: int | None = None
    sell_quantity: int | None = None
    raw: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Parse a Kite position object."""
        tradingsymbol = data.get("tradingsymbol")
        exchange = data.get("exchange")
        return cls(
            tradingsymbol=tradingsymbol if isinstance(tradingsymbol, str) else "",
            exchange=exchange if isinstance(exchange, str) else "",
            instrument_token=optional_int(data, "instrument_token"),
            product=optional_str(data, "product"),
            quantity=optional_int(data, "quantity"),
            overnight_quantity=optional_int(data, "overnight_quantity"),
            multiplier=optional_float(data, "multiplier"),
            average_price=optional_float(data, "average_price"),
            close_price=optional_float(data, "close_price"),
            last_price=optional_float(data, "last_price"),
            value=optional_float(data, "value"),
            pnl=optional_float(data, "pnl"),
            m2m=optional_float(data, "m2m"),
            unrealised=optional_float(data, "unrealised"),
            realised=optional_float(data, "realised"),
            buy_quantity=optional_int(data, "buy_quantity"),
            sell_quantity=optional_int(data, "sell_quantity"),
            raw=data,
        )


@dataclass(frozen=True, slots=True)
class PositionsResponse:
    """Grouped positions returned by Kite."""

    net: tuple[Position, ...]
    day: tuple[Position, ...]
    raw: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Parse Kite's net/day positions response."""
        return cls(
            net=parse_positions(data.get("net")),
            day=parse_positions(data.get("day")),
            raw=data,
        )


def parse_positions(value: object) -> tuple[Position, ...]:
    """Parse a sequence of raw position mappings."""
    if not isinstance(value, list):
        return ()
    return tuple(Position.from_mapping(item) for item in value if isinstance(item, dict))


def optional_str(data: Mapping[str, Any], key: str) -> str | None:
    """Read an optional string value from response data."""
    value = data.get(key)
    return value if isinstance(value, str) and value else None
