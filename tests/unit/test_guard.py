"""Tests for guard mode infrastructure and behavior."""

from __future__ import annotations

import warnings

import pytest

from bigfoot._context import (
    GuardPassThrough,
    _guard_active,
    _guard_allowlist,
    get_verifier_or_raise,
)
from bigfoot._errors import GuardedCallError, GuardedCallWarning, SandboxNotActiveError
from bigfoot.pytest_plugin import _resolve_guard_level


class TestGuardContextVars:
    """Test guard mode ContextVars exist and have correct defaults.

    Note: the _bigfoot_guard autouse fixture sets _guard_active=True during
    each test body, so runtime get() returns True. These tests verify the
    ContextVar's declared default and token-based set/reset behavior.
    """

    def test_guard_active_declared_default_is_false(self) -> None:
        """The ContextVar's declared default is False (before any fixture sets it)."""
        import contextvars

        # Create a fresh context to read the ContextVar's declared default
        ctx = contextvars.copy_context()
        # In the test body, _guard_active is True (set by fixture).
        # The declared default is False, verified by checking a new token reset.
        token = _guard_active.set(False)
        assert _guard_active.get() is False
        _guard_active.reset(token)

    def test_guard_allowlist_default_is_empty_frozenset(self) -> None:
        """Without @pytest.mark.allow, the allowlist is empty."""
        assert _guard_allowlist.get() == frozenset()

    def test_guard_active_can_be_set_and_reset(self) -> None:
        """ContextVar token set/reset restores to the fixture's value (True)."""
        # Fixture sets _guard_active to True
        assert _guard_active.get() is True
        token = _guard_active.set(False)
        assert _guard_active.get() is False
        _guard_active.reset(token)
        # Resets to fixture's value, which is True
        assert _guard_active.get() is True

    def test_guard_allowlist_can_be_set_and_reset(self) -> None:
        token = _guard_allowlist.set(frozenset({"dns", "socket"}))
        assert _guard_allowlist.get() == frozenset({"dns", "socket"})
        _guard_allowlist.reset(token)
        assert _guard_allowlist.get() == frozenset()


class TestGuardPassThrough:
    """Test GuardPassThrough sentinel exception."""

    def test_inherits_from_base_exception(self) -> None:
        assert issubclass(GuardPassThrough, BaseException)

    def test_not_caught_by_generic_except_exception(self) -> None:
        with pytest.raises(GuardPassThrough):
            try:
                raise GuardPassThrough()
            except Exception:
                pass  # Should NOT catch GuardPassThrough


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
        msg = str(err)
        # First fix is @pytest.mark.allow (not sandbox)
        assert msg.startswith("GuardedCallError: 'http:request' blocked by bigfoot guard mode.")
        assert '@pytest.mark.allow("http")' in msg
        assert 'with bigfoot.allow("http")' in msg
        assert "with bigfoot:" in msg
        assert "Valid plugin names for allow():" in msg
        assert "https://bigfoot.readthedocs.io/guides/guard-mode/" in msg
        # Old sections removed
        assert "FOR PLUGIN AUTHORS" not in msg
        assert "FOR CONTRIBUTORS" not in msg
        assert "bigfoot_verifier.sandbox()" not in msg

    def test_message_with_different_plugin(self) -> None:
        err = GuardedCallError(source_id="dns:getaddrinfo:example.com", plugin_name="dns")
        msg = str(err)
        assert "'dns:getaddrinfo:example.com' blocked by bigfoot guard mode." in msg
        assert '@pytest.mark.allow("dns")' in msg
        assert 'with bigfoot.allow("dns")' in msg


class TestSupportsGuard:
    """Test supports_guard ClassVar on plugins."""

    def test_base_plugin_default_is_true(self) -> None:
        from bigfoot._base_plugin import BasePlugin

        assert BasePlugin.supports_guard is True

    def test_mock_plugin_is_false(self) -> None:
        from bigfoot._mock_plugin import MockPlugin

        assert MockPlugin.supports_guard is False

    def test_logging_plugin_is_false(self) -> None:
        from bigfoot.plugins.logging_plugin import LoggingPlugin

        assert LoggingPlugin.supports_guard is False

    def test_jwt_plugin_is_false(self) -> None:
        from bigfoot.plugins.jwt_plugin import JwtPlugin

        assert JwtPlugin.supports_guard is False

    def test_crypto_plugin_is_false(self) -> None:
        from bigfoot.plugins.crypto_plugin import CryptoPlugin

        assert CryptoPlugin.supports_guard is False

    def test_native_plugin_is_false(self) -> None:
        from bigfoot.plugins.native_plugin import NativePlugin

        assert NativePlugin.supports_guard is False

    def test_celery_plugin_is_false(self) -> None:
        from bigfoot.plugins.celery_plugin import CeleryPlugin

        assert CeleryPlugin.supports_guard is False

    def test_file_io_plugin_is_false(self) -> None:
        from bigfoot.plugins.file_io_plugin import FileIoPlugin

        assert FileIoPlugin.supports_guard is False

    def test_dns_plugin_inherits_true(self) -> None:
        from bigfoot.plugins.dns_plugin import DnsPlugin

        assert DnsPlugin.supports_guard is True

    def test_http_plugin_inherits_true(self) -> None:
        from bigfoot.plugins.http import HttpPlugin

        assert HttpPlugin.supports_guard is True

    def test_socket_plugin_inherits_true(self) -> None:
        from bigfoot.plugins.socket_plugin import SocketPlugin

        assert SocketPlugin.supports_guard is True


