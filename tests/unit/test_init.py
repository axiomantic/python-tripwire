# tests/unit/test_init.py
"""Unit tests for bigfoot.__init__ public API.

Verifies that all public names are importable directly from the top-level
package and that __all__ contains exactly the expected names.
"""

import pytest


def test_all_contains_expected_names() -> None:
    """__all__ must be exactly the declared public API set."""
    # ESCAPE: if __all__ contained extras or was missing entries callers get
    # wrong autocomplete / wildcard-import results
    import bigfoot

    expected_all = {
        # Classes
        "StrictVerifier",
        "SandboxContext",
        "InAnyOrderContext",
        "MockPlugin",
        "DatabasePlugin",
        "PopenPlugin",
        "SmtpPlugin",
        "SocketPlugin",
        "AsyncWebSocketPlugin",
        "SyncWebSocketPlugin",
        "RedisPlugin",
        # Errors
        "BigfootError",
        "AssertionInsideSandboxError",
        "AutoAssertError",
        "InvalidStateError",
        "NoActiveVerifierError",
        "UnmockedInteractionError",
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
        "subprocess_mock",
        "popen_mock",
        "smtp_mock",
        "socket_mock",
        "db_mock",
        "async_websocket_mock",
        "sync_websocket_mock",
        "redis_mock",
    }
    assert set(bigfoot.__all__) == expected_all


def test_assertion_inside_sandbox_error_importable() -> None:
    """AssertionInsideSandboxError must be importable from the top-level package."""
    from bigfoot import AssertionInsideSandboxError
    from bigfoot._errors import AssertionInsideSandboxError as _AssertionInsideSandboxError

    assert AssertionInsideSandboxError is _AssertionInsideSandboxError


def test_strict_verifier_importable() -> None:
    """StrictVerifier must be importable from the top-level package."""
    # ESCAPE: if the import was missing or aliased wrongly instantiation would fail
    from bigfoot import StrictVerifier
    from bigfoot._verifier import StrictVerifier as _StrictVerifier

    assert StrictVerifier is _StrictVerifier


def test_sandbox_context_importable() -> None:
    """SandboxContext must be importable from the top-level package."""
    from bigfoot import SandboxContext
    from bigfoot._verifier import SandboxContext as _SandboxContext

    assert SandboxContext is _SandboxContext


def test_in_any_order_context_importable() -> None:
    """InAnyOrderContext must be importable from the top-level package."""
    from bigfoot import InAnyOrderContext
    from bigfoot._verifier import InAnyOrderContext as _InAnyOrderContext

    assert InAnyOrderContext is _InAnyOrderContext


def test_mock_plugin_importable() -> None:
    """MockPlugin must be importable from the top-level package."""
    from bigfoot import MockPlugin
    from bigfoot._mock_plugin import MockPlugin as _MockPlugin

    assert MockPlugin is _MockPlugin


def test_bigfoot_error_importable() -> None:
    """BigfootError must be importable from the top-level package."""
    from bigfoot import BigfootError
    from bigfoot._errors import BigfootError as _BigfootError

    assert BigfootError is _BigfootError


def test_unmocked_interaction_error_importable() -> None:
    """UnmockedInteractionError must be importable from the top-level package."""
    from bigfoot import UnmockedInteractionError
    from bigfoot._errors import UnmockedInteractionError as _UnmockedInteractionError

    assert UnmockedInteractionError is _UnmockedInteractionError


def test_unasserted_interactions_error_importable() -> None:
    """UnassertedInteractionsError must be importable from the top-level package."""
    from bigfoot import UnassertedInteractionsError
    from bigfoot._errors import UnassertedInteractionsError as _UnassertedInteractionsError

    assert UnassertedInteractionsError is _UnassertedInteractionsError


def test_unused_mocks_error_importable() -> None:
    """UnusedMocksError must be importable from the top-level package."""
    from bigfoot import UnusedMocksError
    from bigfoot._errors import UnusedMocksError as _UnusedMocksError

    assert UnusedMocksError is _UnusedMocksError


def test_verification_error_importable() -> None:
    """VerificationError must be importable from the top-level package."""
    from bigfoot import VerificationError
    from bigfoot._errors import VerificationError as _VerificationError

    assert VerificationError is _VerificationError


def test_interaction_mismatch_error_importable() -> None:
    """InteractionMismatchError must be importable from the top-level package."""
    from bigfoot import InteractionMismatchError
    from bigfoot._errors import InteractionMismatchError as _InteractionMismatchError

    assert InteractionMismatchError is _InteractionMismatchError


def test_sandbox_not_active_error_importable() -> None:
    """SandboxNotActiveError must be importable from the top-level package."""
    from bigfoot import SandboxNotActiveError
    from bigfoot._errors import SandboxNotActiveError as _SandboxNotActiveError

    assert SandboxNotActiveError is _SandboxNotActiveError


def test_conflict_error_importable() -> None:
    """ConflictError must be importable from the top-level package."""
    from bigfoot import ConflictError
    from bigfoot._errors import ConflictError as _ConflictError

    assert ConflictError is _ConflictError


