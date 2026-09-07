"""Step 3 — symbol -> instrument_token index.

SDK_GUIDE §6.2 is explicit that ``instruments()`` returns parsed rows and that
indexing, caching and refresh belong to the application.
"""

from typing import Protocol

import pandas as pd

from kite_auto.models.instruments import Instrument
from scanner.config import UNIVERSE_CACHE_FILE, UNIVERSE_SYMBOLS_FILE

CASH_INSTRUMENT_TYPE = "EQ"
CASH_SEGMENT = "NSE"


class InstrumentSource(Protocol):
    """The one SDK method this module needs."""

    async def instruments(self, exchange: str | None = None) -> tuple[Instrument, ...]:
        """Return the parsed instrument master."""


async def build_universe(client: InstrumentSource) -> pd.DataFrame:
    """Resolve ``data/universe_symbols.txt`` to instrument tokens.

    Raises listing every symbol that failed to resolve. A silently dropped symbol
    is a silently smaller universe, which nothing downstream would ever notice.
    """
    symbols = _read_symbols()
    instruments = await client.instruments(CASH_SEGMENT)

    index = {
        instrument.tradingsymbol: instrument.instrument_token
        for instrument in instruments
        if instrument.instrument_type == CASH_INSTRUMENT_TYPE
        and instrument.segment == CASH_SEGMENT
        and instrument.instrument_token is not None
    }

    unresolved = [symbol for symbol in symbols if symbol not in index]
    if unresolved:
        raise ValueError(
            f"{len(unresolved)} of {len(symbols)} symbols did not resolve to an "
            f"NSE {CASH_INSTRUMENT_TYPE} instrument_token: {', '.join(unresolved)}"
        )

    universe = pd.DataFrame(
        {
            "tradingsymbol": symbols,
            "instrument_token": [index[symbol] for symbol in symbols],
        }
    )
    UNIVERSE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    universe.to_parquet(UNIVERSE_CACHE_FILE, index=False)
    return universe


def _read_symbols() -> list[str]:
    if not UNIVERSE_SYMBOLS_FILE.exists():
        raise FileNotFoundError(
            f"{UNIVERSE_SYMBOLS_FILE} is missing — it holds one NSE tradingsymbol per line."
        )

    symbols: list[str] = []
    seen: set[str] = set()
    for line in UNIVERSE_SYMBOLS_FILE.read_text().splitlines():
        symbol = line.strip().upper()
        if not symbol or symbol.startswith("#") or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)

    if not symbols:
        raise ValueError(f"{UNIVERSE_SYMBOLS_FILE} contains no symbols.")
    return symbols