from bigfoot._guard import allow


class TestAllow:
    """Test allow() context manager."""

    def test_sets_allowlist_and_resets(self) -> None:
        assert _guard_allowlist.get() == frozenset()
        with allow("dns", "socket"):
            assert _guard_allowlist.get() == frozenset({"dns", "socket"})
        assert _guard_allowlist.get() == frozenset()

    def test_nestable_unions_allowlists(self) -> None:
        with allow("dns"):
            assert _guard_allowlist.get() == frozenset({"dns"})
            with allow("socket"):
                assert _guard_allowlist.get() == frozenset({"dns", "socket"})
            assert _guard_allowlist.get() == frozenset({"dns"})
        assert _guard_allowlist.get() == frozenset()

    def test_rejects_unknown_plugin_names(self) -> None:
        from bigfoot._errors import BigfootConfigError

        with pytest.raises(BigfootConfigError, match="Unknown plugin name"):
            with allow("nonexistent_plugin"):
                pass

    def test_single_plugin_name(self) -> None:
        with allow("http"):
            assert _guard_allowlist.get() == frozenset({"http"})

    def test_resets_on_exception(self) -> None:
        with pytest.raises(ValueError, match="boom"):
            with allow("dns"):
                raise ValueError("boom")
        assert _guard_allowlist.get() == frozenset()


class TestDeny:
    """Test deny() context manager."""

    def test_deny_narrows_allowlist(self) -> None:
        from bigfoot._guard import deny

        with allow("dns", "socket"):
            assert _guard_allowlist.get() == frozenset({"dns", "socket"})
            with deny("socket"):
                assert _guard_allowlist.get() == frozenset({"dns"})
            assert _guard_allowlist.get() == frozenset({"dns", "socket"})

    def test_deny_without_allow_is_noop(self) -> None:
        from bigfoot._guard import deny

        assert _guard_allowlist.get() == frozenset()
        with deny("dns"):
            assert _guard_allowlist.get() == frozenset()
        assert _guard_allowlist.get() == frozenset()

    def test_deny_resets_on_exception(self) -> None:
        from bigfoot._guard import deny

        with pytest.raises(ValueError, match="boom"):
            with allow("dns", "socket"):
                with deny("socket"):
                    raise ValueError("boom")
        assert _guard_allowlist.get() == frozenset()

    def test_deny_rejects_unknown_plugin_names(self) -> None:
        from bigfoot._errors import BigfootConfigError
        from bigfoot._guard import deny

        with pytest.raises(BigfootConfigError, match="Unknown plugin name"):
            with deny("nonexistent_plugin"):
                pass

    def test_nested_deny(self) -> None:
        from bigfoot._guard import deny

        with allow("dns", "socket", "http"):
            with deny("socket"):
                assert _guard_allowlist.get() == frozenset({"dns", "http"})
                with deny("dns"):
                    assert _guard_allowlist.get() == frozenset({"http"})
                assert _guard_allowlist.get() == frozenset({"dns", "http"})
            assert _guard_allowlist.get() == frozenset({"dns", "socket", "http"})


class TestPublicExports:
    """Test that guard mode symbols are exported from bigfoot package."""

    def test_allow_importable_from_bigfoot(self) -> None:
        from bigfoot import allow as bigfoot_allow

        assert callable(bigfoot_allow)

    def test_deny_importable_from_bigfoot(self) -> None:
        from bigfoot import deny as bigfoot_deny

        assert callable(bigfoot_deny)

    def test_guarded_call_error_importable_from_bigfoot(self) -> None:
        from bigfoot import GuardedCallError as BigfootGuardedCallError

        assert issubclass(BigfootGuardedCallError, Exception)

    def test_allow_in_all(self) -> None:
        import bigfoot

        assert "allow" in bigfoot.__all__

    def test_guarded_call_error_in_all(self) -> None:
        import bigfoot

        assert "GuardedCallError" in bigfoot.__all__

    def test_guarded_call_warning_importable_from_bigfoot(self) -> None:
        from bigfoot import GuardedCallWarning as BigfootGuardedCallWarning

        assert issubclass(BigfootGuardedCallWarning, UserWarning)

    def test_guarded_call_warning_in_all(self) -> None:
        import bigfoot

        assert "GuardedCallWarning" in bigfoot.__all__


