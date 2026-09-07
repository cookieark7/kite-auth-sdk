"""Checksum utilities for Kite access-token exchange."""

from hashlib import sha256


def generate_checksum(api_key: str, request_token: str, api_secret: str) -> str:
    """Generate the Kite access-token checksum."""
    return sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()
