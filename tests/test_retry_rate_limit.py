"""Tests for retry, circuit-breaker, rate-limit, and order guards."""

import pytest

from kite_auto.exceptions import CircuitBreakerOpenError, DuplicateOrderError
from kite_auto.utils.rate_limit import DuplicateOrderGuard, RateLimiter, fingerprint_order
from kite_auto.utils.retry import AsyncRetryPolicy, CircuitBreaker, CircuitState, RetryConfig


@pytest.mark.asyncio
async def test_async_retry_policy_retries_matching_exception() -> None:
    calls = 0
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("temporary")
        return "ok"

    policy = AsyncRetryPolicy(
        RetryConfig(max_attempts=3, initial_delay=0.1, multiplier=2, jitter=0),
        sleep=sleep,
    )

    result = await policy.execute(operation, retry_if=lambda exc: isinstance(exc, TimeoutError))

    assert result == "ok"
    assert calls == 3
    assert sleeps == [0.1, 0.2]


@pytest.mark.asyncio
async def test_circuit_breaker_opens_and_recovers() -> None:
    now = 0.0

    def clock() -> float:
        return now

    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=10, clock=clock)

    async def failing() -> None:
        raise TimeoutError("down")

    with pytest.raises(TimeoutError):
        await breaker.call(failing)
    with pytest.raises(TimeoutError):
        await breaker.call(failing)

    assert breaker.state is CircuitState.OPEN

    with pytest.raises(CircuitBreakerOpenError):
        await breaker.call(lambda: successful("blocked"))

    now = 11.0
    result = await breaker.call(lambda: successful("ok"))

    assert result == "ok"
    assert breaker.failure_count == 0


@pytest.mark.asyncio
async def test_rate_limiter_waits_for_tokens() -> None:
    now = 0.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    async def sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    limiter = RateLimiter(rate=2, capacity=2, clock=clock, sleep=sleep)

    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()

    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_duplicate_order_guard_blocks_until_ttl_expires() -> None:
    now = 0.0

    def clock() -> float:
        return now

    guard = DuplicateOrderGuard(ttl_seconds=10, clock=clock)
    order = {"exchange": "NSE", "tradingsymbol": "INFY", "quantity": 1}

    first = await guard.reserve(order)

    with pytest.raises(DuplicateOrderError):
        await guard.reserve({"quantity": 1, "tradingsymbol": "INFY", "exchange": "NSE"})

    now = 11.0
    second = await guard.reserve(order)

    assert second == first


def test_fingerprint_order_is_stable() -> None:
    first = fingerprint_order({"a": 1, "b": {"c": 2}})
    second = fingerprint_order({"b": {"c": 2}, "a": 1})

    assert first == second


async def successful(value: str) -> str:
    return value