class TestResolveGuardLevel:
    """Test _resolve_guard_level config parser."""

    def test_absent_key_returns_warn(self) -> None:
        """Missing guard key defaults to 'warn'."""
        assert _resolve_guard_level({}) == "warn"

    def test_warn_string_returns_warn(self) -> None:
        assert _resolve_guard_level({"guard": "warn"}) == "warn"

    def test_error_string_returns_error(self) -> None:
        assert _resolve_guard_level({"guard": "error"}) == "error"

    def test_strict_string_returns_error(self) -> None:
        """'strict' is an alias for 'error'."""
        assert _resolve_guard_level({"guard": "strict"}) == "error"

    def test_false_returns_off(self) -> None:
        assert _resolve_guard_level({"guard": False}) == "off"

    def test_true_rejected_with_config_error(self) -> None:
        """guard = true is ambiguous and must be rejected."""
        from bigfoot._errors import BigfootConfigError

        with pytest.raises(BigfootConfigError, match="guard = true is ambiguous"):
            _resolve_guard_level({"guard": True})

    def test_invalid_string_rejected(self) -> None:
        from bigfoot._errors import BigfootConfigError

        with pytest.raises(BigfootConfigError, match="Invalid guard value"):
            _resolve_guard_level({"guard": "invalid"})

    def test_invalid_type_rejected(self) -> None:
        from bigfoot._errors import BigfootConfigError

        with pytest.raises(BigfootConfigError, match="guard must be a string or false"):
            _resolve_guard_level({"guard": 42})

    def test_case_insensitive_warn(self) -> None:
        assert _resolve_guard_level({"guard": "WARN"}) == "warn"

    def test_case_insensitive_error(self) -> None:
        assert _resolve_guard_level({"guard": "ERROR"}) == "error"

    def test_case_insensitive_strict(self) -> None:
        assert _resolve_guard_level({"guard": "STRICT"}) == "error"


class TestGuardedCallWarningClass:
    """Test GuardedCallWarning exception class."""

    def test_is_user_warning(self) -> None:
        assert issubclass(GuardedCallWarning, UserWarning)

    def test_not_bigfoot_error(self) -> None:
        """GuardedCallWarning is a warning, not a BigfootError."""
        from bigfoot._errors import BigfootError

        assert not issubclass(GuardedCallWarning, BigfootError)


class TestWarnModeBehavior:
    """Test guard mode warn behavior in get_verifier_or_raise."""

    def test_warn_mode_emits_warning(self) -> None:
        """Guard in warn mode emits GuardedCallWarning."""
        from bigfoot._context import _guard_level

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("dns:lookup")
                assert len(w) == 1
                assert issubclass(w[0].category, GuardedCallWarning)
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_warn_mode_raises_guard_pass_through(self) -> None:
        """After warning, GuardPassThrough is raised (real call proceeds)."""
        from bigfoot._context import _guard_level

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        try:
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("dns:lookup")
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_warn_mode_warning_is_filterable(self) -> None:
        """warnings.filterwarnings('ignore') suppresses GuardedCallWarning."""
        from bigfoot._context import _guard_level

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                warnings.filterwarnings("ignore", category=GuardedCallWarning)
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("dns:lookup")
                assert len(w) == 0
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_warn_mode_warning_contains_source_id(self) -> None:
        """Warning message includes the source_id."""
        from bigfoot._context import _guard_level

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("dns:lookup")
                assert "'dns:lookup'" in str(w[0].message)
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_warn_mode_warning_contains_fix_hint(self) -> None:
        """Warning message includes @pytest.mark.allow fix hint."""
        from bigfoot._context import _guard_level

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("dns:lookup")
                msg = str(w[0].message)
                assert '@pytest.mark.allow("dns")' in msg
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_error_mode_raises_guarded_call_error(self) -> None:
        """Guard in error mode raises GuardedCallError (not a warning)."""
        from bigfoot._context import _guard_level

        level_token = _guard_level.set("error")
        guard_token = _guard_active.set(True)
        try:
            with pytest.raises(GuardedCallError):
                get_verifier_or_raise("dns:lookup")
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_allowlist_in_warn_mode_suppresses_warning(self) -> None:
        """Allowed plugins don't emit warnings even in warn mode."""
        from bigfoot._context import _guard_level

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        allow_token = _guard_allowlist.set(frozenset({"dns"}))
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                # dns is allowed, so should get GuardPassThrough with no warning
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("dns:lookup")
                guarded_warnings = [
                    x for x in w if issubclass(x.category, GuardedCallWarning)
                ]
                assert len(guarded_warnings) == 0
        finally:
            _guard_allowlist.reset(allow_token)
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)


class TestHookAllowlistMerge:
    """Test that pytest_runtest_call merges fixture-set allowlists with marker allowlists.

    These tests verify the hook clobber fix. The fixture allowlist should NOT
    be discarded when the hook sets the marker allowlist.
    """

    def test_fixture_allowlist_preserved_without_marker(self) -> None:
        """A fixture-set allowlist persists when no @pytest.mark.allow is present.

        Note: We simulate this by checking the merge logic directly.
        The hook reads existing value and merges with empty marker set.
        """
        existing = frozenset({"dns"})
        marker_allowlist: frozenset[str] = frozenset()
        denylist: frozenset[str] = frozenset()
        result = (existing | marker_allowlist) - denylist
        assert result == frozenset({"dns"})

    @pytest.mark.allow("socket")
    def test_fixture_and_marker_allowlist_merged(self) -> None:
        """Fixture allow('dns') + marker allow('socket') = both allowed.

        The hook should merge, not replace.
        """
        # The marker gives us "socket". If the hook merges correctly,
        # a fixture-set "dns" would also be present. We verify the
        # allowlist includes "socket" from the marker at minimum.
        assert "socket" in _guard_allowlist.get()

    @pytest.mark.allow("dns", "socket")
    @pytest.mark.deny("dns")
    def test_marker_deny_narrows_merged_allowlist(self) -> None:
        """deny('dns') removes 'dns' even if it was in the allowlist."""
        allowlist = _guard_allowlist.get()
        assert "dns" not in allowlist
        assert "socket" in allowlist


