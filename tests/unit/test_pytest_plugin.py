# tests/unit/test_pytest_plugin.py
"""Unit tests for bigfoot pytest fixtures.

Tests verify the structural contracts of both fixtures:
- _bigfoot_auto_verifier: autouse generator, sets ContextVar, calls verify_all() at teardown
- bigfoot_verifier: explicit fixture that returns the auto-verifier
"""

from unittest.mock import MagicMock

import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import UnassertedInteractionsError
from bigfoot._verifier import StrictVerifier
from bigfoot.pytest_plugin import _bigfoot_auto_verifier, bigfoot_verifier

# ---------------------------------------------------------------------------
# _bigfoot_auto_verifier fixture contract
# ---------------------------------------------------------------------------


def test_bigfoot_auto_verifier_yields_strict_verifier() -> None:
    """_bigfoot_auto_verifier must yield a StrictVerifier instance."""
    gen = _bigfoot_auto_verifier.__wrapped__()  # type: ignore[attr-defined]
    verifier = next(gen)
    try:
        assert isinstance(verifier, StrictVerifier)
    finally:
        # Exhaust the generator to run teardown (verify_all on empty verifier is a no-op)
        try:
            next(gen)
        except StopIteration:
            pass


def test_bigfoot_auto_verifier_sets_context_var_during_yield() -> None:
    """_bigfoot_auto_verifier must set _current_test_verifier while yielded."""
    gen = _bigfoot_auto_verifier.__wrapped__()  # type: ignore[attr-defined]
    verifier = next(gen)
    try:
        assert _current_test_verifier.get() is verifier
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_bigfoot_auto_verifier_resets_context_var_after_teardown() -> None:
    """_bigfoot_auto_verifier must reset _current_test_verifier after yield."""
    original = _current_test_verifier.get()
    gen = _bigfoot_auto_verifier.__wrapped__()  # type: ignore[attr-defined]
    next(gen)
    try:
        next(gen)
    except StopIteration:
        pass
    assert _current_test_verifier.get() is original


def test_bigfoot_auto_verifier_calls_verify_all_at_teardown() -> None:
    """_bigfoot_auto_verifier must call verifier.verify_all() at teardown."""
    gen = _bigfoot_auto_verifier.__wrapped__()  # type: ignore[attr-defined]
    verifier = next(gen)
    verifier.verify_all = MagicMock()
    try:
        next(gen)
    except StopIteration:
        pass
    verifier.verify_all.assert_called_once_with()


def test_bigfoot_auto_verifier_teardown_propagates_verify_all_exception() -> None:
    """If verify_all() raises, the exception must propagate from the generator teardown."""
    gen = _bigfoot_auto_verifier.__wrapped__()  # type: ignore[attr-defined]
    verifier = next(gen)
    expected_error = UnassertedInteractionsError(
        interactions=[object()],
        hint="1 unasserted interaction",
    )
    verifier.verify_all = MagicMock(side_effect=expected_error)

    with pytest.raises(UnassertedInteractionsError) as exc_info:
        try:
            next(gen)
        except StopIteration:
            pass

    assert exc_info.value is expected_error


# ---------------------------------------------------------------------------
# bigfoot_verifier explicit fixture contract
# ---------------------------------------------------------------------------


def test_bigfoot_verifier_returns_auto_verifier() -> None:
    """bigfoot_verifier must return the same StrictVerifier as the auto-verifier."""
    # Simulate: _bigfoot_auto_verifier yielded a verifier, bigfoot_verifier passes it through
    mock_auto_verifier = MagicMock(spec=StrictVerifier)

    result = bigfoot_verifier.__wrapped__(mock_auto_verifier)  # type: ignore[attr-defined]

    assert result is mock_auto_verifier


def test_bigfoot_verifier_returns_strict_verifier_instance() -> None:
    """The explicit fixture must return a StrictVerifier."""
    real_verifier = StrictVerifier()
    result = bigfoot_verifier.__wrapped__(real_verifier)  # type: ignore[attr-defined]
    assert isinstance(result, StrictVerifier)
    assert result is real_verifier
