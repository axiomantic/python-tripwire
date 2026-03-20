"""Tests for _MockFactory and _SpyFactory module-level API."""

import sys
import types

import pytest

import bigfoot
from bigfoot._mock_plugin import ImportSiteMock, ObjectMock


def _create_fake_module(name: str, **attrs: object) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def test_bigfoot_mock_is_callable(bigfoot_verifier) -> None:
    """bigfoot.mock is callable and returns ImportSiteMock."""
    mock = bigfoot.mock("os.path:sep")
    assert isinstance(mock, ImportSiteMock)


def test_bigfoot_mock_object_returns_object_mock(bigfoot_verifier) -> None:
    """bigfoot.mock.object() returns ObjectMock."""

    class Target:
        value = "original"

    target = Target()
    mock = bigfoot.mock.object(target, "value")
    assert isinstance(mock, ObjectMock)


def test_bigfoot_spy_is_callable(bigfoot_verifier) -> None:
    """bigfoot.spy is callable and returns ImportSiteMock with spy=True."""
    mock = bigfoot.spy("os.path:sep")
    assert isinstance(mock, ImportSiteMock)
    assert mock._spy is True


def test_bigfoot_spy_object_returns_object_mock(bigfoot_verifier) -> None:
    """bigfoot.spy.object() returns ObjectMock with spy=True."""

    class Target:
        value = "original"

    target = Target()
    mock = bigfoot.spy.object(target, "value")
    assert isinstance(mock, ObjectMock)
    assert mock._spy is True


def test_bigfoot_mock_validates_path(bigfoot_verifier) -> None:
    """bigfoot.mock() raises ValueError for invalid paths."""
    with pytest.raises(ValueError, match="must use.*colon"):
        bigfoot.mock("invalid_path")