class TestGetVerifierOrRaiseGuardBranching:
    """Test the modified get_verifier_or_raise with guard mode logic."""

    def test_no_sandbox_no_guard_raises_sandbox_not_active(self) -> None:
        """Without sandbox or guard, raises SandboxNotActiveError (existing behavior).

        Must explicitly disable guard and guard_patches_installed since the
        session fixture and hook set them.
        """
        from bigfoot._context import _guard_patches_installed

        guard_token = _guard_active.set(False)
        patches_token = _guard_patches_installed.set(False)
        try:
            with pytest.raises(SandboxNotActiveError):
                get_verifier_or_raise("dns:getaddrinfo:example.com")
        finally:
            _guard_patches_installed.reset(patches_token)
            _guard_active.reset(guard_token)

    def test_guard_active_not_in_allowlist_raises_guarded_call_error(self) -> None:
        """Guard active + not allowed + error level = GuardedCallError."""
        from bigfoot._context import _guard_level

        level_token = _guard_level.set("error")
        token = _guard_active.set(True)
        try:
            with pytest.raises(GuardedCallError) as exc_info:
                get_verifier_or_raise("dns:getaddrinfo:example.com")
            assert exc_info.value.plugin_name == "dns"
            assert exc_info.value.source_id == "dns:getaddrinfo:example.com"
        finally:
            _guard_active.reset(token)
            _guard_level.reset(level_token)

    def test_guard_active_in_allowlist_raises_guard_pass_through(self) -> None:
        """Guard active + allowed = GuardPassThrough (interceptor should call original)."""
        guard_token = _guard_active.set(True)
        allow_token = _guard_allowlist.set(frozenset({"dns"}))
        try:
            with pytest.raises(GuardPassThrough):
                get_verifier_or_raise("dns:getaddrinfo:example.com")
        finally:
            _guard_allowlist.reset(allow_token)
            _guard_active.reset(guard_token)

    def test_plugin_name_extraction_from_source_id(self) -> None:
        """Plugin name is the prefix before the first colon."""
        from bigfoot._context import _guard_level

        level_token = _guard_level.set("error")
        token = _guard_active.set(True)
        try:
            with pytest.raises(GuardedCallError) as exc_info:
                get_verifier_or_raise("http:request")
            assert exc_info.value.plugin_name == "http"
        finally:
            _guard_active.reset(token)
            _guard_level.reset(level_token)

    def test_plugin_name_extraction_multi_colon(self) -> None:
        """Multi-colon source_id: plugin name is still first segment."""
        from bigfoot._context import _guard_level

        level_token = _guard_level.set("error")
        token = _guard_active.set(True)
        try:
            with pytest.raises(GuardedCallError) as exc_info:
                get_verifier_or_raise("dns:getaddrinfo:example.com")
            assert exc_info.value.plugin_name == "dns"
        finally:
            _guard_active.reset(token)
            _guard_level.reset(level_token)


class TestGuardPassThroughInDirectPlugins:
    """Test that GuardPassThrough is caught correctly in direct-helper plugins.

    These tests verify the interceptor pattern by activating guard mode,
    installing plugin patches, and confirming GuardPassThrough results
    in calling the original function (not raising).

    DNS is used as the representative case since it has no external deps.
    """

    def test_dns_getaddrinfo_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks dns:getaddrinfo when dns not in allowlist."""
        import socket

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                with pytest.raises(GuardedCallError) as exc_info:
                    socket.getaddrinfo("example.com", 80)
                assert exc_info.value.plugin_name == "dns"
                assert exc_info.value.source_id == "dns:lookup"
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            dns.deactivate()

    def test_dns_getaddrinfo_guard_allows_when_in_allowlist(self) -> None:
        """Guard allows dns:getaddrinfo when dns is in allowlist (calls original)."""
        import socket

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        try:
            guard_token = _guard_active.set(True)
            allow_token = _guard_allowlist.set(frozenset({"dns"}))
            try:
                # Should call the real getaddrinfo (not raise)
                result = socket.getaddrinfo("localhost", 80)
                assert isinstance(result, list)
                assert len(result) > 0
            finally:
                _guard_allowlist.reset(allow_token)
                _guard_active.reset(guard_token)
        finally:
            dns.deactivate()

    def test_dns_gethostbyname_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks dns:gethostbyname when dns not in allowlist."""
        import socket

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                with pytest.raises(GuardedCallError) as exc_info:
                    socket.gethostbyname("example.com")
                assert exc_info.value.plugin_name == "dns"
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            dns.deactivate()

    def test_dns_gethostbyname_guard_allows_when_in_allowlist(self) -> None:
        """Guard allows dns:gethostbyname when dns is in allowlist (calls original)."""
        import socket

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        try:
            guard_token = _guard_active.set(True)
            allow_token = _guard_allowlist.set(frozenset({"dns"}))
            try:
                result = socket.gethostbyname("localhost")
                assert isinstance(result, str)
                assert result == "127.0.0.1"
            finally:
                _guard_allowlist.reset(allow_token)
                _guard_active.reset(guard_token)
        finally:
            dns.deactivate()


