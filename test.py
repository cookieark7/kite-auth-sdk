"""End-to-end smoke test for every kite-auto-sdk feature.

Runs each public KiteClient method against the LIVE Zerodha account, isolates each
check so one failure doesn't abort the rest, and prints a PASS/FAIL/SKIP summary.

Run:
    poetry run python test.py
    HEADLESS=false poetry run python test.py        # watch the login

Safety flags (off by default — these two are the dangerous ones):
    ENABLE_ORDER_TEST=1     places a REAL market order (real money!)
    ENABLE_STREAM_TEST=0    skip the live WebSocket stream check

NOTE: a real automated login can violate broker terms and risks 2FA lockout.
If login fails, the script ABORTS immediately so it never re-triggers login on
every subsequent call (which would burn 2FA attempts).
"""

import asyncio
import os
from datetime import datetime, timedelta

from kite_auto import KiteClient
from kite_auto.client import WebSocketMode
from kite_auto.models import CandleInterval

RELIANCE_FALLBACK_TOKEN = 738561  # used if the instrument lookup can't resolve it

results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "…", "WARN": "~"}.get(status, "?")
    line = f"{icon} {status:4}  {name}"
    print(line if not detail else f"{line} — {detail}")
    results.append((name, status, detail))


async def check(name, coro_factory, *, skip=False, skip_reason=""):
    """Await a feature call, capturing the outcome instead of raising."""
    if skip:
        record(name, "SKIP", skip_reason)
        return None
    try:
        value = await coro_factory()
    except Exception as exc:  # smoke test: report, never abort
        record(name, "FAIL", f"{type(exc).__name__}: {exc}")
        return None
    record(name, "PASS")
    return value


async def check_stream(client: KiteClient, token: int, *, seconds: float = 8.0) -> None:
    name = f"stream() [{seconds:.0f}s]"
    received: list[object] = []

    async def on_ticks(ticks) -> None:
        for tick in ticks:
            received.append(tick)
        if ticks:
            first = ticks[0]
            print(f"       tick token={first.instrument_token} ltp={first.last_price}")

    try:
        await asyncio.wait_for(
            client.stream([token], mode=WebSocketMode.FULL, on_ticks=on_ticks),
            timeout=seconds,
        )
    except TimeoutError:
        pass
    except Exception as exc:
        record(name, "FAIL", f"{type(exc).__name__}: {exc}")
        return
    finally:
        try:
            await client.websocket().close()
        except Exception as exc:
            print(f"       (websocket close error: {exc})")

    if received:
        record(name, "PASS", f"{len(received)} ticks received")
    else:
        record(name, "WARN", "WS connected but 0 ticks (market closed?)")


def print_summary() -> None:
    print("\n" + "=" * 56)
    print("  SUMMARY")
    print("=" * 56)
    counts: dict[str, int] = {}
    for name, status, detail in results:
        counts[status] = counts.get(status, 0) + 1
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "…", "WARN": "~"}.get(status, "?")
        print(f"  {icon} {status:4}  {name}" + (f"  ({detail})" if detail else ""))
    print("-" * 56)
    tally = "   ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    print(f"  {tally}")
    print("=" * 56)


async def main() -> None:
    enable_order = os.getenv("ENABLE_ORDER_TEST") == "1"
    enable_stream = os.getenv("ENABLE_STREAM_TEST", "1") != "0"

    print("kite-auto-sdk feature smoke test\n" + "-" * 56)

    async with KiteClient() as client:
        # --- Auth (must pass; abort otherwise to protect 2FA attempts) ---
        token = await check("login()", lambda: client.login())
        if not token:
            record("ABORT", "FAIL", "login failed; skipping rest to avoid re-auth/lockout")
            print_summary()
            return
        print(f"       access_token: {token[:6]}…")

        # --- Instrument master (resolve a real token to feed later checks) ---
        instruments = await check("instruments('NSE')", lambda: client.instruments("NSE"))
        reliance_token = RELIANCE_FALLBACK_TOKEN
        if instruments:
            match = next(
                (
                    i
                    for i in instruments
                    if i.tradingsymbol == "RELIANCE" and i.instrument_type == "EQ"
                ),
                None,
            )
            if match and match.instrument_token:
                reliance_token = match.instrument_token
            print(f"       {len(instruments)} instruments; RELIANCE token={reliance_token}")

        # --- Market data ---
        quote = await check("quote('NSE:RELIANCE')", lambda: client.quote("NSE:RELIANCE"))
        if quote:
            print(f"       last_price={quote.last_price} ohlc={quote.ohlc}")

        quotes = await check(
            "quote_many(['NSE:RELIANCE','NSE:INFY'])",
            lambda: client.quote_many(["NSE:RELIANCE", "NSE:INFY"]),
        )
        if quotes:
            print(f"       {len(quotes)} quotes: {list(quotes)}")

        to = datetime.now()
        frm = to - timedelta(days=10)
        candles = await check(
            "historical_data(day, 10d)",
            lambda: client.historical_data(
                reliance_token, CandleInterval.DAY, from_=frm, to=to
            ),
        )
        if candles:
            print(f"       {len(candles)} candles; latest close={candles[-1].close}")

        # --- Portfolio / orders ---
        positions = await check("positions()", lambda: client.positions())
        if positions:
            print(f"       net={len(positions.net)} day={len(positions.day)}")

        orders = await check("orders()", lambda: client.orders())
        if orders is not None:
            print(f"       {len(orders)} orders today")

        # --- Order placement (REAL money — gated off by default) ---
        await check(
            "place_order() [REAL]",
            lambda: client.place_order(
                exchange="NSE",
                tradingsymbol="INFY",
                transaction_type="BUY",
                quantity=1,
                product="CNC",
                order_type="MARKET",
            ),
            skip=not enable_order,
            skip_reason="set ENABLE_ORDER_TEST=1 to place a REAL order",
        )

        # --- Streaming (live WebSocket, time-limited) ---
        if enable_stream:
            await check_stream(client, reliance_token)
        else:
            record("stream()", "SKIP", "ENABLE_STREAM_TEST=0")

    print_summary()


if __name__ == "__main__":
    asyncio.run(main())
