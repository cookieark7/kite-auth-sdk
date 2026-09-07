"""Tests for host clock-drift verification."""

from collections.abc import Awaitable, Callable

import httpx
import pytest

from kite_auto.exceptions import ClockDriftError
from kite_auto.utils import clock
from kite_auto.utils.clock import assert_clock_sync, measure_clock_drift


async def _const_trusted(value: float | None) -> Callable[[], Awaitable[float | None]]:
    async def _source() -> float | None:
        return value

    return _source


@pytest.mark.asyncio
async def test_measure_clock_drift_returns_absolute_difference() -> None:
    trusted = await _const_trusted(100.0)
    drift = await measure_clock_drift(
        trusted_time_source=trusted, local_time_source=lambda: 97.5
    )
    assert drift == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_measure_clock_drift_returns_none_when_unavailable() -> None:
    trusted = await _const_trusted(None)
    drift = await measure_clock_drift(
        trusted_time_source=trusted, local_time_source=lambda: 100.0
    )
    assert drift is None


@pytest.mark.asyncio
async def test_assert_clock_sync_within_tolerance_returns_drift() -> None:
    trusted = await _const_trusted(100.0)
    drift = await assert_clock_sync(
        max_drift_seconds=5.0,
        hard_fail=False,
        trusted_time_source=trusted,
        local_time_source=lambda: 102.0,
    )
    assert drift == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_assert_clock_sync_warns_but_does_not_raise_when_soft() -> None:
    trusted = await _const_trusted(100.0)
    drift = await assert_clock_sync(
        max_drift_seconds=5.0,
        hard_fail=False,
        trusted_time_source=trusted,
        local_time_source=lambda: 120.0,
    )
    assert drift == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_assert_clock_sync_raises_when_hard_fail_and_drift_exceeds() -> None:
    trusted = await _const_trusted(100.0)
    with pytest.raises(ClockDriftError, match="drift"):
        await assert_clock_sync(
            max_drift_seconds=5.0,
            hard_fail=True,
            trusted_time_source=trusted,
            local_time_source=lambda: 120.0,
        )


@pytest.mark.asyncio
async def test_assert_clock_sync_skips_when_trusted_unavailable_even_if_hard_fail() -> None:
    trusted = await _const_trusted(None)
    drift = await assert_clock_sync(
        max_drift_seconds=5.0,
        hard_fail=True,
        trusted_time_source=trusted,
        local_time_source=lambda: 100.0,
    )
    assert drift is None


class _FakeResponse:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse | None, *, raises: bool = False) -> None:
        self._response = response
        self._raises = raises

    async def __aenter__(self) -> "_FakeAsyncClient":
        if self._raises:
            raise RuntimeError("network down")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def head(self, url: str) -> _FakeResponse:
        assert self._response is not None
        return self._response


@pytest.mark.asyncio
async def test_fetch_trusted_epoch_parses_date_header(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse({"Date": "Wed, 21 Oct 2015 07:28:00 GMT"})
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(response)
    )
    epoch = await clock.fetch_trusted_epoch("http://example.com")
    assert epoch == pytest.approx(1_445_412_480.0)


@pytest.mark.asyncio
async def test_fetch_trusted_epoch_none_without_date_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse({})
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(response)
    )
    assert await clock.fetch_trusted_epoch("http://example.com") is None


@pytest.mark.asyncio
async def test_fetch_trusted_epoch_none_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(None, raises=True)
    )
    assert await clock.fetch_trusted_epoch("http://example.com") is None