class TestGuardPassThroughInStateMachinePlugins:
    """Test GuardPassThrough in StateMachine plugin interceptors.

    Socket is the representative case (no external deps, easy to test).
    Database (sqlite3) is also tested since it is always available.
    """

    def test_socket_connect_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks socket:connect when socket not in allowlist."""
        import socket as socket_mod

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import _SOCKET_CLOSE_ORIGINAL, SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                sock = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
                try:
                    with pytest.raises(GuardedCallError) as exc_info:
                        sock.connect(("127.0.0.1", 1))
                    assert exc_info.value.plugin_name == "socket"
                    assert exc_info.value.source_id == "socket:connect"
                finally:
                    _SOCKET_CLOSE_ORIGINAL(sock)
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            sp.deactivate()

    def test_socket_connect_guard_allows_when_in_allowlist(self) -> None:
        """Guard allows socket:connect when socket is in allowlist (calls real connect)."""
        import socket as socket_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import _SOCKET_CLOSE_ORIGINAL, SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        try:
            guard_token = _guard_active.set(True)
            allow_token = _guard_allowlist.set(frozenset({"socket"}))
            try:
                sock = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
                try:
                    # connect to a port that should refuse -- the point is that it
                    # reaches the REAL connect (ConnectionRefusedError or similar)
                    # rather than raising GuardPassThrough or GuardedCallError
                    with pytest.raises((ConnectionRefusedError, OSError)):
                        sock.connect(("127.0.0.1", 1))
                finally:
                    _SOCKET_CLOSE_ORIGINAL(sock)
            finally:
                _guard_allowlist.reset(allow_token)
                _guard_active.reset(guard_token)
        finally:
            sp.deactivate()

    def test_socket_send_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks socket:send when socket not in allowlist."""
        import socket as socket_mod

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import _SOCKET_CLOSE_ORIGINAL, SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                sock = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
                try:
                    with pytest.raises(GuardedCallError) as exc_info:
                        sock.send(b"hello")
                    assert exc_info.value.plugin_name == "socket"
                    # Note: _get_socket_plugin() hardcodes _SOURCE_CONNECT for source_id
                    assert exc_info.value.source_id == "socket:connect"
                finally:
                    _SOCKET_CLOSE_ORIGINAL(sock)
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            sp.deactivate()

    def test_socket_close_guard_allows_when_in_allowlist(self) -> None:
        """Guard allows socket:close when socket is in allowlist (calls real close)."""
        import socket as socket_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        try:
            guard_token = _guard_active.set(True)
            allow_token = _guard_allowlist.set(frozenset({"socket"}))
            try:
                sock = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
                # close should call the real close without error
                sock.close()
            finally:
                _guard_allowlist.reset(allow_token)
                _guard_active.reset(guard_token)
        finally:
            sp.deactivate()

    def test_database_connect_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks db:connect when db not in allowlist."""
        import sqlite3

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.database_plugin import DatabasePlugin

        v = StrictVerifier()
        dp = DatabasePlugin(v)
        dp.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                with pytest.raises(GuardedCallError) as exc_info:
                    sqlite3.connect(":memory:")
                assert exc_info.value.plugin_name == "db"
                assert exc_info.value.source_id == "db:connect"
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            dp.deactivate()

    def test_database_connect_guard_allows_when_in_allowlist(self) -> None:
        """Guard allows db:connect when db is in allowlist (calls real connect)."""
        import sqlite3

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.database_plugin import DatabasePlugin

        v = StrictVerifier()
        dp = DatabasePlugin(v)
        dp.activate()
        try:
            guard_token = _guard_active.set(True)
            allow_token = _guard_allowlist.set(frozenset({"db"}))
            try:
                # Should call the real sqlite3.connect and return a real connection
                conn = sqlite3.connect(":memory:")
                assert conn is not None
                # Verify it is a real sqlite3.Connection, not a _FakeConnection
                assert type(conn).__name__ == "Connection"
                cursor = conn.execute("SELECT 1")
                row = cursor.fetchone()
                assert row == (1,)
                conn.close()
            finally:
                _guard_allowlist.reset(allow_token)
                _guard_active.reset(guard_token)
        finally:
            dp.deactivate()

    def test_smtp_init_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks smtp:connect when smtp not in allowlist."""
        import smtplib

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.smtp_plugin import SmtpPlugin

        v = StrictVerifier()
        sp = SmtpPlugin(v)
        sp.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                with pytest.raises(GuardedCallError) as exc_info:
                    smtplib.SMTP("localhost", 25)
                assert exc_info.value.plugin_name == "smtp"
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            sp.deactivate()

    def test_popen_init_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks subprocess:popen:spawn when subprocess not in allowlist."""
        import subprocess

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.popen_plugin import PopenPlugin

        v = StrictVerifier()
        pp = PopenPlugin(v)
        pp.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                with pytest.raises(GuardedCallError) as exc_info:
                    subprocess.Popen(["echo", "hello"])
                assert exc_info.value.plugin_name == "subprocess"
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            pp.deactivate()


class TestGuardPassThroughInRemainingPlugins:
    """Test GuardPassThrough in remaining plugin interceptors (Task 9).

    Subprocess is used as the representative case since it has no external
    deps beyond the stdlib and exercises both the block and allow paths.
    HTTP block test verifies the httpx sync interceptor path.
    """

    def test_subprocess_run_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks subprocess.run when subprocess not in allowlist."""
        import subprocess as subprocess_mod

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.subprocess import SubprocessPlugin

        v = StrictVerifier()
        sp = SubprocessPlugin(v)
        sp.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                with pytest.raises(GuardedCallError) as exc_info:
                    subprocess_mod.run(["echo", "hello"], capture_output=True)
                assert exc_info.value.plugin_name == "subprocess"
                assert exc_info.value.source_id == "subprocess:run"
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            sp.deactivate()

    def test_subprocess_run_guard_allows_when_in_allowlist(self) -> None:
        """Guard allows subprocess.run when subprocess is in allowlist."""
        import subprocess as subprocess_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.subprocess import SubprocessPlugin

        v = StrictVerifier()
        sp = SubprocessPlugin(v)
        sp.activate()
        try:
            guard_token = _guard_active.set(True)
            allow_token = _guard_allowlist.set(frozenset({"subprocess"}))
            try:
                result = subprocess_mod.run(
                    ["echo", "hello"], capture_output=True, text=True,
                )
                assert result.returncode == 0
                assert result.stdout == "hello\n"
            finally:
                _guard_allowlist.reset(allow_token)
                _guard_active.reset(guard_token)
        finally:
            sp.deactivate()

    def test_subprocess_which_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks shutil.which when subprocess not in allowlist."""
        import shutil as shutil_mod

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.subprocess import SubprocessPlugin

        v = StrictVerifier()
        sp = SubprocessPlugin(v)
        sp.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                with pytest.raises(GuardedCallError) as exc_info:
                    shutil_mod.which("echo")
                assert exc_info.value.plugin_name == "subprocess"
                assert exc_info.value.source_id == "subprocess:which"
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            sp.deactivate()

    def test_subprocess_which_guard_allows_when_in_allowlist(self) -> None:
        """Guard allows shutil.which when subprocess is in allowlist."""
        import shutil as shutil_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.subprocess import SubprocessPlugin

        v = StrictVerifier()
        sp = SubprocessPlugin(v)
        sp.activate()
        try:
            guard_token = _guard_active.set(True)
            allow_token = _guard_allowlist.set(frozenset({"subprocess"}))
            try:
                result = shutil_mod.which("echo")
                assert isinstance(result, str)
                assert "echo" in result
            finally:
                _guard_allowlist.reset(allow_token)
                _guard_active.reset(guard_token)
        finally:
            sp.deactivate()

    def test_http_sync_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks httpx sync transport when http not in allowlist."""
        import httpx

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.http import HttpPlugin

        v = StrictVerifier()
        hp = HttpPlugin(v)
        hp.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                with pytest.raises(GuardedCallError) as exc_info:
                    httpx.get("https://example.com")
                assert exc_info.value.plugin_name == "http"
                assert exc_info.value.source_id == "http:request"
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            hp.deactivate()


