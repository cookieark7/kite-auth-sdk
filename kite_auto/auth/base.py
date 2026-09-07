"""Authentication strategy contracts."""

from abc import ABC, abstractmethod


class AuthStrategy(ABC):
    """Contract for producing a Kite request token."""

    @abstractmethod
    async def login(self) -> str:
        """Authenticate and return a request token."""

