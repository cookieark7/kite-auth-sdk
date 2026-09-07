"""Manual authentication strategy placeholder."""

from kite_auto.auth.base import AuthStrategy
from kite_auto.exceptions import NotImplementedInScaffoldError


class ManualAuthStrategy(AuthStrategy):
    """Manual request-token flow.

    Business logic is intentionally deferred beyond the scaffold phase.
    """

    async def login(self) -> str:
        raise NotImplementedInScaffoldError("Manual authentication is not implemented yet.")