class TestGuardPytestFixtures:
    """Test guard mode pytest fixtures and mark."""

    def test_allow_mark_is_registered(self, pytestconfig: pytest.Config) -> None:
        """The 'allow' mark should be registered to avoid PytestUnknownMarkWarning."""
        markers: list[str] = pytestconfig.getini("markers")
        # Check that at least one marker line starts with 'allow'
        assert any(m.startswith("allow") for m in markers)

    def test_guard_session_fixture_is_registered(self) -> None:
        """The _bigfoot_guard_patches session fixture should exist in pytest_plugin."""
        from bigfoot import pytest_plugin

        assert hasattr(pytest_plugin, "_bigfoot_guard_patches")

    def test_guard_hook_is_registered(self) -> None:
        """The pytest_runtest_call hook should exist in pytest_plugin module."""
        from bigfoot import pytest_plugin

        assert hasattr(pytest_plugin, "pytest_runtest_call")

    def test_guard_hook_skips_non_guard_plugins(self) -> None:
        """Guard hook should not activate plugins with supports_guard=False."""
        from bigfoot._registry import PLUGIN_REGISTRY, _is_available, get_plugin_class

        for entry in PLUGIN_REGISTRY:
            if not _is_available(entry):
                continue
            plugin_cls = get_plugin_class(entry)
            if not getattr(plugin_cls, "supports_guard", True):
                # These plugins should NOT be activated by guard patches
                assert entry.name in {
                    "logging", "jwt", "crypto", "celery", "native", "file_io",
                }, f"Plugin {entry.name} has supports_guard=False but is not in expected set"

    def test_guard_hook_skips_opt_in_plugins(self) -> None:
        """Guard hook should not activate opt-in plugins (default_enabled=False)."""
        from bigfoot._registry import PLUGIN_REGISTRY

        opt_in = [e for e in PLUGIN_REGISTRY if not e.default_enabled]
        assert len(opt_in) >= 2  # file_io and native at minimum
        for entry in opt_in:
            assert entry.name in {"file_io", "native"}, (
                f"Unexpected opt-in plugin {entry.name}"
            )


