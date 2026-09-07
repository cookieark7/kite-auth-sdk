"""Tests for instrument-master CSV parsing edge cases."""

from datetime import date

from kite_auto.models import Instrument
from kite_auto.models.instruments import parse_instruments_csv


def test_instrument_from_row_handles_blank_and_malformed_cells() -> None:
    instrument = Instrument.from_row(
        {
            "instrument_token": "",
            "exchange_token": "not-a-number",
            "tradingsymbol": " INFY ",
            "name": "",
            "last_price": "abc",
            "expiry": "31-12-2026",  # wrong format
            "strike": "100.5",
            "tick_size": "0.05",
            "lot_size": "1.0",  # float-as-int
            "instrument_type": "EQ",
        }
    )

    assert instrument.instrument_token is None
    assert instrument.exchange_token is None
    assert instrument.tradingsymbol == "INFY"  # stripped
    assert instrument.name is None
    assert instrument.last_price is None
    assert instrument.expiry is None
    assert instrument.strike == 100.5
    assert instrument.lot_size == 1  # 1.0 -> 1
    assert instrument.segment is None  # missing column


def test_parse_instruments_csv_round_trip() -> None:
    csv_text = (
        "instrument_token,tradingsymbol,expiry,strike,lot_size,exchange\n"
        "256265,NIFTY26JUN24000CE,2026-06-25,24000,50,NFO\n"
    )
    instruments = parse_instruments_csv(csv_text)

    assert len(instruments) == 1
    assert instruments[0].instrument_token == 256265
    assert instruments[0].expiry == date(2026, 6, 25)
    assert instruments[0].strike == 24000.0
    assert instruments[0].raw is not None


def test_parse_instruments_csv_empty_returns_empty_tuple() -> None:
    assert parse_instruments_csv("") == ()
