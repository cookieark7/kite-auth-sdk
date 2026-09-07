"""Retry and circuit-breaker infrastructure."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from kite_auto.exceptions import CircuitBreakerOpenError

type AsyncCallable[T] = Callable[[], Awaitable[T]]
type Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Configuration for exponential-backoff retries."""

    max_attempts: int = 3
    initial_delay: float = 0.25
    max_delay: float = 2.0
    multiplier: float = 2.0
    jitter: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_delay < 0:
            raise ValueError("initial_delay cannot be negative")
        if self.max_delay < 0:
            raise ValueError("max_delay cannot be negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between 0 and 1")

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the sleep delay before the next attempt."""
        base_delay = min(
            self.initial_delay * (self.multiplier ** max(attempt - 1, 0)),
            self.max_delay,
        )
        if self.jitter == 0 or base_delay == 0:
            return base_delay
        spread = base_delay * self.jitter
        return random.uniform(max(base_delay - spread, 0), base_delay + spread)  # noqa: S311


class AsyncRetryPolicy:
    """Retry async operations with exponential backoff."""

    def __init__(
        self,
        config: RetryConfig | None = None,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._config = config or RetryConfig()
        self._sleep = sleep

    @property
    def config(self) -> RetryConfig:
        """Return retry configuration."""
        return self._config

    async def execute[T](
        self,
        operation: AsyncCallable[T],
        *,
        retry_if: Callable[[BaseException], bool],
    ) -> T:
        """Execute an operation and retry matching exceptions."""
        attempt = 1
        while True:
            try:
                return await operation()
            except BaseException as exc:
                if attempt >= self._config.max_attempts or not retry_if(exc):
                    raise
                await self._sleep(self._config.delay_for_attempt(attempt))
                attempt += 1


def retry_async[T](
    func: Callable[..., Awaitable[T]],
    *,
    config: RetryConfig | None = None,
    retry_if: Callable[[BaseException], bool] | None = None,
    sleep: Sleep = asyncio.sleep,
) -> Callable[..., Awaitable[T]]:
    """Decorate an async function with retry behavior."""
    policy = AsyncRetryPolicy(config, sleep=sleep)

    async def wrapper(*args: Any, **kwargs: Any) -> T:
        return await policy.execute(
            lambda: func(*args, **kwargs),
            retry_if=retry_if or (lambda _exc: True),
        )

    return wrapper


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Small async circuit breaker for outbound operations."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if recovery_timeout < 0:
            raise ValueError("recovery_timeout cannot be negative")

        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        """Return the current circuit state."""
        if self._state is CircuitState.OPEN and self._can_probe():
            return CircuitState.HALF_OPEN
        return self._state

    @property
    def failure_count(self) -> int:
        """Return consecutive failure count."""
        return self._failure_count

    async def call[T](
        self,
        operation: AsyncCallable[T],
        *,
        record_failure: Callable[[BaseException], bool] | None = None,
    ) -> T:
        """Execute operation if the circuit allows it."""
        if self._state is CircuitState.OPEN:
            if not self._can_probe():
                raise CircuitBreakerOpenError("Circuit breaker is open.")
            self._state = CircuitState.HALF_OPEN

        try:
            result = await operation()
        except BaseException as exc:
            if record_failure is None or record_failure(exc):
                self._record_failure()
            raise

        self._record_success()
        return result

    def _record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = self._clock()

    def _record_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None

    def _can_probe(self) -> bool:
        return (
            self._opened_at is not None
            and self._clock() - self._opened_at >= self._recovery_timeout
        )
