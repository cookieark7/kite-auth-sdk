"""Tests for historical candle models and parsing."""

from datetime import datetime

from kite_auto.models import Candle, CandleInterval
from kite_auto.models.historical import format_candle_time, parse_candle_timestamp


def test_candle_from_full_row_with_oi() -> None:
    candle = Candle.from_sequence(
        ["2026-06-26T09:15:00+05:30", 2975.0, 2992.8, 2968.1, 2975.05, 4821233, 12500]
    )

    assert candle.timestamp == datetime.fromisoformat("2026-06-26T09:15:00+05:30")
    assert candle.open == 2975.0
    assert candle.high == 2992.8
    assert candle.low == 2968.1
    assert candle.close == 2975.05
    assert candle.volume == 4821233
    assert candle.oi == 12500


def test_candle_without_oi_leaves_oi_none() -> None:
    candle = Candle.from_sequence(["2026-06-26T09:15:00+0530", 1, 2, 0, 1.5, 100])

    assert candle.oi is None
    assert candle.volume == 100
    assert candle.close == 1.5


def test_candle_tolerates_short_and_malformed_rows() -> None:
    candle = Candle.from_sequence([])

    assert candle.timestamp is None
    assert candle.open is None
    assert candle.volume is None
    assert candle.raw == ()


def test_parse_candle_timestamp_handles_space_and_passthrough() -> None:
    assert parse_candle_timestamp("2026-06-26 09:15:00") == datetime(2026, 6, 26, 9, 15, 0)
    assert parse_candle_timestamp("not-a-time") is None
    assert parse_candle_timestamp(None) is None
    existing = datetime(2026, 6, 26, 9, 15, 0)
    assert parse_candle_timestamp(existing) is existing


def test_format_candle_time() -> None:
    assert format_candle_time(datetime(2026, 6, 26, 9, 15, 0)) == "2026-06-26 09:15:00"
    assert format_candle_time("2026-06-26 09:15:00") == "2026-06-26 09:15:00"


def test_candle_interval_values() -> None:
    assert CandleInterval.MINUTE_5.value == "5minute"
    assert CandleInterval.DAY.value == "day"
