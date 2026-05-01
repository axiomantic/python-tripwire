# tests/unit/test_init.py
"""Unit tests for tripwire.__init__ public API.

Verifies that all public names are importable directly from the top-level
package and that __all__ contains exactly the expected names.
"""

import pytest


def test_all_contains_expected_names() -> None:
    """__all__ must be exactly the declared public API set."""
    # ESCAPE: if __all__ contained extras or was missing entries callers get
    # wrong autocomplete / wildcard-import results
    import tripwire

    expected_all = {
        # Plugin authoring API
        "BasePlugin",
        "Interaction",
        "Timeline",
        "GuardPassThrough",
        "get_verifier_or_raise",
        "PluginEntry",
        # Classes
        "StrictVerifier",
        "SandboxContext",
        "InAnyOrderContext",
        "MockPlugin",
        "DatabasePlugin",
        "LoggingPlugin",
        "PopenPlugin",
        "SmtpPlugin",
        "SocketPlugin",
        "AsyncWebSocketPlugin",
        "SyncWebSocketPlugin",
        "RedisPlugin",
        "MongoPlugin",
        "CeleryPlugin",
        "DnsPlugin",
        "MemcachePlugin",
        "Psycopg2Plugin",
        "AsyncpgPlugin",
        "AsyncSubprocessPlugin",
        "Boto3Plugin",
        "ElasticsearchPlugin",
        "JwtPlugin",
        "CryptoPlugin",
        # Guard mode
        "allow",
        "deny",
        "restrict",
        "GuardedCallError",
        "GuardedCallWarning",
        # Firewall
        "Disposition",
        "FirewallRequest",
        # Match
        "M",
        # Errors
        "AllWildcardAssertionError",
        "TripwireConfigError",
        "TripwireError",
        "AssertionInsideSandboxError",
        "AutoAssertError",
        "InvalidStateError",
        "NoActiveVerifierError",
        "UnmockedInteractionError",
        "UnsafePassthroughError",
        "PostSandboxInteractionError",
        "UnassertedInteractionsError",
        "UnusedMocksError",
        "VerificationError",
        "InteractionMismatchError",
        "SandboxNotActiveError",
        "ConflictError",
        "MissingAssertionFieldsError",
        # Module-level API
        "mock",
        "sandbox",
        "assert_interaction",
        "in_any_order",
        "verify_all",
        "current_verifier",
        "spy",
        "http",
        "subprocess",
        "popen",
        "smtp",
        "socket",
        "db",
        "async_websocket",
        "sync_websocket",
        "redis",
        "mongo",
        "dns",
        "memcache",
        "celery",
        "log",
        "async_subprocess",
        "psycopg2",
        "asyncpg",
        "boto3",
        "elasticsearch",
        "jwt",
        "crypto",
        "FileIoPlugin",
        "file_io",
        "PikaPlugin",
        "pika",
        "SshPlugin",
        "ssh",
        "GrpcPlugin",
        "grpc",
        "McpPlugin",
        "mcp",
        "NativePlugin",
        "native",
    }
    assert set(tripwire.__all__) == expected_all


def test_assertion_inside_sandbox_error_importable() -> None:
    """AssertionInsideSandboxError must be importable from the top-level package."""
    from tripwire import AssertionInsideSandboxError
    from tripwire._errors import AssertionInsideSandboxError as _AssertionInsideSandboxError

    assert AssertionInsideSandboxError is _AssertionInsideSandboxError


def test_strict_verifier_importable() -> None:
    """StrictVerifier must be importable from the top-level package."""
    # ESCAPE: if the import was missing or aliased wrongly instantiation would fail
    from tripwire import StrictVerifier
    from tripwire._verifier import StrictVerifier as _StrictVerifier

    assert StrictVerifier is _StrictVerifier


def test_sandbox_context_importable() -> None:
    """SandboxContext must be importable from the top-level package."""
    from tripwire import SandboxContext
    from tripwire._verifier import SandboxContext as _SandboxContext

    assert SandboxContext is _SandboxContext


def test_in_any_order_context_importable() -> None:
    """InAnyOrderContext must be importable from the top-level package."""
    from tripwire import InAnyOrderContext
    from tripwire._verifier import InAnyOrderContext as _InAnyOrderContext

    assert InAnyOrderContext is _InAnyOrderContext


