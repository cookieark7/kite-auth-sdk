"""Step 3 — symbol -> instrument_token index (SDK_GUIDE §6.2: the app's job)."""

from pathlib import Path

import pytest

from kite_auto.models.instruments import Instrument
from scanner.universe import build_universe


class StubInstruments:
    def __init__(self, instruments: tuple[Instrument, ...]) -> None:
        self._instruments = instruments
        self.exchanges: list[str | None] = []

    async def instruments(self, exchange: str | None = None) -> tuple[Instrument, ...]:
        self.exchanges.append(exchange)
        return self._instruments


def instrument(
    tradingsymbol: str,
    token: int,
    *,
    instrument_type: str = "EQ",
    segment: str = "NSE",
) -> Instrument:
    return Instrument(
        instrument_token=token,
        exchange_token=token // 256,
        tradingsymbol=tradingsymbol,
        instrument_type=instrument_type,
        segment=segment,
        exchange="NSE",
    )


def write_symbols(root: Path, symbols: list[str]) -> None:
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "universe_symbols.txt").write_text("\n".join(symbols) + "\n")


async def test_resolves_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_symbols(tmp_path, ["RELIANCE", "INFY"])
    client = StubInstruments((instrument("RELIANCE", 738561), instrument("INFY", 408065)))

    universe = await build_universe(client)

    assert client.exchanges == ["NSE"]
    assert dict(zip(universe["tradingsymbol"], universe["instrument_token"], strict=True)) == {
        "RELIANCE": 738561,
        "INFY": 408065,
    }
    assert (tmp_path / "data" / "universe.parquet").exists()


async def test_filters_eq_nse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_symbols(tmp_path, ["RELIANCE"])
    client = StubInstruments(
        (
            instrument("RELIANCE", 1, instrument_type="FUT", segment="NFO-FUT"),
            instrument("RELIANCE", 2, instrument_type="EQ", segment="BSE"),
            instrument("RELIANCE", 3, instrument_type="EQ", segment="NSE"),
        )
    )

    universe = await build_universe(client)

    assert universe["instrument_token"].tolist() == [3]


async def test_unresolved_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_symbols(tmp_path, ["RELIANCE", "NOTLISTED"])
    client = StubInstruments((instrument("RELIANCE", 738561),))

    with pytest.raises(ValueError, match="NOTLISTED"):
        await build_universe(client)


async def test_missing_symbols_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    client = StubInstruments((instrument("RELIANCE", 738561),))

    with pytest.raises(FileNotFoundError, match="universe_symbols.txt"):
        await build_universe(client)
