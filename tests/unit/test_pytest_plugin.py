# tests/unit/test_pytest_plugin.py
"""Unit tests for bigfoot_verifier pytest fixture.

Tests verify the fixture's structural contract directly:
- returns a StrictVerifier instance
- registers verify_all() as a finalizer via request.addfinalizer
- finalizer propagates exceptions raised by verify_all()
"""
from unittest.mock import MagicMock

import pytest

from bigfoot._errors import UnassertedInteractionsError
from bigfoot._verifier import StrictVerifier
from bigfoot.pytest_plugin import bigfoot_verifier


def _make_mock_request() -> MagicMock:
    """Return a mock pytest FixtureRequest with addfinalizer tracking."""
    req = MagicMock(spec=["addfinalizer"])
    req.addfinalizer = MagicMock()
    return req


def test_bigfoot_verifier_returns_strict_verifier() -> None:
    """The fixture must return a StrictVerifier instance."""
    # ESCAPE: if the fixture returned a subclass or unrelated object this would fail
    req = _make_mock_request()

    result = bigfoot_verifier.__wrapped__(req)  # type: ignore[attr-defined]

    assert isinstance(result, StrictVerifier)


def test_bigfoot_verifier_calls_addfinalizer_once() -> None:
    """addfinalizer must be called exactly once during fixture setup."""
    # ESCAPE: if addfinalizer was never called teardown would silently not run
    req = _make_mock_request()

    bigfoot_verifier.__wrapped__(req)  # type: ignore[attr-defined]

    assert req.addfinalizer.call_count == 1


def test_bigfoot_verifier_finalizer_calls_verify_all() -> None:
    """The registered finalizer must call verifier.verify_all()."""
    # ESCAPE: if finalizer called a different method verify_all wouldn't run at teardown
    req = _make_mock_request()

    result = bigfoot_verifier.__wrapped__(req)  # type: ignore[attr-defined]

    # Extract the finalizer that was registered
    (finalizer,), _ = req.addfinalizer.call_args

    # Patch verify_all on the returned verifier to track calls
    result.verify_all = MagicMock()
    finalizer()

    result.verify_all.assert_called_once_with()


def test_bigfoot_verifier_finalizer_propagates_verify_all_exception() -> None:
    """If verify_all() raises, the exception must propagate from the finalizer."""
    # ESCAPE: if the finalizer swallowed exceptions test failures would be silenced
    req = _make_mock_request()

    result = bigfoot_verifier.__wrapped__(req)  # type: ignore[attr-defined]

    (finalizer,), _ = req.addfinalizer.call_args

    expected_error = UnassertedInteractionsError(
        interactions=[object()],
        hint="1 unasserted interaction",
    )
    result.verify_all = MagicMock(side_effect=expected_error)

    with pytest.raises(UnassertedInteractionsError) as exc_info:
        finalizer()

    assert exc_info.value is expected_error
