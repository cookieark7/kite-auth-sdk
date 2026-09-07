"""Access-token exchange for Kite Connect sessions."""

import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from loguru import logger
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from kite_auto.auth.base import AuthStrategy
from kite_auto.auth.checksum import generate_checksum
from kite_auto.auth.session_store import JsonSessionStore, KiteSession, SessionStore
from kite_auto.config import Settings, get_settings
from kite_auto.exceptions import (
    AuthenticationError,
    ConfigurationError,
    TlsVerificationError,
    TokenExchangeError,
    TransientTokenExchangeError,
)

KITE_API_BASE_URL = "https://api.kite.trade"
SESSION_TOKEN_PATH = "/session/token"  # noqa: S105
TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _is_tls_error(exc: BaseException) -> bool:
    """Return whether an httpx transport error was caused by a TLS/SSL failure."""
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return True
        cause = cause.__cause__
    text = str(exc).lower()
    return "ssl" in text or "certificate" in text or "tls" in text


@dataclass(frozen=True, slots=True)
class AccessTokenResponse:
    """Typed response returned after exchanging a Kite request token."""

    access_token: str
    public_token: str | None = None
    refresh_token: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    user_shortname: str | None = None
    email: str | None = None
    broker: str | None = None
    login_time: datetime | None = None
    raw: Mapping[str, Any] | None = None


class TokenExchangeProtocol(Protocol):
    """Protocol for request-token exchange clients."""

    async def exchange_request_token(self, request_token: str) -> AccessTokenResponse:
        """Exchange request token for access-token response."""


