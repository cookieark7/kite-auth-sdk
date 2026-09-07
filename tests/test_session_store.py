"""Tests for session persistence."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kite_auto.auth.session_store import JsonSessionStore, KiteSession
from kite_auto.exceptions import SessionStoreError

TEST_ACCESS_TOKEN = "access-token"  # noqa: S105
TEST_PUBLIC_TOKEN = "public-token"  # noqa: S105


def test_kite_session_serializes_round_trip() -> None:
    created_at = datetime(2026, 5, 19, 9, 30, tzinfo=UTC)
    expires_at = created_at + timedelta(hours=24)
    session = KiteSession(
        access_token=TEST_ACCESS_TOKEN,
        created_at=created_at,
        expires_at=expires_at,
        public_token=TEST_PUBLIC_TOKEN,
        user_id="AB1234",
        raw={"access_token": TEST_ACCESS_TOKEN},
    )

    restored = KiteSession.from_dict(session.to_dict())

    assert restored == session


def test_kite_session_expiry_detection_uses_skew() -> None:
    now = datetime(2026, 5, 19, 9, 30, tzinfo=UTC)
    session = KiteSession(
        access_token=TEST_ACCESS_TOKEN,
        created_at=now - timedelta(hours=23),
        expires_at=now + timedelta(seconds=30),
    )

    assert session.is_expired(now=now, skew=timedelta(minutes=1))
    assert not session.is_expired(now=now, skew=timedelta(seconds=1))


@pytest.mark.asyncio
async def test_json_session_store_save_load_and_clear(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    store = JsonSessionStore(path)
    now = datetime(2026, 5, 19, 9, 30, tzinfo=UTC)
    session = KiteSession(
        access_token=TEST_ACCESS_TOKEN,
        created_at=now,
        expires_at=now + timedelta(hours=24),
        user_id="AB1234",
    )

    assert await store.load() is None

    await store.save(session)
    restored = await store.load()

    assert restored == session
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600

    await store.clear()

    assert await store.load() is None


@pytest.mark.asyncio
async def test_json_session_store_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text("{not-json", encoding="utf-8")
    store = JsonSessionStore(path)

    with pytest.raises(SessionStoreError, match="invalid"):
        await store.load()


@pytest.mark.asyncio
async def test_json_session_store_rejects_invalid_session_shape(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text("[]", encoding="utf-8")
    store = JsonSessionStore(path)

    with pytest.raises(SessionStoreError, match="object"):
        await store.load()
