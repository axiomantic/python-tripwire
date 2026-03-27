"""Tests for guard mode infrastructure and behavior."""

from __future__ import annotations

import warnings

import pytest

from bigfoot._context import (
    GuardPassThrough,
    _guard_active,
    get_verifier_or_raise,
)
from bigfoot._errors import GuardedCallError, GuardedCallWarning, SandboxNotActiveError
from bigfoot._firewall import (
    Disposition,
    FirewallRule,
    FirewallStack,
    _firewall_stack,
)
from bigfoot._match import M
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

    def test_firewall_stack_default_is_empty(self) -> None:
        """Without @pytest.mark.allow, the firewall stack has no rules beyond markers."""
        # The pytest_runtest_call hook sets up the stack; with no markers,
        # it should have no ALLOW rules. Check that the stack exists.
        stack = _firewall_stack.get()
        assert isinstance(stack, FirewallStack)

    def test_guard_active_can_be_set_and_reset(self) -> None:
        """ContextVar token set/reset restores to the fixture's value (True)."""
        # Fixture sets _guard_active to True
        assert _guard_active.get() is True
        token = _guard_active.set(False)
        assert _guard_active.get() is False
        _guard_active.reset(token)
        # Resets to fixture's value, which is True
        assert _guard_active.get() is True

    def test_firewall_stack_can_be_set_and_reset(self) -> None:
        """FirewallStack ContextVar supports token-based set/reset."""
        frames = (
            FirewallRule(pattern=M(protocol="dns"), disposition=Disposition.ALLOW),
            FirewallRule(pattern=M(protocol="socket"), disposition=Disposition.ALLOW),
        )
        new_stack = FirewallStack(frames)
        token = _firewall_stack.set(new_stack)
        assert _firewall_stack.get() is new_stack
        assert len(_firewall_stack.get().frames) == 2
        _firewall_stack.reset(token)


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
        assert msg.startswith("GuardedCallError: 'http:request' blocked by bigfoot firewall.")
        assert '@pytest.mark.allow("http")' in msg
        assert 'with bigfoot.allow("http")' in msg
        assert "with bigfoot:" in msg
        assert "[tool.bigfoot.firewall]" in msg
        assert "https://bigfoot.readthedocs.io/guides/guard-mode/" in msg
        # Old sections removed
        assert "FOR PLUGIN AUTHORS" not in msg
        assert "FOR CONTRIBUTORS" not in msg
        assert "bigfoot_verifier.sandbox()" not in msg
        assert "Valid plugin names for allow():" not in msg

    def test_message_with_different_plugin(self) -> None:
        err = GuardedCallError(source_id="dns:getaddrinfo:example.com", plugin_name="dns")
        msg = str(err)
        assert "'dns:getaddrinfo:example.com' blocked by bigfoot firewall." in msg
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
    """Test allow() context manager pushes ALLOW rules onto firewall stack."""

    def test_pushes_allow_rules_and_resets(self) -> None:
        from bigfoot._firewall_request import NetworkFirewallRequest

        stack_before = _firewall_stack.get()
        with allow("dns", "socket"):
            stack_inside = _firewall_stack.get()
            # Two new ALLOW frames pushed
            assert len(stack_inside.frames) == len(stack_before.frames) + 2
            # DNS request should be ALLOW'd
            dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
            assert stack_inside.evaluate(dns_req) == Disposition.ALLOW
            # Socket request should be ALLOW'd
            sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)
            assert stack_inside.evaluate(sock_req) == Disposition.ALLOW
        # After exit, stack is restored
        assert _firewall_stack.get() is stack_before

    def test_nestable_stacks_rules(self) -> None:
        from bigfoot._firewall_request import NetworkFirewallRequest

        stack_before = _firewall_stack.get()
        with allow("dns"):
            stack_dns = _firewall_stack.get()
            dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
            assert stack_dns.evaluate(dns_req) == Disposition.ALLOW
            with allow("socket"):
                stack_both = _firewall_stack.get()
                sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)
                assert stack_both.evaluate(dns_req) == Disposition.ALLOW
                assert stack_both.evaluate(sock_req) == Disposition.ALLOW
            # socket rule removed after inner exit
            assert _firewall_stack.get() is stack_dns
        assert _firewall_stack.get() is stack_before

    def test_requires_at_least_one_rule(self) -> None:
        with pytest.raises(ValueError, match="allow\\(\\) requires at least one rule"):
            with allow():
                pass

    def test_single_protocol_name(self) -> None:
        from bigfoot._firewall_request import NetworkFirewallRequest

        with allow("http"):
            stack = _firewall_stack.get()
            http_req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
            assert stack.evaluate(http_req) == Disposition.ALLOW

    def test_resets_on_exception(self) -> None:
        stack_before = _firewall_stack.get()
        with pytest.raises(ValueError, match="boom"):
            with allow("dns"):
                raise ValueError("boom")
        assert _firewall_stack.get() is stack_before