class TokenExchangeClient:
    """Async client for Kite request-token to access-token exchange."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = KITE_API_BASE_URL,
        timeout: float = 10.0,
        max_attempts: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._api_key = api_key or self._settings.kite_api_key
        self._api_secret = api_secret or self._secret_value(self._settings.kite_api_secret)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._http_client = http_client

        if self._timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if self._max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

    async def exchange_request_token(self, request_token: str) -> AccessTokenResponse:
        """Exchange a Kite request token for a typed access-token response."""
        self._validate_required_settings(request_token)
        payload = {
            "api_key": self._api_key_value,
            "request_token": request_token,
            "checksum": generate_checksum(
                self._api_key_value,
                request_token,
                self._api_secret_value,
            ),
        }

        async for attempt in AsyncRetrying(
            reraise=True,
            retry=retry_if_exception_type(TransientTokenExchangeError),
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=8),
        ):
            with attempt:
                attempt_number = attempt.retry_state.attempt_number
                logger.info("Exchanging Kite request token, attempt {}", attempt_number)
                return await self._post_session_token(payload)

        raise TokenExchangeError("Kite token exchange did not complete.")

    async def _post_session_token(self, payload: Mapping[str, str]) -> AccessTokenResponse:
        if self._http_client is not None:
            response = await self._post_with_client(self._http_client, payload)
            return self._parse_response(response)

        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        ) as client:
            response = await self._post_with_client(client, payload)
            return self._parse_response(response)

    async def _post_with_client(
        self,
        client: httpx.AsyncClient,
        payload: Mapping[str, str],
    ) -> httpx.Response:
        try:
            return await client.post(SESSION_TOKEN_PATH, data=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if _is_tls_error(exc):
                # A TLS failure against a broker endpoint can mean interception;
                # never silently retry it, and name the cause.
                raise TlsVerificationError(
                    f"TLS verification failed during token exchange: {exc}"
                ) from exc
            raise TransientTokenExchangeError(
                "Transient network error during token exchange"
            ) from exc

    def _parse_response(self, response: httpx.Response) -> AccessTokenResponse:
        payload = self._response_json(response)
        if response.status_code in TRANSIENT_HTTP_STATUS_CODES:
            message = self._error_message(payload, response)
            raise TransientTokenExchangeError(message)
        if response.is_error:
            message = self._error_message(payload, response)
            raise TokenExchangeError(message)

        if payload.get("status") == "error":
            raise TokenExchangeError(self._error_message(payload, response))

        data = payload.get("data")
        if not isinstance(data, dict):
            raise TokenExchangeError("Kite token response did not contain a data object.")

        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise TokenExchangeError("Kite token response did not contain access_token.")

        return AccessTokenResponse(
            access_token=access_token,
            public_token=self._optional_str(data, "public_token"),
            refresh_token=self._optional_str(data, "refresh_token"),
            user_id=self._optional_str(data, "user_id"),
            user_name=self._optional_str(data, "user_name"),
            user_shortname=self._optional_str(data, "user_shortname"),
            email=self._optional_str(data, "email"),
            broker=self._optional_str(data, "broker"),
            login_time=self._parse_login_time(data.get("login_time")),
            raw=data,
        )

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise TokenExchangeError("Kite token response was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise TokenExchangeError("Kite token response JSON must be an object.")
        return payload

    @staticmethod
    def _error_message(payload: Mapping[str, Any], response: httpx.Response) -> str:
        message = payload.get("message")
        if isinstance(message, str) and message:
            return message

        error_type = payload.get("error_type")
        if isinstance(error_type, str) and error_type:
            return f"Kite token exchange failed with {error_type}."

        return f"Kite token exchange failed with HTTP {response.status_code}."

    @staticmethod
    def _optional_str(data: Mapping[str, Any], key: str) -> str | None:
        value = data.get(key)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _parse_login_time(value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            logger.debug("Unable to parse Kite login_time value: {}", value)
            return None

    def _validate_required_settings(self, request_token: str) -> None:
        missing = [
            name
            for name, value in (
                ("KITE_API_KEY", self._api_key),
                ("KITE_API_SECRET", self._api_secret),
                ("request_token", request_token),
            )
            if value is None or value == ""
        ]
        if missing:
            raise ConfigurationError(
                f"Missing required token exchange settings: {', '.join(missing)}"
            )

    @property
    def _api_key_value(self) -> str:
        if self._api_key is None:
            raise ConfigurationError("KITE_API_KEY is required")
        return self._api_key

    @property
    def _api_secret_value(self) -> str:
        if self._api_secret is None:
            raise ConfigurationError("KITE_API_SECRET is required")
        return self._api_secret

    @staticmethod
    def _secret_value(secret: object | None) -> str | None:
        if secret is None:
            return None
        if hasattr(secret, "get_secret_value"):
            return str(secret.get_secret_value())
        return str(secret)


class TokenManager:
    """Coordinates session reuse, token exchange, and reauthentication."""

    def __init__(
        self,
        exchange_client: TokenExchangeProtocol | None = None,
        *,
        auth_strategy: AuthStrategy | None = None,
        session_store: SessionStore | None = None,
        session_ttl: timedelta = timedelta(hours=24),
        expiry_skew: timedelta = timedelta(minutes=1),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._exchange_client = exchange_client or TokenExchangeClient()
        self._auth_strategy = auth_strategy
        self._session_store = session_store or JsonSessionStore()
        self._session_ttl = session_ttl
        self._expiry_skew = expiry_skew
        self._clock = clock or (lambda: datetime.now(UTC))

        if self._session_ttl <= timedelta(0):
            raise ValueError("session_ttl must be greater than 0")
        if self._expiry_skew < timedelta(0):
            raise ValueError("expiry_skew cannot be negative")

    async def exchange_request_token(self, request_token: str) -> AccessTokenResponse:
        """Exchange a request token using the configured token-exchange client."""
        return await self._exchange_client.exchange_request_token(request_token)

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid access token, reauthenticating when needed."""
        session = None if force_refresh else await self._session_store.load()
        if session is not None and not session.is_expired(now=self._now(), skew=self._expiry_skew):
            logger.debug("Using persisted Kite access token")
            return session.access_token

        if session is not None:
            logger.info("Persisted Kite session is expired; reauthenticating")
        else:
            logger.info("No persisted Kite session found; authenticating")

        refreshed = await self.refresh_access_token()
        return refreshed.access_token

    async def refresh_access_token(self) -> KiteSession:
        """Force reauthentication, exchange the request token, and persist the session."""
        if self._auth_strategy is None:
            raise AuthenticationError("TokenManager requires an auth_strategy to reauthenticate.")

        request_token = await self._auth_strategy.login()
        token_response = await self.exchange_request_token(request_token)
        session = self._session_from_token_response(token_response)
        await self._session_store.save(session)
        return session

    async def clear_session(self) -> None:
        """Clear persisted session data."""
        await self._session_store.clear()

    def _session_from_token_response(self, token_response: AccessTokenResponse) -> KiteSession:
        now = self._now()
        return KiteSession(
            access_token=token_response.access_token,
            created_at=now,
            expires_at=now + self._session_ttl,
            public_token=token_response.public_token,
            refresh_token=token_response.refresh_token,
            user_id=token_response.user_id,
            raw=token_response.raw,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
