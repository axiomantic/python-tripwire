"""Tests for Task 3: _context.py — ContextVars."""

from __future__ import annotations

import contextvars

import pytest

from tripwire._context import (
    _active_verifier,
    _any_order_depth,
    get_active_verifier,
    get_verifier_or_raise,
    is_in_any_order,
)
from tripwire._errors import SandboxNotActiveError

# ---------------------------------------------------------------------------
# ContextVar defaults
# ---------------------------------------------------------------------------


def test_active_verifier_default_is_none() -> None:
    assert _active_verifier.get() is None


def test_any_order_depth_default_is_zero() -> None:
    assert _any_order_depth.get() == 0


def test_active_verifier_is_contextvar() -> None:
    assert isinstance(_active_verifier, contextvars.ContextVar)


def test_any_order_depth_is_contextvar() -> None:
    assert isinstance(_any_order_depth, contextvars.ContextVar)


# ---------------------------------------------------------------------------
# get_active_verifier
# ---------------------------------------------------------------------------


def test_get_active_verifier_returns_none_by_default() -> None:
    assert get_active_verifier() is None


def test_get_active_verifier_returns_set_value() -> None:
    sentinel = object()
    token = _active_verifier.set(sentinel)  # type: ignore[arg-type]
    try:
        assert get_active_verifier() is sentinel
    finally:
        _active_verifier.reset(token)
    assert get_active_verifier() is None


# ---------------------------------------------------------------------------
# get_verifier_or_raise
# ---------------------------------------------------------------------------


def testget_verifier_or_raise_raises_when_no_verifier() -> None:
    """With guard mode active (default), raises GuardedCallError instead of
    SandboxNotActiveError. Disable guard to test the original behavior."""
    from tripwire._context import _guard_active, _guard_patches_installed

    guard_token = _guard_active.set(False)
    patches_token = _guard_patches_installed.set(False)
    try:
        with pytest.raises(SandboxNotActiveError) as exc_info:
            get_verifier_or_raise(source_id="test_source")
        assert exc_info.value.source_id == "test_source"
    finally:
        _guard_patches_installed.reset(patches_token)
        _guard_active.reset(guard_token)


def testget_verifier_or_raise_returns_verifier_when_set() -> None:
    sentinel = object()
    token = _active_verifier.set(sentinel)  # type: ignore[arg-type]
    try:
        result = get_verifier_or_raise(source_id="test_source")
        assert result is sentinel
    finally:
        _active_verifier.reset(token)


# ---------------------------------------------------------------------------
# is_in_any_order
# ---------------------------------------------------------------------------


def test_is_in_any_order_returns_false_by_default() -> None:
    assert is_in_any_order() is False


def test_is_in_any_order_returns_true_when_depth_is_one() -> None:
    token = _any_order_depth.set(1)
    try:
        assert is_in_any_order() is True
    finally:
        _any_order_depth.reset(token)
    assert is_in_any_order() is False


def test_is_in_any_order_returns_true_when_depth_is_greater_than_one() -> None:
    token = _any_order_depth.set(5)
    try:
        assert is_in_any_order() is True
    finally:
        _any_order_depth.reset(token)


def test_is_in_any_order_returns_false_when_depth_is_zero() -> None:
    token = _any_order_depth.set(0)
    try:
        assert is_in_any_order() is False
    finally:
        _any_order_depth.reset(token)


# ---------------------------------------------------------------------------
# ContextVar isolation
# ---------------------------------------------------------------------------


def test_active_verifier_isolated_per_context() -> None:
    """ContextVar changes in one context do not affect another."""
    sentinel = object()
    token = _active_verifier.set(sentinel)  # type: ignore[arg-type]
    try:
        assert _active_verifier.get() is sentinel

        def check_isolated() -> None:
            assert _active_verifier.get() is sentinel  # inherited
            _active_verifier.set(None)  # only affects this copy

        ctx = contextvars.copy_context()
        ctx.run(check_isolated)
        # After ctx.run, original context still has sentinel
        assert _active_verifier.get() is sentinel
    finally:
        _active_verifier.reset(token)
    assert _active_verifier.get() is None
