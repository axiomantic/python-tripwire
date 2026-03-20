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


from bigfoot._errors import GuardedCallError


class TestGuardedCallError:
    """Test GuardedCallError exception class."""

    def test_inherits_from_bigfoot_error(self) -> None:
        from bigfoot._errors import BigfootError

        assert issubclass(GuardedCallError, BigfootError)

    def test_stores_source_id_and_plugin_name(self) -> None:
        err = GuardedCallError(source_id="dns:getaddrinfo:example.com", plugin_name="dns")
        assert err.source_id == "dns:getaddrinfo:example.com"
        assert err.plugin_name == "dns"

    def test_message_format(self) -> None:
        err = GuardedCallError(source_id="http:request", plugin_name="http")
        expected = "\n".join([
            "GuardedCallError: 'http:request' blocked by bigfoot guard mode.",
            "",
            "  FOR TEST AUTHORS:",
            "    Option 1: Use a sandbox to mock this call:",
            "      with bigfoot_verifier.sandbox():",
            "          # ... your code ...",
            "    Option 2: Explicitly allow this call (no assertion tracking):",
            '      with bigfoot.allow("http"):',
            "          # ... your code ...",
            "    Option 3: Allow via pytest mark (entire test):",
            '      @pytest.mark.allow("http")',
            "      def test_something():",
            "          ...",
            "",
            "  FOR PLUGIN AUTHORS:",
            "    If this plugin does not perform real I/O, set:",
            "      supports_guard: ClassVar[bool] = False",
            "",
            "  FOR CONTRIBUTORS:",
            "    To add guard support to a new I/O plugin:",
            "    1. Keep supports_guard = True (the default)",
            "    2. Add try/except _GuardPassThrough to each interceptor",
            "    3. On _GuardPassThrough, call the original function",
        ])
        assert str(err) == expected

    def test_message_with_different_plugin(self) -> None:
        err = GuardedCallError(source_id="dns:getaddrinfo:example.com", plugin_name="dns")
        msg = str(err)
        assert msg == "\n".join([
            "GuardedCallError: 'dns:getaddrinfo:example.com' blocked by bigfoot guard mode.",
            "",
            "  FOR TEST AUTHORS:",
            "    Option 1: Use a sandbox to mock this call:",
            "      with bigfoot_verifier.sandbox():",
            "          # ... your code ...",
            "    Option 2: Explicitly allow this call (no assertion tracking):",
            '      with bigfoot.allow("dns"):',
            "          # ... your code ...",
            "    Option 3: Allow via pytest mark (entire test):",
            '      @pytest.mark.allow("dns")',
            "      def test_something():",
            "          ...",
            "",
            "  FOR PLUGIN AUTHORS:",
            "    If this plugin does not perform real I/O, set:",
            "      supports_guard: ClassVar[bool] = False",
            "",
            "  FOR CONTRIBUTORS:",
            "    To add guard support to a new I/O plugin:",
            "    1. Keep supports_guard = True (the default)",
            "    2. Add try/except _GuardPassThrough to each interceptor",
            "    3. On _GuardPassThrough, call the original function",
        ])
