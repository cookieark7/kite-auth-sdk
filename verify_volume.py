"""Step 0 — is Kite's 5-minute bar `volume` per-bar or cumulative?

⚠️ ONE LIVE CALL. This is a real broker session. Run it deliberately, once, on a
known trading day, then delete this file:

    poetry run python verify_volume.py 2026-07-31

If the answer is "cumulative", every RVOL number downstream is meaningless and
the project stops here.
"""

import asyncio
import sys
from datetime import date, datetime

from kite_auto.models import CandleInterval
from scanner.config import IST, SESSION_END, SESSION_START
from scanner.fetch import make_rate_limited_client

RELIANCE_TOKEN = 738561
TOLERANCE = 0.01


async def main(day: date) -> None:
    open_at = datetime.combine(day, SESSION_START, tzinfo=IST)
    close_at = datetime.combine(day, SESSION_END, tzinfo=IST)

    async with make_rate_limited_client() as client:
        await client.login()
        bars = await client.historical_data(
            RELIANCE_TOKEN, CandleInterval.MINUTE_5, from_=open_at, to=close_at
        )
        days = await client.historical_data(
            RELIANCE_TOKEN, CandleInterval.DAY, from_=open_at, to=close_at
        )

    if not bars or not days:
        raise SystemExit(f"No RELIANCE data for {day} — is it a trading day?")

    bar_sum = sum(bar.volume or 0 for bar in bars)
    last_bar = bars[-1].volume or 0
    day_total = days[0].volume or 0

    print(f"5-minute bars:        {len(bars)}")
    print(f"sum of bar volumes:   {bar_sum:,}")
    print(f"last bar volume:      {last_bar:,}")
    print(f"day candle volume:    {day_total:,}")

    if matches(bar_sum, day_total):
        print("VERDICT: per-bar. Correct. Proceed.")
    elif matches(last_bar, day_total):
        print("VERDICT: CUMULATIVE. Every RVOL downstream is meaningless. STOP.")
    else:
        print("VERDICT: neither matches the day total. Investigate before proceeding.")


def matches(value: int, day_total: int) -> bool:
    return day_total > 0 and abs(value - day_total) / day_total <= TOLERANCE


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python verify_volume.py YYYY-MM-DD   (a known trading day)")
    asyncio.run(main(date.fromisoformat(sys.argv[1])))