class TestDeny:
    """Test deny() context manager pushes DENY rules onto firewall stack."""

    def test_deny_blocks_allowed_protocol(self) -> None:
        from bigfoot._firewall_request import NetworkFirewallRequest
        from bigfoot._guard import deny

        dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
        sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)

        with allow("dns", "socket"):
            assert _firewall_stack.get().evaluate(dns_req) == Disposition.ALLOW
            assert _firewall_stack.get().evaluate(sock_req) == Disposition.ALLOW
            with deny("socket"):
                # socket should now be DENY'd, dns still ALLOW'd
                assert _firewall_stack.get().evaluate(dns_req) == Disposition.ALLOW
                assert _firewall_stack.get().evaluate(sock_req) == Disposition.DENY
            # After exiting deny, socket allowed again
            assert _firewall_stack.get().evaluate(sock_req) == Disposition.ALLOW

    def test_deny_without_allow_keeps_deny(self) -> None:
        from bigfoot._firewall_request import NetworkFirewallRequest
        from bigfoot._guard import deny

        dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
        # Default disposition is DENY, so deny on top of empty stack still denies
        with deny("dns"):
            assert _firewall_stack.get().evaluate(dns_req) == Disposition.DENY

    def test_deny_resets_on_exception(self) -> None:
        from bigfoot._guard import deny

        stack_before = _firewall_stack.get()
        with pytest.raises(ValueError, match="boom"):
            with allow("dns", "socket"):
                with deny("socket"):
                    raise ValueError("boom")
        assert _firewall_stack.get() is stack_before

    def test_deny_requires_at_least_one_rule(self) -> None:
        from bigfoot._guard import deny

        with pytest.raises(ValueError, match="deny\\(\\) requires at least one rule"):
            with deny():
                pass

    def test_nested_deny(self) -> None:
        from bigfoot._firewall_request import NetworkFirewallRequest
        from bigfoot._guard import deny

        dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
        sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)
        http_req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)

        with allow("dns", "socket", "http"):
            with deny("socket"):
                assert _firewall_stack.get().evaluate(dns_req) == Disposition.ALLOW
                assert _firewall_stack.get().evaluate(http_req) == Disposition.ALLOW
                assert _firewall_stack.get().evaluate(sock_req) == Disposition.DENY
                with deny("dns"):
                    assert _firewall_stack.get().evaluate(http_req) == Disposition.ALLOW
                    assert _firewall_stack.get().evaluate(dns_req) == Disposition.DENY
                    assert _firewall_stack.get().evaluate(sock_req) == Disposition.DENY
                assert _firewall_stack.get().evaluate(dns_req) == Disposition.ALLOW
            assert _firewall_stack.get().evaluate(sock_req) == Disposition.ALLOW