def test_missing_assertion_fields_error_importable() -> None:
    """MissingAssertionFieldsError must be importable from the top-level package."""
    from bigfoot import MissingAssertionFieldsError
    from bigfoot._errors import MissingAssertionFieldsError as _MissingAssertionFieldsError

    assert MissingAssertionFieldsError is _MissingAssertionFieldsError


def test_http_plugin_importable_if_http_extra_installed() -> None:
    """HttpPlugin must be importable from bigfoot if [http] extra is installed."""
    # ESCAPE: if HttpPlugin import was missing from __init__ when http extra is
    # installed, users would have to import from bigfoot.plugins.http directly
    try:
        import httpx  # noqa: F401
        import requests  # noqa: F401

        http_available = True
    except ImportError:
        http_available = False

    if not http_available:
        pytest.skip("http extra not installed")

    import bigfoot

    assert hasattr(bigfoot, "HttpPlugin")

    from bigfoot import HttpPlugin
    from bigfoot.plugins.http import HttpPlugin as _HttpPlugin

    assert HttpPlugin is _HttpPlugin


def test_no_active_verifier_error_importable() -> None:
    """NoActiveVerifierError must be importable from the top-level package."""
    from bigfoot import NoActiveVerifierError
    from bigfoot._errors import NoActiveVerifierError as _NoActiveVerifierError

    assert NoActiveVerifierError is _NoActiveVerifierError


def test_module_level_mock_importable() -> None:
    """bigfoot.mock must be importable as a callable."""
    import bigfoot

    assert callable(bigfoot.mock)


def test_module_level_sandbox_importable() -> None:
    """bigfoot.sandbox must be importable as a callable."""
    import bigfoot

    assert callable(bigfoot.sandbox)


def test_module_level_assert_interaction_importable() -> None:
    """bigfoot.assert_interaction must be importable as a callable."""
    import bigfoot

    assert callable(bigfoot.assert_interaction)


def test_module_level_in_any_order_importable() -> None:
    """bigfoot.in_any_order must be importable as a callable."""
    import bigfoot

    assert callable(bigfoot.in_any_order)


def test_module_level_verify_all_importable() -> None:
    """bigfoot.verify_all must be importable as a callable."""
    import bigfoot

    assert callable(bigfoot.verify_all)


def test_module_level_current_verifier_importable() -> None:
    """bigfoot.current_verifier must be importable as a callable."""
    import bigfoot

    assert callable(bigfoot.current_verifier)


def test_module_level_spy_importable() -> None:
    """bigfoot.spy must be importable as a callable."""
    import bigfoot

    assert callable(bigfoot.spy)


def test_module_level_http_importable() -> None:
    """bigfoot.http must be importable as an object."""
    import bigfoot

    assert bigfoot.http is not None


def test_module_level_mock_raises_no_active_verifier_error_outside_test() -> None:
    """bigfoot.mock() raises NoActiveVerifierError when called outside a test context."""
    import bigfoot
    from bigfoot._context import _current_test_verifier
    from bigfoot._errors import NoActiveVerifierError

    # Temporarily clear the test verifier to simulate being outside a test
    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            bigfoot.mock("SomeService")
    finally:
        _current_test_verifier.reset(token)


def test_spy_importable_from_bigfoot() -> None:
    """bigfoot.spy is importable and is callable."""
    import bigfoot

    assert callable(bigfoot.spy)


def test_missing_assertion_fields_error_importable_from_bigfoot() -> None:
    """MissingAssertionFieldsError is importable from the bigfoot namespace."""
    import bigfoot
    from bigfoot import MissingAssertionFieldsError

    assert issubclass(MissingAssertionFieldsError, bigfoot.BigfootError)


def test_spy_in_all() -> None:
    """'spy' is listed in bigfoot.__all__."""
    import bigfoot

    assert "spy" in bigfoot.__all__


def test_missing_assertion_fields_error_in_all() -> None:
    """'MissingAssertionFieldsError' is listed in bigfoot.__all__."""
    import bigfoot

    assert "MissingAssertionFieldsError" in bigfoot.__all__


def test_mock_accepts_wraps_parameter() -> None:
    """bigfoot.mock() accepts a wraps keyword argument."""
    import inspect

    import bigfoot

    sig = inspect.signature(bigfoot.mock)
    assert "wraps" in sig.parameters


def test_async_websocket_mock_raises_import_error_when_websockets_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """async_websocket_mock.__getattr__ raises ImportError with install instructions when websockets is not installed.

    ESCAPE: async_websocket_mock
      CLAIM: Accessing any attribute on async_websocket_mock raises ImportError with
             instructions when bigfoot.plugins.websocket_plugin._WEBSOCKETS_AVAILABLE is False.
      PATH:  _AsyncWebSocketProxy.__getattr__ -> checks _WEBSOCKETS_AVAILABLE -> raises ImportError.
      CHECK: Raises ImportError with message containing "bigfoot[websockets]" and "pip install".
      MUTATION: If __getattr__ does not check _WEBSOCKETS_AVAILABLE, the error is deferred to
                activate() time (inside a test context), and the message will be different or absent.
      ESCAPE: A proxy that checks availability but emits a wrong message would still pass the
              attribute access but fail only the message assertion -- caught by exact string check.
    """
    import bigfoot
    import bigfoot.plugins.websocket_plugin as ws_mod

    monkeypatch.setattr(ws_mod, "_WEBSOCKETS_AVAILABLE", False)

    with pytest.raises(ImportError) as exc_info:
        _ = bigfoot.async_websocket_mock.new_session  # noqa: B018

    assert "bigfoot[websockets]" in str(exc_info.value)
    assert "pip install" in str(exc_info.value)


