"""Tests for Kite access-token exchange."""

from datetime import datetime
from urllib.parse import parse_qs

import httpx
import pytest

from kite_auto.auth.checksum import generate_checksum
from kite_auto.auth.token_manager import (
    SESSION_TOKEN_PATH,
    AccessTokenResponse,
    TokenExchangeClient,
    TokenManager,
)
from kite_auto.exceptions import ConfigurationError, TokenExchangeError

TEST_API_KEY = "api-key"
TEST_API_SECRET = "api-secret"  # noqa: S105
TEST_REQUEST_TOKEN = "request-token"  # noqa: S105
TEST_ACCESS_TOKEN = "access-token"  # noqa: S105


def test_generate_checksum() -> None:
    checksum = generate_checksum(TEST_API_KEY, TEST_REQUEST_TOKEN, TEST_API_SECRET)

    assert checksum == "d93f7cb933c3518b3a5f87fa0b49ff6bc71de987dc59bc8015b296920b762fd0"


@pytest.mark.asyncio
async def test_exchange_request_token_posts_checksum_and_parses_response() -> None:
    captured_body = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_body
        captured_body = request.content.decode()
        assert request.url.path == SESSION_TOKEN_PATH
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "access_token": "access-token",
                    "public_token": "public-token",
                    "refresh_token": "",
                    "user_id": "AB1234",
                    "user_name": "Ada Lovelace",
                    "user_shortname": "Ada",
                    "email": "ada@example.com",
                    "broker": "ZERODHA",
                    "login_time": "2026-05-19T09:30:00",
                },
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = TokenExchangeClient(
            api_key=TEST_API_KEY,
            api_secret=TEST_API_SECRET,
            http_client=http_client,
            max_attempts=1,
        )

        response = await client.exchange_request_token(TEST_REQUEST_TOKEN)

    posted = parse_qs(captured_body)
    assert posted == {
        "api_key": ["api-key"],
        "request_token": ["request-token"],
        "checksum": ["d93f7cb933c3518b3a5f87fa0b49ff6bc71de987dc59bc8015b296920b762fd0"],
    }
    assert response.access_token == "access-token"  # noqa: S105
    assert response.public_token == "public-token"  # noqa: S105
    assert response.refresh_token is None
    assert response.user_id == "AB1234"
    assert response.user_name == "Ada Lovelace"
    assert response.user_shortname == "Ada"
    assert response.email == "ada@example.com"
    assert response.broker == "ZERODHA"
    assert response.login_time == datetime(2026, 5, 19, 9, 30)
    assert response.raw is not None
    assert response.raw["access_token"] == TEST_ACCESS_TOKEN


@pytest.mark.asyncio
async def test_exchange_request_token_retries_transient_server_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"status": "error", "message": "Temporarily down"})
        return httpx.Response(
            200,
            json={"status": "success", "data": {"access_token": TEST_ACCESS_TOKEN}},
        )

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = TokenExchangeClient(
            api_key=TEST_API_KEY,
            api_secret=TEST_API_SECRET,
            http_client=http_client,
            max_attempts=2,
        )

        response = await client.exchange_request_token(TEST_REQUEST_TOKEN)

    assert calls == 2
    assert response.access_token == "access-token"  # noqa: S105


@pytest.mark.asyncio
async def test_exchange_request_token_retries_transport_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("network unavailable", request=request)
        return httpx.Response(
            200,
            json={"status": "success", "data": {"access_token": TEST_ACCESS_TOKEN}},
        )

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = TokenExchangeClient(
            api_key=TEST_API_KEY,
            api_secret=TEST_API_SECRET,
            http_client=http_client,
            max_attempts=2,
        )

        response = await client.exchange_request_token(TEST_REQUEST_TOKEN)

    assert calls == 2
    assert response.access_token == "access-token"  # noqa: S105


@pytest.mark.asyncio
async def test_exchange_request_token_raises_for_kite_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"status": "error", "message": "Invalid checksum"})

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = TokenExchangeClient(
            api_key=TEST_API_KEY,
            api_secret=TEST_API_SECRET,
            http_client=http_client,
            max_attempts=1,
        )

        with pytest.raises(TokenExchangeError, match="Invalid checksum"):
            await client.exchange_request_token(TEST_REQUEST_TOKEN)


@pytest.mark.asyncio
async def test_exchange_request_token_raises_for_missing_access_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {}})

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = TokenExchangeClient(
            api_key=TEST_API_KEY,
            api_secret=TEST_API_SECRET,
            http_client=http_client,
            max_attempts=1,
        )

        with pytest.raises(TokenExchangeError, match="access_token"):
            await client.exchange_request_token(TEST_REQUEST_TOKEN)


@pytest.mark.asyncio
async def test_exchange_request_token_raises_for_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async with httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = TokenExchangeClient(
            api_key=TEST_API_KEY,
            api_secret=TEST_API_SECRET,
            http_client=http_client,
            max_attempts=1,
        )

        with pytest.raises(TokenExchangeError, match="valid JSON"):
            await client.exchange_request_token(TEST_REQUEST_TOKEN)


@pytest.mark.asyncio
async def test_exchange_request_token_requires_credentials() -> None:
    client = TokenExchangeClient(max_attempts=1)

    with pytest.raises(ConfigurationError, match="KITE_API_KEY, KITE_API_SECRET"):
        await client.exchange_request_token(TEST_REQUEST_TOKEN)


@pytest.mark.asyncio
async def test_token_manager_delegates_exchange() -> None:
    class FakeExchangeClient:
        async def exchange_request_token(self, request_token: str) -> AccessTokenResponse:
            return AccessTokenResponse(access_token=f"access-for-{request_token}")

    manager = TokenManager(exchange_client=FakeExchangeClient())

    response = await manager.exchange_request_token(TEST_REQUEST_TOKEN)

    assert response.access_token == "access-for-request-token"  # noqa: S105
