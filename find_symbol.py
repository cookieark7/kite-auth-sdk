"""Throwaway: find the Kite tradingsymbol behind a name that failed to resolve.

    poetry run python find_symbol.py JBCHE "JB CHEM"

Searches the WHOLE instrument dump, not just NSE equities, and prints each hit's
instrument_type and segment — that is what separates "the symbol was renamed"
from "build_universe's filter is too narrow".

One authenticated GET against /instruments/NSE. No 2FA is involved: a valid
persisted session is reused, so this cannot cost lockout budget. Delete this file
once the universe resolves.
"""

import asyncio
import sys

from kite_auto.models.instruments import Instrument
from scanner.fetch import make_rate_limited_client
from scanner.universe import CASH_INSTRUMENT_TYPE, CASH_SEGMENT

MAX_HITS = 20


async def main(terms: list[str]) -> None:
    async with make_rate_limited_client() as client:
        await client.login()
        instruments = await client.instruments(CASH_SEGMENT)

    eligible = [
        instrument
        for instrument in instruments
        if instrument.instrument_type == CASH_INSTRUMENT_TYPE
        and instrument.segment == CASH_SEGMENT
    ]
    print(f"{len(instruments)} instruments in the dump, {len(eligible)} of them NSE EQ\n")

    for term in terms:
        hits = [instrument for instrument in instruments if matches(instrument, term)]
        print(f"{term!r} -> {len(hits)} hit(s)")
        for instrument in hits[:MAX_HITS]:
            usable = "OK " if instrument in eligible else "   "
            print(
                f"  {usable} {instrument.tradingsymbol:<22} "
                f"{instrument.instrument_type or '?':<6} {instrument.segment or '?':<10} "
                f"{instrument.name or ''}"
            )
        if len(hits) > MAX_HITS:
            print(f"  ... {len(hits) - MAX_HITS} more")
        print()


def matches(instrument: Instrument, term: str) -> bool:
    needle = term.upper()
    return needle in instrument.tradingsymbol.upper() or needle in (instrument.name or "").upper()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('usage: python find_symbol.py TERM [TERM ...]   e.g. JBCHE "JB CHEM"')
    asyncio.run(main(sys.argv[1:]))
