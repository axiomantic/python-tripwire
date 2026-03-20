"""Tests for guard mode infrastructure and behavior."""

from __future__ import annotations

import pytest

from bigfoot._context import (
    _guard_active,
    _guard_allowlist,
    _GuardPassThrough,
)


class TestGuardContextVars:
    """Test guard mode ContextVars exist and have correct defaults."""

    def test_guard_active_default_is_false(self) -> None:
        assert _guard_active.get() is False

    def test_guard_allowlist_default_is_empty_frozenset(self) -> None:
        assert _guard_allowlist.get() == frozenset()

    def test_guard_active_can_be_set_and_reset(self) -> None:
        token = _guard_active.set(True)
        assert _guard_active.get() is True
        _guard_active.reset(token)
        assert _guard_active.get() is False

    def test_guard_allowlist_can_be_set_and_reset(self) -> None:
        token = _guard_allowlist.set(frozenset({"dns", "socket"}))
        assert _guard_allowlist.get() == frozenset({"dns", "socket"})
        _guard_allowlist.reset(token)
        assert _guard_allowlist.get() == frozenset()


class TestGuardPassThrough:
    """Test _GuardPassThrough sentinel exception."""

    def test_inherits_from_base_exception(self) -> None:
        assert issubclass(_GuardPassThrough, BaseException)

    def test_not_caught_by_generic_except_exception(self) -> None:
        with pytest.raises(_GuardPassThrough):
            try:
                raise _GuardPassThrough()
            except Exception:
                pass  # Should NOT catch _GuardPassThrough