def test_mock_plugin_importable() -> None:
    """MockPlugin must be importable from the top-level package."""
    from tripwire import MockPlugin
    from tripwire._mock_plugin import MockPlugin as _MockPlugin

    assert MockPlugin is _MockPlugin


def test_tripwire_error_importable() -> None:
    """TripwireError must be importable from the top-level package."""
    from tripwire import TripwireError
    from tripwire._errors import TripwireError as _TripwireError

    assert TripwireError is _TripwireError


def test_unmocked_interaction_error_importable() -> None:
    """UnmockedInteractionError must be importable from the top-level package."""
    from tripwire import UnmockedInteractionError
    from tripwire._errors import UnmockedInteractionError as _UnmockedInteractionError

    assert UnmockedInteractionError is _UnmockedInteractionError


def test_unasserted_interactions_error_importable() -> None:
    """UnassertedInteractionsError must be importable from the top-level package."""
    from tripwire import UnassertedInteractionsError
    from tripwire._errors import UnassertedInteractionsError as _UnassertedInteractionsError

    assert UnassertedInteractionsError is _UnassertedInteractionsError


def test_unused_mocks_error_importable() -> None:
    """UnusedMocksError must be importable from the top-level package."""
    from tripwire import UnusedMocksError
    from tripwire._errors import UnusedMocksError as _UnusedMocksError

    assert UnusedMocksError is _UnusedMocksError


def test_verification_error_importable() -> None:
    """VerificationError must be importable from the top-level package."""
    from tripwire import VerificationError
    from tripwire._errors import VerificationError as _VerificationError

    assert VerificationError is _VerificationError


def test_interaction_mismatch_error_importable() -> None:
    """InteractionMismatchError must be importable from the top-level package."""
    from tripwire import InteractionMismatchError
    from tripwire._errors import InteractionMismatchError as _InteractionMismatchError

    assert InteractionMismatchError is _InteractionMismatchError


def test_sandbox_not_active_error_importable() -> None:
    """SandboxNotActiveError must be importable from the top-level package."""
    from tripwire import SandboxNotActiveError
    from tripwire._errors import SandboxNotActiveError as _SandboxNotActiveError

    assert SandboxNotActiveError is _SandboxNotActiveError


def test_conflict_error_importable() -> None:
    """ConflictError must be importable from the top-level package."""
    from tripwire import ConflictError
    from tripwire._errors import ConflictError as _ConflictError

    assert ConflictError is _ConflictError


def test_missing_assertion_fields_error_importable() -> None:
    """MissingAssertionFieldsError must be importable from the top-level package."""
    from tripwire import MissingAssertionFieldsError
    from tripwire._errors import MissingAssertionFieldsError as _MissingAssertionFieldsError

    assert MissingAssertionFieldsError is _MissingAssertionFieldsError


def test_http_plugin_importable_if_http_extra_installed() -> None:
    """HttpPlugin must be importable from tripwire if [http] extra is installed."""
    # ESCAPE: if HttpPlugin import was missing from __init__ when http extra is
    # installed, users would have to import from tripwire.plugins.http directly
    try:
        import httpx  # noqa: F401
        import requests  # noqa: F401

        http_available = True
    except ImportError:
        http_available = False

    if not http_available:
        pytest.skip("http extra not installed")

    import tripwire

    assert hasattr(tripwire, "HttpPlugin")

    from tripwire import HttpPlugin
    from tripwire.plugins.http import HttpPlugin as _HttpPlugin

    assert HttpPlugin is _HttpPlugin


def test_no_active_verifier_error_importable() -> None:
    """NoActiveVerifierError must be importable from the top-level package."""
    from tripwire import NoActiveVerifierError
    from tripwire._errors import NoActiveVerifierError as _NoActiveVerifierError

    assert NoActiveVerifierError is _NoActiveVerifierError


def test_module_level_mock_importable() -> None:
    """tripwire.mock must be importable as a callable."""
    import tripwire

    assert callable(tripwire.mock)


def test_module_level_sandbox_importable() -> None:
    """tripwire.sandbox must be importable as a callable."""
    import tripwire

    assert callable(tripwire.sandbox)


def test_module_level_assert_interaction_importable() -> None:
    """tripwire.assert_interaction must be importable as a callable."""
    import tripwire

    assert callable(tripwire.assert_interaction)


