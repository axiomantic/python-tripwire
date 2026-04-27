"""Tests for _MockFactory and _SpyFactory module-level API."""

import sys
import types

import pytest

import tripwire
from tripwire._mock_plugin import ImportSiteMock, ObjectMock


def _create_fake_module(name: str, **attrs: object) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def test_tripwire_mock_is_callable(tripwire_verifier) -> None:
    """tripwire.mock is callable and returns ImportSiteMock."""
    mock = tripwire.mock("os.path:sep")
    assert isinstance(mock, ImportSiteMock)


def test_tripwire_mock_object_returns_object_mock(tripwire_verifier) -> None:
    """tripwire.mock.object() returns ObjectMock."""

    class Target:
        value = "original"

    target = Target()
    mock = tripwire.mock.object(target, "value")
    assert isinstance(mock, ObjectMock)


def test_tripwire_spy_is_callable(tripwire_verifier) -> None:
    """tripwire.spy is callable and returns ImportSiteMock with spy=True."""
    mock = tripwire.spy("os.path:sep")
    assert isinstance(mock, ImportSiteMock)
    assert mock._spy is True


def test_tripwire_spy_object_returns_object_mock(tripwire_verifier) -> None:
    """tripwire.spy.object() returns ObjectMock with spy=True."""

    class Target:
        value = "original"

    target = Target()
    mock = tripwire.spy.object(target, "value")
    assert isinstance(mock, ObjectMock)
    assert mock._spy is True


def test_tripwire_mock_validates_path(tripwire_verifier) -> None:
    """tripwire.mock() raises ValueError for invalid paths."""
    with pytest.raises(ValueError, match="must use.*colon"):
        tripwire.mock("invalid_path")
