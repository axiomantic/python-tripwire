"""Tests for spy mode in import-site mocking."""

import sys
import types

import pytest

from tripwire._context import _active_verifier
from tripwire._mock_plugin import ImportSiteMock, MockPlugin
from tripwire._verifier import StrictVerifier


def _create_fake_module(name: str, **attrs: object) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def test_spy_calls_original_function(tripwire_verifier: StrictVerifier) -> None:
    """Spy delegates to the original function."""
    calls: list[tuple] = []

    def real_fn(x: int) -> int:
        calls.append((x,))
        return x * 2

    mod = _create_fake_module("_test_spy_calls", fn=real_fn)
    try:
        plugin = MockPlugin(tripwire_verifier)
        spy = ImportSiteMock(path="_test_spy_calls:fn", plugin=plugin, spy=True)
        spy._activate(enforce=True)

        token = _active_verifier.set(tripwire_verifier)
        try:
            result = mod.fn(5)
        finally:
            _active_verifier.reset(token)

        assert result == 10
        assert calls == [(5,)]
        spy._deactivate()

        # Assert the interaction so teardown doesn't raise
        tripwire_verifier.assert_interaction(
            spy.__getattr__("__call__"),
            args=(5,),
            kwargs={},
            returned=10,
        )
    finally:
        del sys.modules["_test_spy_calls"]


def test_spy_records_returned_value(tripwire_verifier: StrictVerifier) -> None:
    """Spy records 'returned' in interaction details."""
    mod = _create_fake_module("_test_spy_returned", fn=lambda x: x + 1)
    try:
        plugin = MockPlugin(tripwire_verifier)
        spy = ImportSiteMock(path="_test_spy_returned:fn", plugin=plugin, spy=True)
        spy._activate(enforce=True)

        token = _active_verifier.set(tripwire_verifier)
        try:
            mod.fn(5)
        finally:
            _active_verifier.reset(token)

        interactions = tripwire_verifier._timeline._interactions
        assert len(interactions) == 1
        assert interactions[0].details["returned"] == 6
        spy._deactivate()

        # Assert the interaction so teardown doesn't raise
        tripwire_verifier.assert_interaction(
            spy.__getattr__("__call__"),
            args=(5,),
            kwargs={},
            returned=6,
        )
    finally:
        del sys.modules["_test_spy_returned"]


def test_spy_records_raised_exception(tripwire_verifier: StrictVerifier) -> None:
    """Spy records 'raised' in interaction details when original raises."""
    def raises_fn() -> None:
        raise ValueError("boom")

    mod = _create_fake_module("_test_spy_raised", fn=raises_fn)
    try:
        plugin = MockPlugin(tripwire_verifier)
        spy = ImportSiteMock(path="_test_spy_raised:fn", plugin=plugin, spy=True)
        spy._activate(enforce=True)

        token = _active_verifier.set(tripwire_verifier)
        try:
            with pytest.raises(ValueError, match="boom"):
                mod.fn()
        finally:
            _active_verifier.reset(token)

        interactions = tripwire_verifier._timeline._interactions
        assert len(interactions) == 1
        assert "raised" in interactions[0].details
        assert isinstance(interactions[0].details["raised"], ValueError)
        spy._deactivate()

        # Assert the interaction so teardown doesn't raise
        from dirty_equals import IsInstance
        tripwire_verifier.assert_interaction(
            spy.__getattr__("__call__"),
            args=(),
            kwargs={},
            raised=IsInstance(ValueError),
        )
    finally:
        del sys.modules["_test_spy_raised"]