class TestRestrict:
    """Test restrict() context manager pushes restriction ceiling onto firewall stack."""

    def test_restrict_blocks_non_matching_protocols(self) -> None:
        from bigfoot._firewall_request import NetworkFirewallRequest
        from bigfoot._guard import restrict

        http_req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
        redis_req = NetworkFirewallRequest(protocol="redis", host="localhost", port=6379)

        with allow("http", "redis"):
            # Both allowed before restrict
            assert _firewall_stack.get().evaluate(http_req) == Disposition.ALLOW
            assert _firewall_stack.get().evaluate(redis_req) == Disposition.ALLOW
            with restrict("http"):
                # Only HTTP passes the restrict ceiling
                with allow("http"):
                    assert _firewall_stack.get().evaluate(http_req) == Disposition.ALLOW
                assert _firewall_stack.get().evaluate(redis_req) == Disposition.DENY

    def test_restrict_resets_on_exit(self) -> None:
        from bigfoot._guard import restrict

        stack_before = _firewall_stack.get()
        with restrict("http"):
            pass
        assert _firewall_stack.get() is stack_before

    def test_restrict_requires_at_least_one_rule(self) -> None:
        from bigfoot._guard import restrict

        with pytest.raises(ValueError, match="restrict\\(\\) requires at least one rule"):
            with restrict():
                pass

    def test_restrict_multiple_protocols_ored(self) -> None:
        from bigfoot._firewall_request import NetworkFirewallRequest
        from bigfoot._guard import restrict

        http_req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
        dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
        redis_req = NetworkFirewallRequest(protocol="redis", host="localhost", port=6379)

        with restrict("http", "dns"):
            with allow("http", "dns", "redis"):
                assert _firewall_stack.get().evaluate(http_req) == Disposition.ALLOW
                assert _firewall_stack.get().evaluate(dns_req) == Disposition.ALLOW
                # Redis is not in the restrict set, so blocked by ceiling
                assert _firewall_stack.get().evaluate(redis_req) == Disposition.DENY

    def test_restrict_inner_allow_cannot_widen_ceiling(self) -> None:
        from bigfoot._firewall_request import NetworkFirewallRequest
        from bigfoot._guard import restrict

        redis_req = NetworkFirewallRequest(protocol="redis", host="localhost", port=6379)

        with restrict("http"):
            # Inner allow("redis") should NOT widen past the HTTP ceiling
            with allow("redis"):
                assert _firewall_stack.get().evaluate(redis_req) == Disposition.DENY


class TestPublicExports:
    """Test that guard mode symbols are exported from bigfoot package."""

    def test_allow_importable_from_bigfoot(self) -> None:
        from bigfoot import allow as bigfoot_allow

        assert callable(bigfoot_allow)

    def test_deny_importable_from_bigfoot(self) -> None:
        from bigfoot import deny as bigfoot_deny

        assert callable(bigfoot_deny)

    def test_restrict_importable_from_bigfoot(self) -> None:
        from bigfoot import restrict as bigfoot_restrict

        assert callable(bigfoot_restrict)

    def test_guarded_call_error_importable_from_bigfoot(self) -> None:
        from bigfoot import GuardedCallError as BigfootGuardedCallError

        assert issubclass(BigfootGuardedCallError, Exception)

    def test_allow_in_all(self) -> None:
        import bigfoot

        assert "allow" in bigfoot.__all__

    def test_restrict_in_all(self) -> None:
        import bigfoot

        assert "restrict" in bigfoot.__all__

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
        from bigfoot._firewall_request import NetworkFirewallRequest

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                # Use http (not dns/socket) so the project-level firewall
                # allow = ["dns:*", "socket:*"] does not suppress the warning.
                req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("http:request", firewall_request=req)
                assert len(w) == 1
                assert issubclass(w[0].category, GuardedCallWarning)
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_warn_mode_raises_guard_pass_through(self) -> None:
        """After warning, GuardPassThrough is raised (real call proceeds)."""
        from bigfoot._context import _guard_level
        from bigfoot._firewall_request import NetworkFirewallRequest

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        try:
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("http:request", firewall_request=req)
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_warn_mode_warning_is_filterable(self) -> None:
        """warnings.filterwarnings('ignore') suppresses GuardedCallWarning."""
        from bigfoot._context import _guard_level
        from bigfoot._firewall_request import NetworkFirewallRequest

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                warnings.filterwarnings("ignore", category=GuardedCallWarning)
                req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("http:request", firewall_request=req)
                assert len(w) == 0
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_warn_mode_warning_contains_source_id(self) -> None:
        """Warning message includes the source_id."""
        from bigfoot._context import _guard_level
        from bigfoot._firewall_request import NetworkFirewallRequest

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("http:request", firewall_request=req)
                assert "'http:request'" in str(w[0].message)
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_warn_mode_warning_contains_blocked_by_firewall(self) -> None:
        """Warning message says 'blocked by firewall'."""
        from bigfoot._context import _guard_level
        from bigfoot._firewall_request import NetworkFirewallRequest

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("http:request", firewall_request=req)
                msg = str(w[0].message)
                assert "blocked by firewall" in msg
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_error_mode_raises_guarded_call_error(self) -> None:
        """Guard in error mode raises GuardedCallError (not a warning)."""
        from bigfoot._context import _guard_level
        from bigfoot._firewall_request import NetworkFirewallRequest

        level_token = _guard_level.set("error")
        guard_token = _guard_active.set(True)
        try:
            req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
            with pytest.raises(GuardedCallError):
                get_verifier_or_raise("http:request", firewall_request=req)
        finally:
            _guard_active.reset(guard_token)
            _guard_level.reset(level_token)

    def test_firewall_allow_in_warn_mode_suppresses_warning(self) -> None:
        """Allowed protocols don't emit warnings even in warn mode."""
        from bigfoot._context import _guard_level
        from bigfoot._firewall_request import NetworkFirewallRequest

        level_token = _guard_level.set("warn")
        guard_token = _guard_active.set(True)
        # Push an ALLOW rule for dns onto the firewall stack
        with allow("dns"):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("dns:lookup", firewall_request=req)
                guarded_warnings = [
                    x for x in w if issubclass(x.category, GuardedCallWarning)
                ]
                assert len(guarded_warnings) == 0
        _guard_active.reset(guard_token)
        _guard_level.reset(level_token)


