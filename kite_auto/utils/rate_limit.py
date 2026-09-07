"""Rate limiting and duplicate-order prevention."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from kite_auto.exceptions import DuplicateOrderError

type Sleep = Callable[[float], Awaitable[None]]


class RateLimiter:
    """Async token-bucket rate limiter."""

    def __init__(
        self,
        *,
        rate: float = 3.0,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be greater than 0")
        if capacity is not None and capacity <= 0:
            raise ValueError("capacity must be greater than 0")

        self._rate = rate
        self._capacity = capacity or rate
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._updated_at = clock()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        """Wait until the bucket can admit the requested cost."""
        if cost <= 0:
            raise ValueError("cost must be greater than 0")
        if cost > self._capacity:
            raise ValueError("cost cannot exceed bucket capacity")

        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                missing = cost - self._tokens
                wait_time = missing / self._rate

            await self._sleep(wait_time)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(now - self._updated_at, 0)
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._updated_at = now


@dataclass(frozen=True, slots=True)
class OrderFingerprint:
    """Stable duplicate-order fingerprint."""

    value: str


class DuplicateOrderGuard:
    """Prevent duplicate order submissions inside a configurable time window."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than 0")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, order: Mapping[str, Any]) -> OrderFingerprint:
        """Reserve an order fingerprint or raise when it is a recent duplicate."""
        fingerprint = fingerprint_order(order)
        async with self._lock:
            self._purge_expired()
            if fingerprint.value in self._seen:
                raise DuplicateOrderError("Duplicate order blocked by idempotency guard.")
            self._seen[fingerprint.value] = self._clock()
        return fingerprint

    async def release(self, fingerprint: OrderFingerprint) -> None:
        """Release a reservation, allowing the same order to be submitted again."""
        async with self._lock:
            self._seen.pop(fingerprint.value, None)

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [
            fingerprint
            for fingerprint, created_at in self._seen.items()
            if now - created_at >= self._ttl_seconds
        ]
        for fingerprint in expired:
            self._seen.pop(fingerprint, None)


def fingerprint_order(order: Mapping[str, Any]) -> OrderFingerprint:
    """Build a stable fingerprint for an order payload."""
    canonical = json.dumps(
        normalize_order_payload(order),
        sort_keys=True,
        separators=(",", ":"),
    )
    return OrderFingerprint(hashlib.sha256(canonical.encode()).hexdigest())


def normalize_order_payload(value: Any) -> Any:
    """Normalize order payload values for stable JSON hashing."""
    if isinstance(value, Mapping):
        return {str(key): normalize_order_payload(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [normalize_order_payload(item) for item in value]
    return value
