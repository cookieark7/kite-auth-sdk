"""Scaffold sanity tests."""

from kite_auto import KiteClient
from kite_auto.config import Settings, get_settings


def test_package_exports_kite_client() -> None:
    assert KiteClient.__name__ == "KiteClient"


def test_settings_loader_returns_settings() -> None:
    assert isinstance(get_settings(), Settings)