def test_module_level_in_any_order_importable() -> None:
    """tripwire.in_any_order must be importable as a callable."""
    import tripwire

    assert callable(tripwire.in_any_order)


def test_module_level_verify_all_importable() -> None:
    """tripwire.verify_all must be importable as a callable."""
    import tripwire

    assert callable(tripwire.verify_all)


def test_module_level_current_verifier_importable() -> None:
    """tripwire.current_verifier must be importable as a callable."""
    import tripwire

    assert callable(tripwire.current_verifier)


def test_module_level_spy_importable() -> None:
    """tripwire.spy must be importable as a callable."""
    import tripwire

    assert callable(tripwire.spy)


def test_module_level_http_importable() -> None:
    """tripwire.http must be importable as an object."""
    import tripwire

    assert tripwire.http is not None


def test_module_level_mock_raises_no_active_verifier_error_outside_test() -> None:
    """tripwire.mock() raises NoActiveVerifierError when called outside a test context."""
    import tripwire
    from tripwire._context import _current_test_verifier
    from tripwire._errors import NoActiveVerifierError

    # Temporarily clear the test verifier to simulate being outside a test
    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            tripwire.mock("os.path:sep")
    finally:
        _current_test_verifier.reset(token)


def test_spy_importable_from_tripwire() -> None:
    """tripwire.spy is importable and is callable."""
    import tripwire

    assert callable(tripwire.spy)


def test_missing_assertion_fields_error_importable_from_tripwire() -> None:
    """MissingAssertionFieldsError is importable from the tripwire namespace."""
    import tripwire
    from tripwire import MissingAssertionFieldsError

    assert issubclass(MissingAssertionFieldsError, tripwire.TripwireError)


def test_spy_in_all() -> None:
    """'spy' is listed in tripwire.__all__."""
    import tripwire

    assert "spy" in tripwire.__all__


def test_missing_assertion_fields_error_in_all() -> None:
    """'MissingAssertionFieldsError' is listed in tripwire.__all__."""
    import tripwire

    assert "MissingAssertionFieldsError" in tripwire.__all__


def test_mock_accepts_path_parameter() -> None:
    """tripwire.mock() accepts a path positional argument (new import-site API)."""
    import inspect

    import tripwire

    sig = inspect.signature(tripwire.mock)
    assert "path" in sig.parameters


def test_async_websocket_raises_import_error_when_websockets_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """async_websocket.__getattr__ raises ImportError with install instructions when websockets is not installed.

    ESCAPE: async_websocket
      CLAIM: Accessing any attribute on async_websocket raises ImportError with
             instructions when tripwire.plugins.websocket_plugin._WEBSOCKETS_AVAILABLE is False.
      PATH:  _AsyncWebSocketProxy.__getattr__ -> checks _WEBSOCKETS_AVAILABLE -> raises ImportError.
      CHECK: Raises ImportError with message containing "pytest-tripwire[websockets]" and "pip install".
      MUTATION: If __getattr__ does not check _WEBSOCKETS_AVAILABLE, the error is deferred to
                activate() time (inside a test context), and the message will be different or absent.
      ESCAPE: A proxy that checks availability but emits a wrong message would still pass the
              attribute access but fail only the message assertion -- caught by exact string check.
    """
    import tripwire
    import tripwire.plugins.websocket_plugin as ws_mod

    monkeypatch.setattr(ws_mod, "_WEBSOCKETS_AVAILABLE", False)

    with pytest.raises(ImportError) as exc_info:
        _ = tripwire.async_websocket.new_session  # noqa: B018

    assert "pytest-tripwire[websockets]" in str(exc_info.value)
    assert "pip install" in str(exc_info.value)


