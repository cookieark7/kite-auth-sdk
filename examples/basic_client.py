"""Minimal public API sketch for future SDK usage."""

import asyncio

from kite_auto import KiteClient


async def main() -> None:
    client = KiteClient()
    await client.login()


if __name__ == "__main__":
    asyncio.run(main())

