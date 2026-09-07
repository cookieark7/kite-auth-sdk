"""Instrument master (tradable contract) models.

Kite serves the instrument dump as CSV, not JSON. This module parses a row into a
typed :class:`Instrument`. Indexing, caching, and refresh are the application's
concern, not the SDK's — this is a thin one-shot parse.
"""

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from io import StringIO
from typing import Self


@dataclass(frozen=True, slots=True)
class Instrument:
    """A single tradable instrument from the Kite instrument master."""

    instrument_token: int | None
    exchange_token: int | None
    tradingsymbol: str
    name: str | None = None
    last_price: float | None = None
    expiry: date | None = None
    strike: float | None = None
    tick_size: float | None = None
    lot_size: int | None = None
    instrument_type: str | None = None
    segment: str | None = None
    exchange: str | None = None
    raw: Mapping[str, str] | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, str]) -> Self:
        """Parse one CSV row (as produced by ``csv.DictReader``)."""
        return cls(
            instrument_token=_csv_int(row.get("instrument_token")),
            exchange_token=_csv_int(row.get("exchange_token")),
            tradingsymbol=(row.get("tradingsymbol") or "").strip(),
            name=_csv_str(row.get("name")),
            last_price=_csv_float(row.get("last_price")),
            expiry=_csv_date(row.get("expiry")),
            strike=_csv_float(row.get("strike")),
            tick_size=_csv_float(row.get("tick_size")),
            lot_size=_csv_int(row.get("lot_size")),
            instrument_type=_csv_str(row.get("instrument_type")),
            segment=_csv_str(row.get("segment")),
            exchange=_csv_str(row.get("exchange")),
            raw=dict(row),
        )


def parse_instruments_csv(text: str) -> tuple[Instrument, ...]:
    """Parse the Kite instruments CSV dump into typed instruments."""
    reader = csv.DictReader(StringIO(text))
    return tuple(Instrument.from_row(row) for row in reader)


def _csv_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _csv_int(value: str | None) -> int | None:
    text = _csv_str(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            number = float(text)
        except ValueError:
            return None
        return int(number) if number.is_integer() else None


def _csv_float(value: str | None) -> float | None:
    text = _csv_str(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _csv_date(value: str | None) -> date | None:
    text = _csv_str(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
