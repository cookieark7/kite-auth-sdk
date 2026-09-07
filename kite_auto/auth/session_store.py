"""Session storage contracts and JSON persistence."""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from kite_auto.exceptions import SessionStoreError

DEFAULT_SESSION_PATH = Path.home() / ".kite_auto" / "session.json"


@dataclass(frozen=True, slots=True)
class KiteSession:
    """Persistable Kite access-token session."""

    access_token: str
    expires_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    public_token: str | None = None
    refresh_token: str | None = None
    user_id: str | None = None
    raw: Mapping[str, Any] | None = None

    def is_expired(
        self,
        *,
        now: datetime | None = None,
        skew: timedelta = timedelta(minutes=1),
    ) -> bool:
        """Return whether the session should be treated as expired."""
        current_time = normalize_datetime(now or datetime.now(UTC))
        return normalize_datetime(self.expires_at) <= current_time + skew

    def to_dict(self) -> dict[str, Any]:
        """Serialize the session to JSON-compatible data."""
        data: dict[str, Any] = {
            "access_token": self.access_token,
            "created_at": normalize_datetime(self.created_at).isoformat(),
            "expires_at": normalize_datetime(self.expires_at).isoformat(),
        }
        optional_values = {
            "public_token": self.public_token,
            "refresh_token": self.refresh_token,
            "user_id": self.user_id,
            "raw": dict(self.raw) if self.raw is not None else None,
        }
        data.update({key: value for key, value in optional_values.items() if value is not None})
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Deserialize a persisted session."""
        access_token = data.get("access_token")
        created_at = data.get("created_at")
        expires_at = data.get("expires_at")

        if not isinstance(access_token, str) or not access_token:
            raise SessionStoreError("Stored session is missing access_token.")
        if not isinstance(created_at, str) or not isinstance(expires_at, str):
            raise SessionStoreError("Stored session is missing timestamp fields.")

        return cls(
            access_token=access_token,
            created_at=parse_datetime(created_at, field_name="created_at"),
            expires_at=parse_datetime(expires_at, field_name="expires_at"),
            public_token=optional_str(data, "public_token"),
            refresh_token=optional_str(data, "refresh_token"),
            user_id=optional_str(data, "user_id"),
            raw=optional_mapping(data, "raw"),
        )


class SessionStore(ABC):
    """Contract for loading and saving persisted Kite sessions."""

    @abstractmethod
    async def save(self, session: KiteSession) -> None:
        """Persist session data."""

    @abstractmethod
    async def load(self) -> KiteSession | None:
        """Load persisted session data."""

    @abstractmethod
    async def clear(self) -> None:
        """Delete persisted session data."""


class JsonSessionStore(SessionStore):
    """JSON-backed session store.

    Writes are atomic and the session file is created with owner-only permissions.
    """

    def __init__(self, path: str | Path = DEFAULT_SESSION_PATH) -> None:
        self._path = Path(path).expanduser()

    @property
    def path(self) -> Path:
        """Return the session file path."""
        return self._path

    async def save(self, session: KiteSession) -> None:
        """Persist session data to JSON."""
        await asyncio.to_thread(self._save_sync, session)

    async def load(self) -> KiteSession | None:
        """Load session data from JSON, returning None when absent."""
        return await asyncio.to_thread(self._load_sync)

    async def clear(self) -> None:
        """Delete the JSON session file if it exists."""
        await asyncio.to_thread(self._clear_sync)

    def _save_sync(self, session: KiteSession) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp_path = self._path.with_name(f"{self._path.name}.tmp")
            tmp_path.write_text(
                json.dumps(session.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.chmod(tmp_path, 0o600)
            tmp_path.replace(self._path)
            os.chmod(self._path, 0o600)
        except OSError as exc:
            raise SessionStoreError(f"Unable to save session to {self._path}.") from exc

    def _load_sync(self) -> KiteSession | None:
        if not self._path.exists():
            return None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SessionStoreError(f"Stored session JSON is invalid: {self._path}.") from exc
        except OSError as exc:
            raise SessionStoreError(f"Unable to load session from {self._path}.") from exc

        if not isinstance(payload, dict):
            raise SessionStoreError("Stored session JSON must be an object.")
        return KiteSession.from_dict(payload)

    def _clear_sync(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            raise SessionStoreError(f"Unable to clear session at {self._path}.") from exc


def parse_datetime(value: str, *, field_name: str) -> datetime:
    """Parse and normalize a stored datetime value."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SessionStoreError(f"Stored session has invalid {field_name}.") from exc
    return normalize_datetime(parsed)


def normalize_datetime(value: datetime) -> datetime:
    """Normalize naive datetimes to UTC and preserve aware instants."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def optional_str(data: Mapping[str, Any], key: str) -> str | None:
    """Return a non-empty optional string field."""
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def optional_mapping(data: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    """Return an optional mapping field."""
    value = data.get(key)
    return value if isinstance(value, dict) else None