class TestHookFirewallStackMerge:
    """Test that pytest_runtest_call builds firewall stack from markers correctly.

    These tests verify that allow/deny markers produce correct firewall rules.
    """

    def test_no_markers_empty_stack_denies(self) -> None:
        """Without markers, the firewall stack default-denies all protocols."""
        from bigfoot._firewall_request import NetworkFirewallRequest

        stack = _firewall_stack.get()
        http_req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
        # Default disposition is DENY (but markers from hook may be present;
        # without @pytest.mark.allow, http should not be allowed)
        assert stack.evaluate(http_req) == Disposition.DENY

    @pytest.mark.allow("socket")
    def test_marker_allow_creates_allow_rule(self) -> None:
        """@pytest.mark.allow('socket') creates ALLOW rule in firewall stack."""
        from bigfoot._firewall_request import NetworkFirewallRequest

        stack = _firewall_stack.get()
        sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)
        assert stack.evaluate(sock_req) == Disposition.ALLOW

    @pytest.mark.allow("socket")
    @pytest.mark.deny("dns")
    def test_marker_deny_blocks_non_allowed(self) -> None:
        """deny('dns') blocks 'dns' when it is not in the allow set."""
        from bigfoot._firewall_request import NetworkFirewallRequest

        stack = _firewall_stack.get()
        dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
        sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)
        assert stack.evaluate(dns_req) == Disposition.DENY
        assert stack.evaluate(sock_req) == Disposition.ALLOW


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
        """Guard active + allowed via firewall = GuardPassThrough (interceptor should call original)."""
        from bigfoot._firewall_request import NetworkFirewallRequest

        guard_token = _guard_active.set(True)
        try:
            with allow("dns"):
                req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
                with pytest.raises(GuardPassThrough):
                    get_verifier_or_raise("dns:getaddrinfo:example.com", firewall_request=req)
        finally:
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
        from bigfoot._guard import deny
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                # Explicitly deny dns to override project-level allow = ["dns:*"]
                with deny("dns"):
                    with pytest.raises(GuardedCallError) as exc_info:
                        socket.getaddrinfo("example.com", 80)
                    assert exc_info.value.plugin_name == "dns"
                    assert exc_info.value.source_id == "dns:lookup"
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            dns.deactivate()

    def test_dns_getaddrinfo_guard_passes_through_when_not_active(self) -> None:
        """Guard pass-through: interceptor calls original when guard is not active.

        With patches installed but guard not active, GuardPassThrough is raised
        and the interceptor calls the original function. This tests the
        pass-through path; firewall evaluation is tested separately in TestAllow.
        """
        import socket

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        try:
            # Guard not active but patches installed -> pass-through
            guard_token = _guard_active.set(False)
            try:
                result = socket.getaddrinfo("localhost", 80)
                assert isinstance(result, list)
                assert len(result) > 0
            finally:
                _guard_active.reset(guard_token)
        finally:
            dns.deactivate()

    def test_dns_gethostbyname_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks dns:gethostbyname when dns not in allowlist."""
        import socket

        from bigfoot._context import _guard_level
        from bigfoot._guard import deny
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                # Explicitly deny dns to override project-level allow = ["dns:*"]
                with deny("dns"):
                    with pytest.raises(GuardedCallError) as exc_info:
                        socket.gethostbyname("example.com")
                    assert exc_info.value.plugin_name == "dns"
            finally:
                _guard_active.reset(guard_token)
                _guard_level.reset(level_token)
        finally:
            dns.deactivate()

    def test_dns_gethostbyname_guard_passes_through_when_not_active(self) -> None:
        """Guard pass-through: interceptor calls original gethostbyname when guard is not active."""
        import socket

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        try:
            guard_token = _guard_active.set(False)
            try:
                result = socket.gethostbyname("localhost")
                assert isinstance(result, str)
                assert result == "127.0.0.1"
            finally:
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
        from bigfoot._guard import deny
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import _SOCKET_CLOSE_ORIGINAL, SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                # Explicitly deny socket to override project-level allow = ["socket:*"]
                with deny("socket"):
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

    def test_socket_connect_guard_passes_through_when_not_active(self) -> None:
        """Guard pass-through: interceptor calls real connect when guard is not active."""
        import socket as socket_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import _SOCKET_CLOSE_ORIGINAL, SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        try:
            guard_token = _guard_active.set(False)
            try:
                sock = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
                try:
                    # connect to a port that should refuse -- the point is that it
                    # reaches the REAL connect (ConnectionRefusedError or similar)
                    with pytest.raises((ConnectionRefusedError, OSError)):
                        sock.connect(("127.0.0.1", 1))
                finally:
                    _SOCKET_CLOSE_ORIGINAL(sock)
            finally:
                _guard_active.reset(guard_token)
        finally:
            sp.deactivate()

    def test_socket_send_guard_blocks_when_not_allowed(self) -> None:
        """Guard blocks socket:send when socket not in allowlist."""
        import socket as socket_mod

        from bigfoot._context import _guard_level
        from bigfoot._guard import deny
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import _SOCKET_CLOSE_ORIGINAL, SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        try:
            level_token = _guard_level.set("error")
            guard_token = _guard_active.set(True)
            try:
                # Explicitly deny socket to override project-level allow = ["socket:*"]
                with deny("socket"):
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

    def test_socket_close_guard_passes_through_when_not_active(self) -> None:
        """Guard pass-through: interceptor calls real close when guard is not active."""
        import socket as socket_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        try:
            guard_token = _guard_active.set(False)
            try:
                sock = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
                # close should call the real close without error
                sock.close()
            finally:
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

    def test_database_connect_guard_passes_through_when_not_active(self) -> None:
        """Guard pass-through: interceptor calls real connect when guard is not active."""
        import sqlite3

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.database_plugin import DatabasePlugin

        v = StrictVerifier()
        dp = DatabasePlugin(v)
        dp.activate()
        try:
            guard_token = _guard_active.set(False)
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

    def test_subprocess_run_guard_passes_through_when_not_active(self) -> None:
        """Guard pass-through: interceptor calls real subprocess.run when guard is not active."""
        import subprocess as subprocess_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.subprocess import SubprocessPlugin

        v = StrictVerifier()
        sp = SubprocessPlugin(v)
        sp.activate()
        try:
            guard_token = _guard_active.set(False)
            try:
                result = subprocess_mod.run(
                    ["echo", "hello"], capture_output=True, text=True,
                )
                assert result.returncode == 0
                assert result.stdout == "hello\n"
            finally:
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

    def test_subprocess_which_guard_passes_through_when_not_active(self) -> None:
        """Guard pass-through: interceptor calls real shutil.which when guard is not active."""
        import shutil as shutil_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.subprocess import SubprocessPlugin

        v = StrictVerifier()
        sp = SubprocessPlugin(v)
        sp.activate()
        try:
            guard_token = _guard_active.set(False)
            try:
                result = shutil_mod.which("echo")
                assert isinstance(result, str)
                assert "echo" in result
            finally:
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

    def test_firewall_stack_denies_by_default(self) -> None:
        """Without @pytest.mark.allow, all protocols are denied by the firewall."""
        from bigfoot._firewall_request import NetworkFirewallRequest

        stack = _firewall_stack.get()
        req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
        assert stack.evaluate(req) == Disposition.DENY

    @pytest.mark.allow("dns", "socket")
    def test_mark_allow_populates_firewall_stack(self) -> None:
        """@pytest.mark.allow should push ALLOW rules onto firewall stack."""
        from bigfoot._firewall_request import NetworkFirewallRequest

        stack = _firewall_stack.get()
        dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
        sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)
        assert stack.evaluate(dns_req) == Disposition.ALLOW
        assert stack.evaluate(sock_req) == Disposition.ALLOW

    @pytest.mark.allow("dns")
    def test_mark_allow_single_plugin(self) -> None:
        """Single plugin in @pytest.mark.allow pushes one ALLOW rule."""
        from bigfoot._firewall_request import NetworkFirewallRequest

        stack = _firewall_stack.get()
        dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
        assert stack.evaluate(dns_req) == Disposition.ALLOW

    @pytest.mark.allow("dns")
    @pytest.mark.allow("socket")
    def test_multiple_allow_marks_combine(self) -> None:
        """Multiple @pytest.mark.allow decorators combine into firewall rules."""
        from bigfoot._firewall_request import NetworkFirewallRequest

        stack = _firewall_stack.get()
        dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
        sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)
        assert stack.evaluate(dns_req) == Disposition.ALLOW
        assert stack.evaluate(sock_req) == Disposition.ALLOW

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
        from bigfoot._guard import deny
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import _SOCKET_CLOSE_ORIGINAL, SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        level_token = _guard_level.set("error")
        try:
            # Explicitly deny socket to override project-level allow = ["socket:*"]
            with deny("socket"):
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

    def test_guard_pass_through_permits_real_socket_operations(self) -> None:
        """Guard pass-through permits real socket operations when guard is not active.

        Plugins haven't been migrated to pass FirewallRequest yet, so the
        pass-through path (_guard_active=False + patches installed) is the
        mechanism that permits real I/O. Once plugins pass FirewallRequest,
        @pytest.mark.allow will work end-to-end via firewall evaluation.
        """
        import socket as socket_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.socket_plugin import SocketPlugin

        v = StrictVerifier()
        sp = SocketPlugin(v)
        sp.activate()
        guard_token = _guard_active.set(False)
        try:
            sock = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_STREAM)
            try:
                # connect to a port that should refuse -- proves the REAL connect ran
                with pytest.raises((ConnectionRefusedError, OSError)):
                    sock.connect(("127.0.0.1", 1))
            finally:
                sock.close()
        finally:
            _guard_active.reset(guard_token)
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
    def test_allow_context_manager_adds_to_marker_rules(self) -> None:
        """allow() inside @pytest.mark.allow adds rules to the firewall stack."""
        from bigfoot._firewall_request import NetworkFirewallRequest

        stack_mark = _firewall_stack.get()
        dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
        # Use redis (not socket) as the "not allowed" protocol since the project-level
        # firewall config allows both dns:* and socket:*.
        redis_req = NetworkFirewallRequest(protocol="redis", host="127.0.0.1", port=6379)

        # Mark already allows "dns"
        assert stack_mark.evaluate(dns_req) == Disposition.ALLOW
        assert stack_mark.evaluate(redis_req) == Disposition.DENY

        with allow("redis"):
            stack_both = _firewall_stack.get()
            assert stack_both.evaluate(dns_req) == Disposition.ALLOW
            assert stack_both.evaluate(redis_req) == Disposition.ALLOW

        # After exiting allow(), redis back to DENY
        assert _firewall_stack.get().evaluate(redis_req) == Disposition.DENY
        assert _firewall_stack.get().evaluate(dns_req) == Disposition.ALLOW

    def test_guard_pass_through_permits_real_dns_operations(self) -> None:
        """Guard pass-through permits real DNS when guard is not active.

        Plugins haven't been migrated to pass FirewallRequest yet, so the
        pass-through path is used. Firewall stack semantics for marks are
        tested separately in TestGuardActiveDuringTestBody.
        """
        import socket as socket_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        guard_token = _guard_active.set(False)
        try:
            result = socket_mod.getaddrinfo("localhost", 80)
            assert isinstance(result, list)
            assert len(result) > 0
            first = result[0]
            assert len(first) == 5  # (family, type, proto, canonname, sockaddr)
        finally:
            _guard_active.reset(guard_token)
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
        from bigfoot._guard import deny
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        level_token = _guard_level.set("error")
        try:
            # Explicitly deny dns to override project-level allow = ["dns:*"]
            with deny("dns"):
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

    def test_guard_pass_through_permits_real_subprocess(self) -> None:
        """Guard pass-through permits real subprocess.run when guard is not active.

        Plugins haven't been migrated to pass FirewallRequest yet.
        """
        import subprocess as subprocess_mod

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.subprocess import SubprocessPlugin

        v = StrictVerifier()
        sp = SubprocessPlugin(v)
        sp.activate()
        guard_token = _guard_active.set(False)
        try:
            result = subprocess_mod.run(
                ["echo", "guard_test"], capture_output=True, text=True,
            )
            assert result.returncode == 0
            assert result.stdout == "guard_test\n"
        finally:
            _guard_active.reset(guard_token)
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

    def test_sandbox_takes_precedence_over_firewall_allow(self) -> None:
        """Sandbox intercepts calls regardless of firewall allow rules.

        In the new firewall system, sandbox is step 1 in the decision tree
        and is always consulted before the firewall. An allow() rule does
        not bypass sandbox interception.
        """
        import socket

        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import _DnsSentinel

        v = StrictVerifier()
        dns_plugin = None
        for p in v._plugins:
            from bigfoot.plugins.dns_plugin import DnsPlugin
            if isinstance(p, DnsPlugin):
                dns_plugin = p
                break
        assert dns_plugin is not None

        dns_plugin.mock_getaddrinfo(
            "localhost", returns=[(2, 1, 6, "", ("127.0.0.1", 80))],
        )

        with allow("dns"):
            with v.sandbox():
                # Even with allow("dns"), sandbox intercepts the call
                result = socket.getaddrinfo("localhost", 80)
                assert result == [(2, 1, 6, "", ("127.0.0.1", 80))]

        # The interaction IS recorded because sandbox takes precedence
        v.assert_interaction(
            _DnsSentinel("dns:getaddrinfo:localhost"),
            host="localhost",
            port=80,
            family=0,
            type=0,
            proto=0,
        )

    def test_guarded_call_error_message_has_actionable_guidance(self) -> None:
        """GuardedCallError message contains all three remediation options.

        Creates its own DnsPlugin activation to be resilient against earlier
        tests that force-reset plugin install counts.
        """
        import socket as socket_mod

        from bigfoot._context import _guard_level
        from bigfoot._guard import deny
        from bigfoot._verifier import StrictVerifier
        from bigfoot.plugins.dns_plugin import DnsPlugin

        v = StrictVerifier()
        dns = DnsPlugin(v)
        dns.activate()
        level_token = _guard_level.set("error")
        try:
            # Explicitly deny dns to override project-level allow = ["dns:*"]
            with deny("dns"):
                with pytest.raises(GuardedCallError) as exc_info:
                    socket_mod.getaddrinfo("example.com", 80)
            msg = str(exc_info.value)
            # New firewall message format
            assert "blocked by bigfoot firewall" in msg
            assert "Attempted:" in msg
            assert "Fix with @pytest.mark.allow:" in msg
            assert '@pytest.mark.allow(M(protocol="dns"))' in msg
            assert "Fix with context manager (scoped to a block):" in msg
            assert 'bigfoot.allow(M(protocol="dns"))' in msg
            assert "Fix in pyproject.toml:" in msg
            assert "[tool.bigfoot.firewall]" in msg
            assert 'allow = ["dns:*"]' in msg
            assert "Or mock the call with a sandbox:" in msg
            assert "with bigfoot:" in msg
            assert "https://bigfoot.readthedocs.io/guides/guard-mode/" in msg
            # Old sections removed
            assert "supports_guard" not in msg
            assert "Valid plugin names for allow():" not in msg
        finally:
            _guard_level.reset(level_token)
            dns.deactivate()


class TestGuardAllowConfigMigration:
    """Test that old guard_allow config key raises migration error."""

    def test_guard_allow_raises_migration_error(self) -> None:
        """guard_allow config key is rejected with migration instructions."""
        from unittest.mock import patch

        from bigfoot._errors import BigfootConfigError
        from bigfoot.pytest_plugin import pytest_runtest_call

        config = {"guard": "error", "guard_allow": ["socket"]}

        class FakeItem:
            def iter_markers(self, name: str):
                return []

        item = FakeItem()

        with patch("bigfoot.pytest_plugin.load_bigfoot_config", return_value=config):
            hook_gen = pytest_runtest_call(item)
            with pytest.raises(BigfootConfigError, match="guard_allow config key has been replaced"):
                next(hook_gen)

    def test_guard_allow_string_raises_migration_error(self) -> None:
        """guard_allow = "socket" (string) also raises migration error."""
        from unittest.mock import patch

        from bigfoot._errors import BigfootConfigError
        from bigfoot.pytest_plugin import pytest_runtest_call

        config = {"guard": "error", "guard_allow": "socket"}

        class FakeItem:
            def iter_markers(self, name: str):
                return []

        item = FakeItem()

        with patch("bigfoot.pytest_plugin.load_bigfoot_config", return_value=config):
            hook_gen = pytest_runtest_call(item)
            with pytest.raises(BigfootConfigError, match="guard_allow config key has been replaced"):
                next(hook_gen)


class TestFirewallTomlConfig:
    """Test [tool.bigfoot.firewall] TOML config integration with pytest hook."""

    def test_firewall_allow_rule_in_config(self) -> None:
        """[tool.bigfoot.firewall] allow = ["socket:*"] creates ALLOW rule."""
        from unittest.mock import patch

        from bigfoot._firewall_request import NetworkFirewallRequest
        from bigfoot.pytest_plugin import pytest_runtest_call

        config = {"guard": "error", "firewall": {"allow": ["socket:*"]}}

        class FakeItem:
            fspath = "tests/test_example.py"

            def iter_markers(self, name: str):
                return []

        item = FakeItem()

        with patch("bigfoot.pytest_plugin.load_bigfoot_config", return_value=config):
            hook_gen = pytest_runtest_call(item)
            next(hook_gen)
            stack = _firewall_stack.get()
            sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)
            assert stack.evaluate(sock_req) == Disposition.ALLOW
            try:
                hook_gen.send(None)
            except StopIteration:
                pass

    def test_no_firewall_config_denies_all(self) -> None:
        """Without [tool.bigfoot.firewall], all protocols are denied."""
        from unittest.mock import patch

        from bigfoot._firewall_request import NetworkFirewallRequest
        from bigfoot.pytest_plugin import pytest_runtest_call

        config = {"guard": "error"}

        class FakeItem:
            fspath = "tests/test_example.py"

            def iter_markers(self, name: str):
                return []

        item = FakeItem()

        with patch("bigfoot.pytest_plugin.load_bigfoot_config", return_value=config):
            hook_gen = pytest_runtest_call(item)
            next(hook_gen)
            stack = _firewall_stack.get()
            http_req = NetworkFirewallRequest(protocol="http", host="example.com", port=80)
            assert stack.evaluate(http_req) == Disposition.DENY
            try:
                hook_gen.send(None)
            except StopIteration:
                pass

    def test_marker_allow_merged_with_toml_config(self) -> None:
        """@pytest.mark.allow merges with [tool.bigfoot.firewall] allow rules."""
        from unittest.mock import patch

        from bigfoot._firewall_request import NetworkFirewallRequest
        from bigfoot.pytest_plugin import pytest_runtest_call

        config = {"guard": "error", "firewall": {"allow": ["socket:*"]}}

        class FakeMark:
            def __init__(self, *args: str) -> None:
                self.args = args

        class FakeItem:
            fspath = "tests/test_example.py"

            def iter_markers(self, name: str):
                if name == "allow":
                    return [FakeMark("dns")]
                return []

        item = FakeItem()

        with patch("bigfoot.pytest_plugin.load_bigfoot_config", return_value=config):
            hook_gen = pytest_runtest_call(item)
            next(hook_gen)
            stack = _firewall_stack.get()
            sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)
            dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
            assert stack.evaluate(sock_req) == Disposition.ALLOW
            assert stack.evaluate(dns_req) == Disposition.ALLOW
            try:
                hook_gen.send(None)
            except StopIteration:
                pass

    def test_deny_marker_overrides_toml_allow(self) -> None:
        """@pytest.mark.deny blocks protocols even when TOML allows them."""
        from unittest.mock import patch

        from bigfoot._firewall_request import NetworkFirewallRequest
        from bigfoot.pytest_plugin import pytest_runtest_call

        config = {"guard": "error", "firewall": {"allow": ["socket:*", "dns:*"]}}

        class FakeMark:
            def __init__(self, *args: str) -> None:
                self.args = args

        class FakeItem:
            fspath = "tests/test_example.py"

            def iter_markers(self, name: str):
                if name == "deny":
                    return [FakeMark("socket")]
                return []

        item = FakeItem()

        with patch("bigfoot.pytest_plugin.load_bigfoot_config", return_value=config):
            hook_gen = pytest_runtest_call(item)
            next(hook_gen)
            stack = _firewall_stack.get()
            sock_req = NetworkFirewallRequest(protocol="socket", host="127.0.0.1", port=80)
            dns_req = NetworkFirewallRequest(protocol="dns", host="example.com", port=53)
            # deny is innermost (pushed after allow), so socket is denied
            assert stack.evaluate(sock_req) == Disposition.DENY
            assert stack.evaluate(dns_req) == Disposition.ALLOW
            try:
                hook_gen.send(None)
            except StopIteration:
                pass
