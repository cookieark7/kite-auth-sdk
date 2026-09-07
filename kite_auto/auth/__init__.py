"""Authentication strategy interfaces and implementations."""

from kite_auto.auth.base import AuthStrategy
from kite_auto.auth.manual_auth import ManualAuthStrategy
from kite_auto.auth.playwright_auth import (
    PlaywrightAuthStrategy,
    PlaywrightLoginSelectors,
    extract_request_token,
)
from kite_auto.auth.session_store import JsonSessionStore, KiteSession, SessionStore
from kite_auto.auth.token_manager import AccessTokenResponse, TokenExchangeClient, TokenManager

__all__ = [
    "AccessTokenResponse",
    "AuthStrategy",
    "JsonSessionStore",
    "KiteSession",
    "ManualAuthStrategy",
    "PlaywrightAuthStrategy",
    "PlaywrightLoginSelectors",
    "SessionStore",
    "TokenExchangeClient",
    "TokenManager",
    "extract_request_token",
]
