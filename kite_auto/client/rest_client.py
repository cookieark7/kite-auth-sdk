"""Authenticated async REST transport for Kite Connect."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Self

import httpx

from kite_auto.auth.token_manager import KITE_API_BASE_URL
from kite_auto.config import Settings, get_settings
from kite_auto.exceptions import ConfigurationError, KiteApiError, TransientKiteApiError
from kite_auto.utils.rate_limit import RateLimiter
from kite_auto.utils.retry import AsyncRetryPolicy, CircuitBreaker

KITE_API_VERSION = "3"
AUTH_RETRY_STATUS_CODES = frozenset({401, 403})
TRANSIENT_RETRY_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
DEFAULT_RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

type AccessTokenProvider = Callable[..., Awaitable[str]]


class RestClient:
    """Async REST API client with automatic token injection."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str | None = None,
        access_token_provider: AccessTokenProvider | None = None,
        base_url: str = KITE_API_BASE_URL,
        timeout: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
        retry_policy: AsyncRetryPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        retryable_methods: frozenset[str] = DEFAULT_RETRYABLE_METHODS,
    ) -> None:
        self._settings = settings or get_settings()
        self._api_key = api_key or self._settings.kite_api_key
        self._access_token_provider = access_token_provider
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http_client = http_client
        self._owns_client = http_client is None
        self._retry_policy = retry_policy
        self._rate_limiter = rate_limiter
        self._circuit_breaker = circuit_breaker
        self._retryable_methods = frozenset(method.upper() for method in retryable_methods)

        if self._timeout <= 0:
            raise ValueError("timeout must be greater than 0")

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client if this object created it."""
        if self._http_client is not None and self._owns_client:
            await self._http_client.aclose()
            self._http_client = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        data: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        force_refresh: bool = False,
    ) -> Any:
        """Send an authenticated Kite request and return the JSON `data` field."""
        method = method.upper()
        response = await self._send_with_resilience(
            method,
            path,
            params=params,
            data=data,
            json=json,
            force_refresh=force_refresh,
        )

        if response.status_code in AUTH_RETRY_STATUS_CODES and not force_refresh:
            response = await self._send_with_resilience(
                method,
                path,
                params=params,
                data=data,
                json=json,
                force_refresh=True,
            )

        return self._parse_response(response)

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> Any:
        """Send an authenticated GET request."""
        return await self.request("GET", path, params=params)

    async def post(
        self,
        path: str,
        *,
        data: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        """Send an authenticated POST request."""
        return await self.request("POST", path, data=data, json=json)

    async def get_text(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> str:
        """Send an authenticated GET and return the raw body (e.g. the instruments CSV)."""
        response = await self._send_with_resilience(
            "GET", path, params=params, data=None, json=None, force_refresh=False
        )
        if response.status_code in AUTH_RETRY_STATUS_CODES:
            response = await self._send_with_resilience(
                "GET", path, params=params, data=None, json=None, force_refresh=True
            )
        if response.is_error:
            raise KiteApiError(self._error_message_from_response(response))
        return response.text

    async def _send_with_resilience(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None,
        data: Mapping[str, Any] | None,
        json: Mapping[str, Any] | None,
        force_refresh: bool,
    ) -> httpx.Response:
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()

        async def operation() -> httpx.Response:
            return await self._send_once(
                method,
                path,
                params=params,
                data=data,
                json=json,
                force_refresh=force_refresh,
            )

        if self._circuit_breaker is not None:
            base_operation = operation
            circuit_breaker = self._circuit_breaker

            async def operation() -> httpx.Response:
                return await circuit_breaker.call(
                    base_operation,
                    record_failure=lambda exc: isinstance(exc, TransientKiteApiError),
                )

        if self._retry_policy is not None and method in self._retryable_methods:
            return await self._retry_policy.execute(
                operation,
                retry_if=lambda exc: isinstance(exc, TransientKiteApiError),
            )

        return await operation()

    async def _send_once(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None,
        data: Mapping[str, Any] | None,
        json: Mapping[str, Any] | None,
        force_refresh: bool,
    ) -> httpx.Response:
        client = self._client()
        try:
            response = await client.request(
                method,
                path,
                params=params,
                data=data,
                json=json,
                headers=await self._headers(force_refresh=force_refresh),
            )
            if (
                method in self._retryable_methods
                and response.status_code in TRANSIENT_RETRY_STATUS_CODES
            ):
                raise TransientKiteApiError(self._error_message_from_response(response))
            return response
        except httpx.HTTPError as exc:
            if method in self._retryable_methods:
                raise TransientKiteApiError(
                    "Kite API request failed due to a transient network error."
                ) from exc
            raise KiteApiError("Kite API request failed due to a network error.") from exc

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._http_client

    async def _headers(self, *, force_refresh: bool) -> dict[str, str]:
        api_key = self._api_key_value
        access_token = await self._access_token(force_refresh=force_refresh)
        return {
            "Authorization": f"token {api_key}:{access_token}",
            "X-Kite-Version": KITE_API_VERSION,
        }

    async def _access_token(self, *, force_refresh: bool) -> str:
        if self._access_token_provider is None:
            raise ConfigurationError("RestClient requires an access_token_provider.")
        try:
            return await self._access_token_provider(force_refresh=force_refresh)
        except TypeError:
            if force_refresh:
                raise
            return await self._access_token_provider()

    @property
    def _api_key_value(self) -> str:
        if self._api_key is None:
            raise ConfigurationError("KITE_API_KEY is required for REST API requests.")
        return self._api_key

    @staticmethod
    def _parse_response(response: httpx.Response) -> Any:
        payload = RestClient._response_json(response)
        if response.is_error or payload.get("status") == "error":
            message = RestClient._error_message(payload, response)
            raise KiteApiError(message)
        if "data" not in payload:
            raise KiteApiError("Kite API response did not contain data.")
        return payload["data"]

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise KiteApiError("Kite API response was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise KiteApiError("Kite API response JSON must be an object.")
        return payload

    @staticmethod
    def _error_message(payload: Mapping[str, Any], response: httpx.Response) -> str:
        message = payload.get("message")
        if isinstance(message, str) and message:
            return message

        error_type = payload.get("error_type")
        if isinstance(error_type, str) and error_type:
            return f"Kite API request failed with {error_type}."

        return f"Kite API request failed with HTTP {response.status_code}."

    @staticmethod
    def _error_message_from_response(response: httpx.Response) -> str:
        try:
            payload = RestClient._response_json(response)
        except KiteApiError:
            return f"Kite API request failed with HTTP {response.status_code}."
        return RestClient._error_message(payload, response)
