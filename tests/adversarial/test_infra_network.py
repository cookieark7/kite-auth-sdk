"""Category 6 — Infra / network (kite_sdk_test_plan.md).

Wired to the real TokenExchangeClient. Theme: infra failures must be
distinguishable (by TYPE) from credential failures, because retrying a
credential failure burns lockout attempts.
"""

from __future__ import annotations

import httpx
import pytest

from kite_auto.auth.token_manager import TokenExchangeClient
from kite_auto.exceptions import TokenExchangeError, TransientTokenExchangeError


def _client(handler) -> TokenExchangeClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="https://api.kite.trade")
    return TokenExchangeClient(
        api_key="api-key",
        api_secret="api-secret",  # noqa: S106
        max_attempts=1,  # isolate a single response; retry behaviour tested elsewhere
        http_client=http_client,
    )


async def test_503_is_distinguishable_from_credential_failure() -> None:
    """503 and a credential rejection must raise DIFFERENT types.

    Intent: a caller must be able to retry the 503 and must never retry the
    credential failure — so the distinction has to be in the type, not just the
    message.
    """
    def server_error(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "error", "message": "Service down"})

    def bad_credentials(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"status": "error", "error_type": "TokenException", "message": "bad checksum"},
        )

    with pytest.raises(TransientTokenExchangeError):
        await _client(server_error).exchange_request_token("req-token")

    with pytest.raises(TokenExchangeError) as terminal:
        await _client(bad_credentials).exchange_request_token("req-token")

    # The credential failure must NOT be the retryable transient subtype.
    assert not isinstance(terminal.value, TransientTokenExchangeError)


async def test_connection_hang_times_out_and_is_identifiable() -> None:
    """A hung connection must surface as a timeout, distinct from bad credentials.

    Intent: the timeout must actually return (not hang the test) and be a
    retryable transient type, never mistaken for a credential rejection.
    """
    def hang(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=_request)

    with pytest.raises(TransientTokenExchangeError) as exc:
        await _client(hang).exchange_request_token("req-token")

    assert not isinstance(exc.value, type(None))
    # A timeout is infra, not credentials: distinct handling required.
    assert isinstance(exc.value, TransientTokenExchangeError)


async def test_dns_failure_surfaces_clear_error() -> None:
    """A DNS resolution failure must surface as a clear typed error."""
    def dns_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 8] nodename nor servname provided", request=request)

    with pytest.raises(TransientTokenExchangeError):
        await _client(dns_fail).exchange_request_token("req-token")


async def test_ssl_failure_is_not_silently_retried_and_names_cause() -> None:
    """An SSL failure must be distinguishable and must not be treated as a
    routinely-retryable transient error.

    Intent: TLS failure on a broker endpoint is security-relevant. It should
    surface a clear, non-transient (or specifically-typed) error that names TLS
    — not a generic 'transient network error' that the retry loop happily
    repeats.
    """
    def ssl_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request
        )

    with pytest.raises(Exception) as exc:
        await _client(ssl_fail).exchange_request_token("req-token")

    # Both properties are the intent; either failing is a real finding.
    assert not isinstance(exc.value, TransientTokenExchangeError), (
        "TLS failure classified as routinely-retryable transient error"
    )
    assert "ssl" in str(exc.value).lower() or "tls" in str(exc.value).lower(), (
        "surfaced error does not name the TLS cause"
    )
