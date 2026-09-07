"""Exception hierarchy for kite-auto-sdk."""


class KiteAutoError(Exception):
    """Base exception for all SDK errors."""


class NotImplementedInScaffoldError(KiteAutoError, NotImplementedError):
    """Raised by placeholders created during the scaffold phase."""


class ConfigurationError(KiteAutoError):
    """Raised when required SDK configuration is missing or invalid."""


class AuthenticationError(KiteAutoError):
    """Raised when authentication fails."""


class ClockDriftError(KiteAutoError):
    """Raised when host clock drift exceeds the configured tolerance."""


class RequestTokenNotFoundError(AuthenticationError):
    """Raised when the login redirect does not contain a request token."""


class LoginPageStructureError(AuthenticationError):
    """Raised when the login page structure is unrecognized (e.g. a Kite UI
    change, a disabled control, or an unexpected CAPTCHA/verification screen).

    This is *not* a transient credential failure, so callers and the login retry
    loop must not retry it: a UI change will not fix itself, and retrying wastes
    attempts against the account's lockout budget.
    """


class TotpLockoutError(AuthenticationError):
    """Raised when the SDK's own safety cap stops further TOTP submissions before
    Kite's real account-lockout threshold is reached."""


class TokenExchangeError(KiteAutoError):
    """Raised when request-token exchange fails."""


class TransientTokenExchangeError(TokenExchangeError):
    """Raised for token-exchange failures that are safe to retry."""


class TlsVerificationError(TokenExchangeError):
    """Raised when a TLS/SSL failure occurs talking to Kite.

    Deliberately *not* a :class:`TransientTokenExchangeError`: a certificate
    failure against a broker endpoint can indicate interception and must never be
    silently retried.
    """


class SessionStoreError(KiteAutoError):
    """Raised when session persistence fails."""


class KiteApiError(KiteAutoError):
    """Raised when a Kite API request fails."""


class TransientKiteApiError(KiteApiError):
    """Raised for Kite API failures that are safe to retry."""


class KiteWebSocketError(KiteAutoError):
    """Raised when Kite WebSocket streaming fails."""


class RateLimitError(KiteAutoError):
    """Raised when rate limiting cannot admit an operation."""


class CircuitBreakerOpenError(KiteAutoError):
    """Raised when a circuit breaker rejects an operation."""


class DuplicateOrderError(KiteAutoError):
    """Raised when duplicate order placement is blocked."""