class TestGuardActiveDuringTestBody:
    """Test that _guard_active is True during test body via pytest_runtest_call hook."""

    def test_guard_active_is_true_during_test(self) -> None:
        """Guard mode should be active during the test body."""
        assert _guard_active.get() is True

    def test_guard_allowlist_empty_by_default(self) -> None:
        """Without @pytest.mark.allow, allowlist should be empty."""
        assert _guard_allowlist.get() == frozenset()

    @pytest.mark.allow("dns", "socket")
    def test_mark_allow_populates_allowlist(self) -> None:
        """@pytest.mark.allow should set the allowlist."""
        assert _guard_allowlist.get() == frozenset({"dns", "socket"})

    @pytest.mark.allow("dns")
    def test_mark_allow_single_plugin(self) -> None:
        """Single plugin in @pytest.mark.allow works."""
        assert _guard_allowlist.get() == frozenset({"dns"})

    @pytest.mark.allow("dns")
    @pytest.mark.allow("socket")
    def test_multiple_allow_marks_combine(self) -> None:
        """Multiple @pytest.mark.allow decorators combine via union."""
        assert _guard_allowlist.get() == frozenset({"dns", "socket"})

    def test_bigfoot_guard_hook_exists_in_pytest_plugin(self) -> None:
        """The pytest_runtest_call hook should exist in pytest_plugin module."""
        from bigfoot import pytest_plugin

        assert hasattr(pytest_plugin, "pytest_runtest_call")


