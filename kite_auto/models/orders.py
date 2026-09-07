"""Order models."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

from kite_auto.models.market import optional_datetime, optional_float, optional_int
from kite_auto.models.positions import optional_str


@dataclass(frozen=True, slots=True)
class Order:
    """Kite order summary."""

    order_id: str
    tradingsymbol: str | None = None
    exchange: str | None = None
    status: str | None = None
    transaction_type: str | None = None
    order_type: str | None = None
    product: str | None = None
    quantity: int | None = None
    filled_quantity: int | None = None
    pending_quantity: int | None = None
    price: float | None = None
    average_price: float | None = None
    order_timestamp: datetime | None = None
    raw: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Parse a Kite order object."""
        order_id = data.get("order_id")
        return cls(
            order_id=order_id if isinstance(order_id, str) else "",
            tradingsymbol=optional_str(data, "tradingsymbol"),
            exchange=optional_str(data, "exchange"),
            status=optional_str(data, "status"),
            transaction_type=optional_str(data, "transaction_type"),
            order_type=optional_str(data, "order_type"),
            product=optional_str(data, "product"),
            quantity=optional_int(data, "quantity"),
            filled_quantity=optional_int(data, "filled_quantity"),
            pending_quantity=optional_int(data, "pending_quantity"),
            price=optional_float(data, "price"),
            average_price=optional_float(data, "average_price"),
            order_timestamp=optional_datetime(data, "order_timestamp"),
            raw=data,
        )