def test_bigfoot_module_is_context_manager(bigfoot_verifier: object) -> None:
    """``with bigfoot:`` activates a sandbox and returns the active StrictVerifier.

    ESCAPE: module context manager
      CLAIM: Entering ``with bigfoot:`` calls sandbox().__enter__() on the current
             verifier and returns the StrictVerifier instance.
      PATH:  _BigfootModule.__enter__ -> sandbox() -> SandboxContext.__enter__ -> returns verifier.
      CHECK: The ``as`` target is the StrictVerifier; calling mock/assert inside works normally.
      MUTATION: If __class__ swap is missing, ``with bigfoot:`` raises AttributeError.
      ESCAPE: A stub that returns *something* but not the verifier would fail the isinstance check.
    """
    import bigfoot
    from bigfoot import StrictVerifier

    proxy = bigfoot.mock("Svc")
    proxy.do.returns(42)

    with bigfoot as v:
        assert isinstance(v, StrictVerifier)
        result = proxy.do()
        assert result == 42

    bigfoot.assert_interaction(proxy.do, args=(), kwargs={})


async def test_bigfoot_module_is_async_context_manager(bigfoot_verifier: object) -> None:
    """``async with bigfoot:`` activates a sandbox and returns the StrictVerifier.

    ESCAPE: async module context manager
      CLAIM: Entering ``async with bigfoot:`` delegates to sandbox().__aenter__() and
             returns the StrictVerifier.
      PATH:  _BigfootModule.__aenter__ -> sandbox() -> SandboxContext.__aenter__ -> returns verifier.
      CHECK: The ``as`` target is the StrictVerifier; async code inside the block is intercepted.
      MUTATION: Missing __aenter__ raises AttributeError; wrong return raises AssertionError.
    """
    import bigfoot
    from bigfoot import StrictVerifier

    proxy = bigfoot.mock("AsyncSvc")
    proxy.fetch.returns({"ok": True})

    async with bigfoot as v:
        assert isinstance(v, StrictVerifier)
        result = proxy.fetch()
        assert result == {"ok": True}

    bigfoot.assert_interaction(proxy.fetch, args=(), kwargs={})


def test_bigfoot_nested_sandboxes_via_with_bigfoot(bigfoot_verifier: object) -> None:
    """Nested ``with bigfoot:`` blocks use reference counting and do not conflict.

    ESCAPE: nested sandboxes
      CLAIM: Entering ``with bigfoot:`` twice nests correctly; the inner exit does not
             deactivate plugins prematurely.
      PATH:  _BigfootModule.__enter__ pushes to stack twice; __exit__ pops in LIFO order.
      CHECK: Both ``as`` values are the same StrictVerifier; no errors on exit.
      MUTATION: A non-stacking implementation would push the same cm twice and break LIFO order.
    """
    import bigfoot
    from bigfoot import StrictVerifier

    with bigfoot as v1:
        with bigfoot as v2:
            assert isinstance(v1, StrictVerifier)
            assert isinstance(v2, StrictVerifier)
            assert v1 is v2


def test_sync_websocket_mock_raises_import_error_when_websocket_client_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_websocket_mock.__getattr__ raises ImportError with install instructions when websocket-client is not installed.

    ESCAPE: sync_websocket_mock
      CLAIM: Accessing any attribute on sync_websocket_mock raises ImportError with
             instructions when bigfoot.plugins.websocket_plugin._WEBSOCKET_CLIENT_AVAILABLE is False.
      PATH:  _SyncWebSocketProxy.__getattr__ -> checks _WEBSOCKET_CLIENT_AVAILABLE -> raises ImportError.
      CHECK: Raises ImportError with message containing "bigfoot[websocket-client]" and "pip install".
      MUTATION: If __getattr__ does not check _WEBSOCKET_CLIENT_AVAILABLE, the error is deferred
                to activate() time (inside a test context), and the message will be different or absent.
      ESCAPE: A proxy that checks availability but emits a wrong message would still pass the
              attribute access but fail only the message assertion -- caught by exact string check.
    """
    import bigfoot
    import bigfoot.plugins.websocket_plugin as ws_mod

    monkeypatch.setattr(ws_mod, "_WEBSOCKET_CLIENT_AVAILABLE", False)

    with pytest.raises(ImportError) as exc_info:
        _ = bigfoot.sync_websocket_mock.new_session  # noqa: B018

    assert "bigfoot[websocket-client]" in str(exc_info.value)
    assert "pip install" in str(exc_info.value)
