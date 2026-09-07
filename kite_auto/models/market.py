"""Market data models."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self


@dataclass(frozen=True, slots=True)
class OHLC:
    """Open-high-low-close price snapshot."""

    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> Self:
        """Parse OHLC values from Kite response data."""
        if data is None:
            return cls()
        return cls(
            open=optional_float(data, "open"),
            high=optional_float(data, "high"),
            low=optional_float(data, "low"),
            close=optional_float(data, "close"),
        )


@dataclass(frozen=True, slots=True)
class Quote:
    """Full market quote for one instrument."""

    instrument: str
    instrument_token: int | None = None
    timestamp: datetime | None = None
    last_trade_time: datetime | None = None
    last_price: float | None = None
    last_quantity: int | None = None
    buy_quantity: int | None = None
    sell_quantity: int | None = None
    volume: int | None = None
    average_price: float | None = None
    oi: float | None = None
    net_change: float | None = None
    lower_circuit_limit: float | None = None
    upper_circuit_limit: float | None = None
    ohlc: OHLC | None = None
    raw: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, instrument: str, data: Mapping[str, Any]) -> Self:
        """Parse a full Kite quote response."""
        ohlc_data = data.get("ohlc")
        return cls(
            instrument=instrument,
            instrument_token=optional_int(data, "instrument_token"),
            timestamp=optional_datetime(data, "timestamp"),
            last_trade_time=optional_datetime(data, "last_trade_time"),
            last_price=optional_float(data, "last_price"),
            last_quantity=optional_int(data, "last_quantity"),
            buy_quantity=optional_int(data, "buy_quantity"),
            sell_quantity=optional_int(data, "sell_quantity"),
            volume=optional_int(data, "volume"),
            average_price=optional_float(data, "average_price"),
            oi=optional_float(data, "oi"),
            net_change=optional_float(data, "net_change"),
            lower_circuit_limit=optional_float(data, "lower_circuit_limit"),
            upper_circuit_limit=optional_float(data, "upper_circuit_limit"),
            ohlc=OHLC.from_mapping(ohlc_data) if isinstance(ohlc_data, dict) else None,
            raw=data,
        )


def optional_int(data: Mapping[str, Any], key: str) -> int | None:
    """Read an optional integer value from response data."""
    value = data.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def optional_float(data: Mapping[str, Any], key: str) -> float | None:
    """Read an optional numeric value from response data."""
    value = data.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def optional_datetime(data: Mapping[str, Any], key: str) -> datetime | None:
    """Read an optional Kite datetime string."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        return None
    for candidate in (value, value.replace(" ", "T")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None
