"""Unit tests for version module."""

from src.version import __version__, get_version


def test_get_version():
    assert get_version() == "1.0.0"
    assert __version__ == "1.0.0"
