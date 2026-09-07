"""Step 2 — fetch adapter over the SDK."""

from datetime import date, datetime
from itertools import pairwise
from pathlib import Path

import pandas as pd
import pytest

from kite_auto.client.rest_client import RestClient
from kite_auto.models import CandleInterval
from kite_auto.models.historical import Candle
from scanner.config import IST, MAX_DAYS_PER_REQUEST
from scanner.fetch import (
    candles_to_frame,
    date_chunks,
    fetch_symbol,
    fetch_universe,
    make_rate_limited_client,
)


class StubHistorical:
    """Returns a pre-canned batch of candles per call, recording the bounds asked for."""

    def __init__(self, batches: list[tuple[Candle, ...]]) -> None:
        self._batches = batches
        self.calls: list[tuple[int, str, str]] = []

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
        self.calls.append((instrument_token, str(from_), str(to)))
        return self._batches[len(self.calls) - 1]


def candle(hour: int, minute: int, *, close: float = 100.0, volume: int = 500) -> Candle:
    stamp = datetime(2026, 6, 26, hour, minute, tzinfo=IST)
    return Candle(
        timestamp=stamp,
        open=close - 1.0,
        high=close + 2.0,
        low=close - 3.0,
        close=close,
        volume=volume,
    )


def test_rate_limiter_injected() -> None:
    client = make_rate_limited_client()

    rest_client = client._rest_client
    assert isinstance(rest_client, RestClient)
    assert rest_client._rate_limiter is not None


def test_date_chunks() -> None:
    chunks = date_chunks(date(2025, 1, 1), date(2025, 12, 31))

    assert len(chunks) == 4
    assert all((end - start).days + 1 <= MAX_DAYS_PER_REQUEST for start, end in chunks)
    assert chunks[0][0] == date(2025, 1, 1)
    assert chunks[-1][1] == date(2025, 12, 31)
    assert all(later[0] > earlier[1] for earlier, later in pairwise(chunks))

    assert date_chunks(date(2025, 1, 1), date(2025, 1, 30)) == [
        (date(2025, 1, 1), date(2025, 1, 30))
    ]


def test_candles_to_frame() -> None:
    frame = candles_to_frame(
        (
            candle(9, 15, close=101.0, volume=500),
            candle(9, 20, close=102.5, volume=700),
        )
    )

    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert frame["close"].tolist() == [101.0, 102.5]
    assert frame["volume"].tolist() == [500, 700]
    assert frame["timestamp"].dt.tz is not None
    assert frame["timestamp"].dt.hour.tolist() == [9, 9]
    assert frame["timestamp"].dt.minute.tolist() == [15, 20]


def test_candle_none_raises() -> None:
    broken = Candle(
        timestamp=datetime(2026, 6, 26, 9, 15, tzinfo=IST),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=None,
    )

    with pytest.raises(ValueError, match="volume"):
        candles_to_frame((broken,))


async def test_chunk_boundary_dedup() -> None:
    overlap = candle(9, 20)
    client = StubHistorical(
        [
            (candle(9, 15), overlap),
            (overlap, candle(9, 25)),
        ]
    )

    frame = await fetch_symbol(client, 738561, date(2026, 1, 1), date(2026, 6, 30))

    assert len(client.calls) == 2
    assert frame["timestamp"].dt.minute.tolist() == [15, 20, 25]


async def test_fetch_universe_skips_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    candles_to_frame((candle(9, 15),)).to_parquet(raw_dir / "DONE.parquet")

    client = StubHistorical([(candle(9, 15), candle(9, 20))])
    universe = pd.DataFrame(
        {"tradingsymbol": ["DONE", "TODO"], "instrument_token": [1, 2]},
    )

    await fetch_universe(client, universe, date(2026, 1, 1), date(2026, 1, 31))

    assert [call[0] for call in client.calls] == [2]
    assert (raw_dir / "TODO.parquet").exists()