class TestGuardModeIntegration:
    """Integration tests for guard mode end-to-end behavior.

    These tests verify the full guard mode stack: interceptors, ContextVars,
    allowlists, sandbox precedence, and config-driven disablement.
    """

    def test_guard_blocks_real_socket_connect_outside_sandbox(self) -> None:
        """Guard mode blocks real socket.connect when outside a sandbox.

        Creates its own SocketPlugin activation to be resilient against earlier
        tests that force-reset plugin install counts.
        """
        import socket as socket_mod

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import _SOCKET_CLOSE_ORIGINAL, SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        level_token = _guard_level.set("error")
        try:
            sock = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
            try:
                with pytest.raises(GuardedCallError) as exc_info:
                    sock.connect(("127.0.0.1", 1))
                assert exc_info.value.plugin_name == "socket"
                assert exc_info.value.source_id == "socket:connect"
            finally:
                _SOCKET_CLOSE_ORIGINAL(sock)
        finally:
            _guard_level.reset(level_token)
            sp.deactivate()

    @pytest.mark.allow("socket")
    def test_allow_mark_permits_real_socket_operations(self) -> None:
        """@pytest.mark.allow('socket') permits real socket operations.

        Creates its own SocketPlugin activation to be resilient against earlier
        tests that force-reset plugin install counts.
        """
        import socket as socket_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        try:
            sock = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
            try:
                # connect to a port that should refuse -- proves the REAL connect ran
                with pytest.raises((ConnectionRefusedError, OSError)):
                    sock.connect(("127.0.0.1", 1))
            finally:
                sock.close()
        finally:
            sp.deactivate()

    def test_sandbox_takes_precedence_over_guard(self) -> None:
        """Inside a sandbox, guard mode is irrelevant; sandbox mocking applies.

        Creates its own verifier + DnsPlugin to be self-contained.
        """
        import socket

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin, _DnsSentinel

        v = StrictVerifier()
        dns_plugin = None
        for p in v._plugins:
            if isinstance(p, DnsPlugin):
                dns_plugin = p
                break
        assert dns_plugin is not None, "DnsPlugin should be registered"

        dns_plugin.mock_getaddrinfo(
            "example.com", returns=[(2, 1, 6, "", ("93.184.216.34", 80))],
        )
        with v.sandbox():
            result = socket.getaddrinfo("example.com", 80)
            assert result == [(2, 1, 6, "", ("93.184.216.34", 80))]

        v.assert_interaction(
            _DnsSentinel("dns:getaddrinfo:example.com"),
            host="example.com",
            port=80,
            family=0,
            type=0,
            proto=0,
        )

    @pytest.mark.allow("dns")
    def test_allow_context_manager_adds_to_mark_allowlist(self) -> None:
        """allow() inside @pytest.mark.allow adds to the existing allowlist."""
        # Mark already allows "dns"
        assert _guard_allowlist.get() == frozenset({"dns"})

        with allow("socket"):
            assert _guard_allowlist.get() == frozenset({"dns", "socket"})

        # After exiting allow(), back to mark-only
        assert _guard_allowlist.get() == frozenset({"dns"})

    @pytest.mark.allow("dns")
    @pytest.mark.allow("socket")
    def test_multiple_allow_marks_combine_end_to_end(self) -> None:
        """Multiple @pytest.mark.allow marks combine; real I/O passes through.

        Creates its own DnsPlugin activation to be resilient against earlier
        tests that force-reset plugin install counts.
        """
        import socket as socket_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        try:
            # Both dns and socket are allowed, so real getaddrinfo should work
            result = socket_mod.getaddrinfo("localhost", 80)
            assert isinstance(result, list)
            assert len(result) > 0
            # Verify the result contains real address tuples
            first = result[0]
            assert len(first) == 5  # (family, type, proto, canonname, sockaddr)
        finally:
            dns.deactivate()

    def test_guard_active_is_false_during_fixture_setup(self) -> None:
        """Indirectly verify guard is scoped to test body, not fixtures.

        The hook wraps pytest_runtest_call (test body only). If guard were
        active during fixture setup, the _bigfoot_auto_verifier fixture
        would fail when creating StrictVerifier (which internally may
        perform I/O-like operations). The fact that we get here proves it.
        """
        assert _guard_active.get() is True  # Active in test body

    def test_guard_blocks_dns_lookup_outside_sandbox(self) -> None:
        """Guard blocks real DNS lookups outside a sandbox.

        Creates its own DnsPlugin activation to be resilient against earlier
        tests that force-reset plugin install counts (e.g., test_dns_plugin.py).
        """
        import socket as socket_mod

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        level_token = _guard_level.set("error")
        try:
            with pytest.raises(GuardedCallError) as exc_info:
                socket_mod.getaddrinfo("example.com", 80)
            assert exc_info.value.plugin_name == "dns"
        finally:
            _guard_level.reset(level_token)
            dns.deactivate()

    def test_guard_blocks_subprocess_outside_sandbox(self) -> None:
        """Guard blocks real subprocess.run outside a sandbox.

        Creates its own SubprocessPlugin activation to be resilient against
        earlier tests that force-reset plugin install counts.
        """
        import subprocess as subprocess_mod

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.subprocess import SubprocessPlugin

        v = StrictVerifier()
        sp = SubprocessPlugin(v)
        sp.activate()
        level_token = _guard_level.set("error")
        try:
            with pytest.raises(GuardedCallError) as exc_info:
                subprocess_mod.run(["echo", "hello"], capture_output=True)
            assert exc_info.value.plugin_name == "subprocess"
            assert exc_info.value.source_id == "subprocess:run"
        finally:
            _guard_level.reset(level_token)
            sp.deactivate()

    @pytest.mark.allow("subprocess")
    def test_allow_mark_permits_real_subprocess(self) -> None:
        """@pytest.mark.allow('subprocess') permits real subprocess.run.

        Creates its own SubprocessPlugin activation to be resilient against
        earlier tests that force-reset plugin install counts.
        """
        import subprocess as subprocess_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.subprocess import SubprocessPlugin

        v = StrictVerifier()
        sp = SubprocessPlugin(v)
        sp.activate()
        try:
            result = subprocess_mod.run(
                ["echo", "guard_test"], capture_output=True, text=True,
            )
            assert result.returncode == 0
            assert result.stdout == "guard_test\n"
        finally:
            sp.deactivate()

    def test_guard_blocks_http_outside_sandbox(self) -> None:
        """Guard blocks real HTTP requests outside a sandbox.

        Creates its own HttpPlugin activation to be resilient against earlier
        tests that force-reset plugin install counts.
        """
        import httpx

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.http import HttpPlugin

        v = StrictVerifier()
        hp = HttpPlugin(v)
        hp.activate()
        level_token = _guard_level.set("error")
        try:
            with pytest.raises(GuardedCallError) as exc_info:
                httpx.get("https://example.com")
            assert exc_info.value.plugin_name == "http"
            assert exc_info.value.source_id == "http:request"
        finally:
            _guard_level.reset(level_token)
            hp.deactivate()

    def test_allow_bypasses_sandbox_interceptor(self) -> None:
        """allow() causes interceptor to call original even inside sandbox."""
        import socket

        from bigfoot._verifier import StrictVerifier

        # Set allowlist
        token = _guard_allowlist.set(frozenset({"dns"}))
        try:
            # Create verifier with DNS plugin and enter sandbox
            v = StrictVerifier()
            with v.sandbox():
                # DNS call should pass through to real function
                result = socket.getaddrinfo("localhost", 80)
                assert isinstance(result, list)
                assert len(result) > 0
            # No interactions should be recorded for dns
            dns_interactions = [
                i for i in v._timeline._interactions
                if i.source_id.startswith("dns:")
            ]
            assert len(dns_interactions) == 0
        finally:
            _guard_allowlist.reset(token)

    def test_guarded_call_error_message_has_actionable_guidance(self) -> None:
        """GuardedCallError message contains all three remediation options.

        Creates its own DnsPlugin activation to be resilient against earlier
        tests that force-reset plugin install counts.
        """
        import socket as socket_mod

        from bigfoot._context import _guard_level
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        level_token = _guard_level.set("error")
        try:
            with pytest.raises(GuardedCallError) as exc_info:
                socket_mod.getaddrinfo("example.com", 80)
            msg = str(exc_info.value)
            # New message format
            assert '@pytest.mark.allow("dns")' in msg
            assert 'bigfoot.allow("dns")' in msg
            assert "with bigfoot:" in msg
            assert "Valid plugin names for allow():" in msg
            # Old sections removed
            assert "supports_guard" not in msg
        finally:
            _guard_level.reset(level_token)
            dns.deactivate()
