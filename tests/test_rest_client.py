"""Tests for authenticated REST transport."""

from collections.abc import Mapping

import httpx
import pytest

from kite_auto.client.rest_client import RestClient
from kite_auto.exceptions import ConfigurationError, KiteApiError
from kite_auto.utils.rate_limit import RateLimiter
from kite_auto.utils.retry import AsyncRetryPolicy, CircuitBreaker, RetryConfig

TEST_API_KEY = "api-key"
TEST_ACCESS_TOKEN = "access-token"  # noqa: S105
TEST_REFRESHED_TOKEN = "refreshed-token"  # noqa: S105


@pytest.mark.asyncio
async def test_rest_client_injects_auth_headers_and_returns_data() -> None:
    captured_headers: Mapping[str, str] | None = None

    async def token_provider(*, force_refresh: bool = False) -> str:
        assert force_refresh is False
        return TEST_ACCESS_TOKEN

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_headers
        captured_headers = request.headers
        return httpx.Response(200, json={"status": "success", "data": {"ok": True}})

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = RestClient(
            api_key=TEST_API_KEY,
            access_token_provider=token_provider,
            http_client=http_client,
        )

        data = await client.get("/portfolio/positions")

    assert data == {"ok": True}
    assert captured_headers is not None
    assert captured_headers["Authorization"] == f"token {TEST_API_KEY}:{TEST_ACCESS_TOKEN}"
    assert captured_headers["X-Kite-Version"] == "3"


@pytest.mark.asyncio
async def test_rest_client_refreshes_once_on_auth_failure() -> None:
    calls = 0
    refresh_flags: list[bool] = []

    async def token_provider(*, force_refresh: bool = False) -> str:
        refresh_flags.append(force_refresh)
        return TEST_REFRESHED_TOKEN if force_refresh else TEST_ACCESS_TOKEN

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(403, json={"status": "error", "message": "Token expired"})
        return httpx.Response(200, json={"status": "success", "data": {"ok": True}})

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = RestClient(
            api_key=TEST_API_KEY,
            access_token_provider=token_provider,
            http_client=http_client,
        )

        data = await client.get("/quote", params=[("i", "NSE:INFY")])

    assert data == {"ok": True}
    assert calls == 2
    assert refresh_flags == [False, True]


@pytest.mark.asyncio
async def test_rest_client_raises_kite_api_error() -> None:
    async def token_provider(*, force_refresh: bool = False) -> str:
        return TEST_ACCESS_TOKEN

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": "error", "message": "Bad request"})

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = RestClient(
            api_key=TEST_API_KEY,
            access_token_provider=token_provider,
            http_client=http_client,
        )

        with pytest.raises(KiteApiError, match="Bad request"):
            await client.get("/quote")


@pytest.mark.asyncio
async def test_rest_client_requires_api_key_and_token_provider() -> None:
    client = RestClient()

    with pytest.raises(ConfigurationError, match="KITE_API_KEY"):
        await client.get("/quote")

    client = RestClient(api_key=TEST_API_KEY)

    with pytest.raises(ConfigurationError, match="access_token_provider"):
        await client.get("/quote")


@pytest.mark.asyncio
async def test_rest_client_raises_for_invalid_json() -> None:
    async def token_provider(*, force_refresh: bool = False) -> str:
        return TEST_ACCESS_TOKEN

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = RestClient(
            api_key=TEST_API_KEY,
            access_token_provider=token_provider,
            http_client=http_client,
        )

        with pytest.raises(KiteApiError, match="valid JSON"):
            await client.get("/quote")


def test_rest_client_response_parser_requires_data() -> None:
    response = httpx.Response(200, json={"status": "success"})

    with pytest.raises(KiteApiError, match="data"):
        RestClient._parse_response(response)


@pytest.mark.asyncio
async def test_rest_client_retries_retryable_get_status() -> None:
    calls = 0
    sleeps: list[float] = []

    async def token_provider(*, force_refresh: bool = False) -> str:
        return TEST_ACCESS_TOKEN

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"status": "error", "message": "temporary"})
        return httpx.Response(200, json={"status": "success", "data": {"ok": True}})

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = RestClient(
            api_key=TEST_API_KEY,
            access_token_provider=token_provider,
            http_client=http_client,
            retry_policy=AsyncRetryPolicy(
                RetryConfig(max_attempts=2, initial_delay=0, jitter=0),
                sleep=sleep,
            ),
        )

        data = await client.get("/quote")

    assert data == {"ok": True}
    assert calls == 2
    assert sleeps == [0]


@pytest.mark.asyncio
async def test_rest_client_does_not_retry_post_by_default() -> None:
    calls = 0

    async def token_provider(*, force_refresh: bool = False) -> str:
        return TEST_ACCESS_TOKEN

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"status": "error", "message": "temporary"})

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = RestClient(
            api_key=TEST_API_KEY,
            access_token_provider=token_provider,
            http_client=http_client,
            retry_policy=AsyncRetryPolicy(RetryConfig(max_attempts=2, initial_delay=0)),
        )

        with pytest.raises(KiteApiError, match="temporary"):
            await client.post("/orders/regular", data={"tradingsymbol": "INFY"})

    assert calls == 1


@pytest.mark.asyncio
async def test_rest_client_uses_rate_limiter_and_circuit_breaker() -> None:
    now = 0.0
    sleeps: list[float] = []
    calls = 0

    def clock() -> float:
        return now

    async def sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    async def token_provider(*, force_refresh: bool = False) -> str:
        return TEST_ACCESS_TOKEN

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": "success", "data": {"ok": True}})

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = RestClient(
            api_key=TEST_API_KEY,
            access_token_provider=token_provider,
            http_client=http_client,
            rate_limiter=RateLimiter(rate=1, capacity=1, clock=clock, sleep=sleep),
            circuit_breaker=CircuitBreaker(failure_threshold=1, recovery_timeout=1),
        )

        await client.get("/quote")
        await client.get("/quote")

    assert calls == 2
    assert sleeps == [1.0]