def test_tripwire_module_is_context_manager(tripwire_verifier: object) -> None:
    """``with tripwire:`` activates a sandbox and returns the active StrictVerifier.

    ESCAPE: module context manager
      CLAIM: Entering ``with tripwire:`` calls sandbox().__enter__() on the current
             verifier and returns the StrictVerifier instance.
      PATH:  _TripwireModule.__enter__ -> sandbox() -> SandboxContext.__enter__ -> returns verifier.
      CHECK: The ``as`` target is the StrictVerifier; calling mock/assert inside works normally.
      MUTATION: If __class__ swap is missing, ``with tripwire:`` raises AttributeError.
      ESCAPE: A stub that returns *something* but not the verifier would fail the isinstance check.
    """
    import sys
    import types

    import tripwire
    from tripwire import StrictVerifier

    mod = types.ModuleType("_test_init_cm")
    mod.do = lambda: "real"  # type: ignore[attr-defined]
    sys.modules["_test_init_cm"] = mod
    try:
        mock = tripwire.mock("_test_init_cm:do")
        mock.returns(42)

        with tripwire as v:
            assert isinstance(v, StrictVerifier)
            result = mod.do()
            assert result == 42

        mock.assert_call(args=(), kwargs={})
    finally:
        del sys.modules["_test_init_cm"]


async def test_tripwire_module_is_async_context_manager(tripwire_verifier: object) -> None:
    """``async with tripwire:`` activates a sandbox and returns the StrictVerifier.

    ESCAPE: async module context manager
      CLAIM: Entering ``async with tripwire:`` delegates to sandbox().__aenter__() and
             returns the StrictVerifier.
      PATH:  _TripwireModule.__aenter__ -> sandbox() -> SandboxContext.__aenter__ -> returns verifier.
      CHECK: The ``as`` target is the StrictVerifier; async code inside the block is intercepted.
      MUTATION: Missing __aenter__ raises AttributeError; wrong return raises AssertionError.
    """
    import sys
    import types

    import tripwire
    from tripwire import StrictVerifier

    mod = types.ModuleType("_test_init_async_cm")
    mod.fetch = lambda: "real"  # type: ignore[attr-defined]
    sys.modules["_test_init_async_cm"] = mod
    try:
        mock = tripwire.mock("_test_init_async_cm:fetch")
        mock.returns({"ok": True})

        async with tripwire as v:
            assert isinstance(v, StrictVerifier)
            result = mod.fetch()
            assert result == {"ok": True}

        mock.assert_call(args=(), kwargs={})
    finally:
        del sys.modules["_test_init_async_cm"]


def test_tripwire_nested_sandboxes_via_with_tripwire(tripwire_verifier: object) -> None:
    """Nested ``with tripwire:`` blocks use reference counting and do not conflict.

    ESCAPE: nested sandboxes
      CLAIM: Entering ``with tripwire:`` twice nests correctly; the inner exit does not
             deactivate plugins prematurely.
      PATH:  _TripwireModule.__enter__ pushes to stack twice; __exit__ pops in LIFO order.
      CHECK: Both ``as`` values are the same StrictVerifier; no errors on exit.
      MUTATION: A non-stacking implementation would push the same cm twice and break LIFO order.
    """
    import tripwire
    from tripwire import StrictVerifier

    with tripwire as v1:
        with tripwire as v2:
            assert isinstance(v1, StrictVerifier)
            assert isinstance(v2, StrictVerifier)
            assert v1 is v2


def test_sync_websocket_raises_import_error_when_websocket_client_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_websocket.__getattr__ raises ImportError with install instructions when websocket-client is not installed.

    ESCAPE: sync_websocket
      CLAIM: Accessing any attribute on sync_websocket raises ImportError with
             instructions when tripwire.plugins.websocket_plugin._WEBSOCKET_CLIENT_AVAILABLE is False.
      PATH:  _SyncWebSocketProxy.__getattr__ -> checks _WEBSOCKET_CLIENT_AVAILABLE -> raises ImportError.
      CHECK: Raises ImportError with message containing "pytest-tripwire[websocket-client]" and "pip install".
      MUTATION: If __getattr__ does not check _WEBSOCKET_CLIENT_AVAILABLE, the error is deferred
                to activate() time (inside a test context), and the message will be different or absent.
      ESCAPE: A proxy that checks availability but emits a wrong message would still pass the
              attribute access but fail only the message assertion -- caught by exact string check.
    """
    import tripwire
    import tripwire.plugins.websocket_plugin as ws_mod

    monkeypatch.setattr(ws_mod, "_WEBSOCKET_CLIENT_AVAILABLE", False)

    with pytest.raises(ImportError) as exc_info:
        _ = tripwire.sync_websocket.new_session  # noqa: B018

    assert "pytest-tripwire[websocket-client]" in str(exc_info.value)
    assert "pip install" in str(exc_info.value)


