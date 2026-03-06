"""Unit tests for AsyncWebSocketPlugin (Task 4.1) and SyncWebSocketPlugin (Task 4.2).

All tests use the red-green-refactor cycle. Tests were written BEFORE the
implementation. Each test asserts exact equality against complete expected
output -- no substring checks, no existence-only assertions.
"""

from __future__ import annotations

import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import InvalidStateError, UnmockedInteractionError
from bigfoot._state_machine_plugin import SessionHandle
from bigfoot._verifier import StrictVerifier

websockets = pytest.importorskip("websockets")
websocket = pytest.importorskip("websocket")

from bigfoot.plugins.websocket_plugin import (  # noqa: E402
    _WEBSOCKET_CLIENT_AVAILABLE,
    _WEBSOCKETS_AVAILABLE,
    AsyncWebSocketPlugin,
    SyncWebSocketPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_async_verifier_with_plugin() -> tuple[StrictVerifier, AsyncWebSocketPlugin]:
    """Return (verifier, plugin) with AsyncWebSocketPlugin registered but NOT activated."""
    v = StrictVerifier()
    p = AsyncWebSocketPlugin(v)
    return v, p


def _make_sync_verifier_with_plugin() -> tuple[StrictVerifier, SyncWebSocketPlugin]:
    """Return (verifier, plugin) with SyncWebSocketPlugin registered but NOT activated."""
    v = StrictVerifier()
    p = SyncWebSocketPlugin(v)
    return v, p


def _reset_async_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    import websockets as _ws

    with AsyncWebSocketPlugin._install_lock:
        AsyncWebSocketPlugin._install_count = 0
        if AsyncWebSocketPlugin._original_connect is not None:
            _ws.connect = AsyncWebSocketPlugin._original_connect
            AsyncWebSocketPlugin._original_connect = None


def _reset_sync_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    import websocket as _wsc

    with SyncWebSocketPlugin._install_lock:
        SyncWebSocketPlugin._install_count = 0
        if SyncWebSocketPlugin._original_create_connection is not None:
            _wsc.create_connection = SyncWebSocketPlugin._original_create_connection
            SyncWebSocketPlugin._original_create_connection = None


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    """Ensure both plugin install counts start and end at 0 for every test."""
    _reset_async_plugin_count()
    _reset_sync_plugin_count()
    yield
    _reset_async_plugin_count()
    _reset_sync_plugin_count()


# ===========================================================================
# AsyncWebSocketPlugin
# ===========================================================================

# ---------------------------------------------------------------------------
# Static interface: _initial_state / _transitions / _unmocked_source_id
# ---------------------------------------------------------------------------


# ESCAPE: test_async_initial_state
#   CLAIM: _initial_state() returns "connecting".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "connecting".
#   MUTATION: Returning "disconnected" or "open" fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_async_initial_state() -> None:
    v, p = _make_async_verifier_with_plugin()
    assert p._initial_state() == "connecting"


# ESCAPE: test_async_transitions_structure
#   CLAIM: _transitions() returns the exact expected dict.
#   PATH:  Direct call on plugin instance.
#   CHECK: result == exact dict.
#   MUTATION: Any missing key or wrong state name fails the equality check.
#   ESCAPE: Extra keys in the dict would also fail the equality check.
def test_async_transitions_structure() -> None:
    v, p = _make_async_verifier_with_plugin()
    assert p._transitions() == {
        "connect": {"connecting": "open"},
        "send": {"open": "open"},
        "recv": {"open": "open"},
        "close": {"open": "closed"},
    }


# ESCAPE: test_async_unmocked_source_id
#   CLAIM: _unmocked_source_id() returns "websocket:async:connect".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "websocket:async:connect".
#   MUTATION: Returning a different string fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_async_unmocked_source_id() -> None:
    v, p = _make_async_verifier_with_plugin()
    assert p._unmocked_source_id() == "websocket:async:connect"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_async_activate_installs_patches
#   CLAIM: After activate(), websockets.connect is replaced with bigfoot interceptor.
#   PATH:  activate() -> _install_count == 0 -> store original -> install interceptor.
#   CHECK: websockets.connect is not the original after activate().
#   MUTATION: Skipping patch installation leaves original in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison proves replacement.
def test_async_activate_installs_patches() -> None:
    import websockets as _ws

    v, p = _make_async_verifier_with_plugin()
    original = _ws.connect
    p.activate()
    assert _ws.connect is not original
    p.deactivate()


# ESCAPE: test_async_deactivate_restores_patches
#   CLAIM: After activate() then deactivate(), websockets.connect is restored.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore original.
#   CHECK: websockets.connect is the original after deactivate().
#   MUTATION: Not restoring in deactivate() leaves bigfoot's interceptor in place.
#   ESCAPE: Nothing reasonable -- identity comparison against saved original.
def test_async_deactivate_restores_patches() -> None:
    import websockets as _ws

    v, p = _make_async_verifier_with_plugin()
    original = _ws.connect
    p.activate()
    p.deactivate()
    assert _ws.connect is original


# ESCAPE: test_async_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patch is removed.
#   PATH:  First activate -> _install_count=1; second activate -> _install_count=2 (no reinstall).
#          First deactivate -> _install_count=1 (patch remains).
#          Second deactivate -> _install_count=0 (original restored).
#   CHECK: After first deactivate, websockets.connect is still patched.
#          After second deactivate, it is the original.
#   MUTATION: Restoring on first deactivate fails the mid-point identity check.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove count-controlled restoration.
def test_async_reference_counting_nested() -> None:
    import websockets as _ws

    v, p = _make_async_verifier_with_plugin()
    original = _ws.connect
    p.activate()
    p.activate()
    assert AsyncWebSocketPlugin._install_count == 2

    p.deactivate()
    assert AsyncWebSocketPlugin._install_count == 1
    assert _ws.connect is not original

    p.deactivate()
    assert AsyncWebSocketPlugin._install_count == 0
    assert _ws.connect is original


# ---------------------------------------------------------------------------
# Basic session lifecycle: connect -> send -> recv -> close
# ---------------------------------------------------------------------------


# ESCAPE: test_async_basic_connect_send_recv_close
#   CLAIM: A full session (connect, send, recv, close) completes without error;
#          each step returns the configured value and advances state correctly.
#   PATH:  activate -> async with websockets.connect() -> __aenter__ executes "connect" step ->
#          ws.send() -> "send" step; ws.recv() -> "recv" step; ws.close() -> "close" step.
#   CHECK: send returns None; recv returns "hello"; close returns None.
#          After sandbox no UnusedMocksError is raised.
#   MUTATION: Returning wrong value from any step fails the corresponding equality check.
#   ESCAPE: recv returning b"hello" instead of "hello" would fail the equality check.
async def test_async_basic_connect_send_recv_close() -> None:
    v, p = _make_async_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("send", returns=None)
    session.expect("recv", returns="hello")
    session.expect("close", returns=None)

    with v.sandbox():
        async with websockets.connect("ws://localhost:8765") as ws:
            send_result = await ws.send("ping")
            recv_result = await ws.recv()
            close_result = await ws.close()

    assert send_result is None
    assert recv_result == "hello"
    assert close_result is None

    v.assert_interaction(p.connect, uri="ws://localhost:8765")
    v.assert_interaction(p.send, message="ping")
    v.assert_interaction(p.recv, message="hello")
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# InvalidStateError: recv before connect (state machine)
# ---------------------------------------------------------------------------


# ESCAPE: test_async_recv_before_connect_raises_invalid_state
#   CLAIM: Calling _execute_step with "recv" from state "connecting" raises InvalidStateError.
#   PATH:  _execute_step(handle, "recv", ...) with handle._state="connecting"
#          -> "recv" method exists but "connecting" not in method_transitions -> InvalidStateError.
#   CHECK: InvalidStateError raised; exc.source_id == "websocket:async:recv";
#          exc.method == "recv"; exc.current_state == "connecting";
#          exc.valid_states == frozenset({"open"}).
#   MUTATION: Not checking current state allows the call through without raising.
#   ESCAPE: Raising InvalidStateError with wrong current_state fails the attribute check.
def test_async_recv_before_connect_raises_invalid_state() -> None:
    v, p = _make_async_verifier_with_plugin()
    session = p.new_session()
    # Manually bind at initial state "connecting" without executing the connect step
    fake_obj = object()
    handle = p._bind_connection(fake_obj)
    assert handle._state == "connecting"

    with pytest.raises(InvalidStateError) as exc_info:
        p._execute_step(handle, "recv", (), {}, "websocket:async:recv")

    exc = exc_info.value
    assert exc.source_id == "websocket:async:recv"
    assert exc.method == "recv"
    assert exc.current_state == "connecting"
    assert exc.valid_states == frozenset({"open"})


# ---------------------------------------------------------------------------
# FIFO ordering: two sequential sessions
# ---------------------------------------------------------------------------


# ESCAPE: test_async_fifo_two_sessions
#   CLAIM: Two sessions are consumed in registration order; the first ws gets the
#          first session's script, the second ws gets the second session's script.
#   PATH:  new_session x2 -> two handles in _session_queue (FIFO deque).
#          First websockets.connect -> pops first handle at connect() call time.
#          Second websockets.connect -> pops second handle at connect() call time.
#          Each recv returns its own configured value.
#   CHECK: first_recv_result == "first"; second_recv_result == "second".
#   MUTATION: Reversing FIFO order (LIFO) swaps the returned values; both checks fail.
#   ESCAPE: Nothing reasonable -- exact string equality on distinct values.
async def test_async_fifo_two_sessions() -> None:
    v, p = _make_async_verifier_with_plugin()
    session1 = p.new_session()
    session1.expect("connect", returns=None)
    session1.expect("recv", returns="first")
    session1.expect("close", returns=None)

    session2 = p.new_session()
    session2.expect("connect", returns=None)
    session2.expect("recv", returns="second")
    session2.expect("close", returns=None)

    with v.sandbox():
        # Both connect() calls happen before either __aenter__ in this test
        # to verify FIFO pop is at connect() call time.
        cm1 = websockets.connect("ws://localhost:8765")
        cm2 = websockets.connect("ws://localhost:8765")
        async with cm1 as ws1:
            async with cm2 as ws2:
                first_recv_result = await ws1.recv()
                second_recv_result = await ws2.recv()
                await ws1.close()
                await ws2.close()

    assert first_recv_result == "first"
    assert second_recv_result == "second"

    # Timeline order: connect1, connect2, recv1, recv2, close1, close2
    # The nested async with blocks fire __aenter__ for cm1 then cm2 before any recv.
    v.assert_interaction(p.connect, uri="ws://localhost:8765")
    v.assert_interaction(p.connect, uri="ws://localhost:8765")
    v.assert_interaction(p.recv, message="first")
    v.assert_interaction(p.recv, message="second")
    v.assert_interaction(p.close)
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# ImportError when websockets not installed
# ---------------------------------------------------------------------------


# ESCAPE: test_async_importerror_flag
#   CLAIM: _WEBSOCKETS_AVAILABLE is True when websockets is importable.
#   PATH:  Module-level try/except import guard.
#   CHECK: _WEBSOCKETS_AVAILABLE == True (since pytest.importorskip ensured it).
#   MUTATION: Setting it to False when websockets IS importable fails the equality check.
#   ESCAPE: Nothing reasonable -- exact boolean equality.
def test_async_importerror_flag() -> None:
    assert _WEBSOCKETS_AVAILABLE is True


# ESCAPE: test_async_activate_raises_when_unavailable
#   CLAIM: If _WEBSOCKETS_AVAILABLE is False, calling activate() raises ImportError
#          with the correct installation hint message.
#   PATH:  activate() -> check _WEBSOCKETS_AVAILABLE -> False -> raise ImportError.
#   CHECK: ImportError raised; str(exc) == exact message string.
#   MUTATION: Not checking the flag and proceeding normally would not raise.
#   ESCAPE: Raising ImportError with a different message fails the exact string check.
def test_async_activate_raises_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import bigfoot.plugins.websocket_plugin as _wsp

    v, p = _make_async_verifier_with_plugin()
    monkeypatch.setattr(_wsp, "_WEBSOCKETS_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install bigfoot[websockets] to use AsyncWebSocketPlugin: pip install bigfoot[websockets]"
    )


# ---------------------------------------------------------------------------
# close() releases session
# ---------------------------------------------------------------------------


# ESCAPE: test_async_close_releases_session
#   CLAIM: After ws.close() is called, the session is removed from _active_sessions
#          and get_unused_mocks() returns nothing (all steps consumed).
#   PATH:  connect -> bind; close -> _execute_step("close") -> _release_session(fake_ws) ->
#          key removed from _active_sessions.
#   CHECK: len(p._active_sessions) == 0 after sandbox; p.get_unused_mocks() == [].
#   MUTATION: Not calling _release_session in close interceptor leaves session in _active_sessions.
#   ESCAPE: _active_sessions having len > 0 would fail the length check.
async def test_async_close_releases_session() -> None:
    v, p = _make_async_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        async with websockets.connect("ws://localhost:8765") as ws:
            await ws.close()

    assert len(p._active_sessions) == 0
    assert p.get_unused_mocks() == []

    v.assert_interaction(p.connect, uri="ws://localhost:8765")
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.async_websocket_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_async_websocket_mock_proxy_new_session
#   CLAIM: bigfoot.async_websocket_mock.new_session() returns a SessionHandle.
#   PATH:  _AsyncWebSocketProxy.__getattr__("new_session") -> get verifier ->
#          find/create AsyncWebSocketPlugin -> return plugin.new_session.
#   CHECK: session is a SessionHandle instance; chaining .expect() returns self.
#   MUTATION: Returning None instead of a SessionHandle fails isinstance check.
#   ESCAPE: Nothing reasonable -- both isinstance and chained .expect() call check it.
def test_async_websocket_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    session = bigfoot.async_websocket_mock.new_session()
    assert isinstance(session, SessionHandle)
    result = session.expect("connect", returns=None, required=False)
    assert result is session


# ESCAPE: test_async_websocket_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.async_websocket_mock outside a test context raises
#          NoActiveVerifierError.
#   PATH:  _AsyncWebSocketProxy.__getattr__ -> _get_test_verifier_or_raise ->
#          NoActiveVerifierError.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Silently returning None would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_async_websocket_mock_proxy_raises_outside_context() -> None:
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.async_websocket_mock.new_session
    finally:
        _current_test_verifier.reset(token)


# ===========================================================================
# SyncWebSocketPlugin
# ===========================================================================

# ---------------------------------------------------------------------------
# Static interface: _initial_state / _transitions / _unmocked_source_id
# ---------------------------------------------------------------------------


# ESCAPE: test_sync_initial_state
#   CLAIM: _initial_state() returns "connecting".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "connecting".
#   MUTATION: Returning "disconnected" fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_sync_initial_state() -> None:
    v, p = _make_sync_verifier_with_plugin()
    assert p._initial_state() == "connecting"


# ESCAPE: test_sync_transitions_structure
#   CLAIM: _transitions() returns the exact expected dict.
#   PATH:  Direct call on plugin instance.
#   CHECK: result == exact dict.
#   MUTATION: Any missing key or wrong state name fails the equality check.
#   ESCAPE: Extra keys also fail the equality check.
def test_sync_transitions_structure() -> None:
    v, p = _make_sync_verifier_with_plugin()
    assert p._transitions() == {
        "connect": {"connecting": "open"},
        "send": {"open": "open"},
        "recv": {"open": "open"},
        "close": {"open": "closed"},
    }


# ESCAPE: test_sync_unmocked_source_id
#   CLAIM: _unmocked_source_id() returns "websocket:sync:connect".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "websocket:sync:connect".
#   MUTATION: Returning a different string fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_sync_unmocked_source_id() -> None:
    v, p = _make_sync_verifier_with_plugin()
    assert p._unmocked_source_id() == "websocket:sync:connect"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_sync_activate_installs_patches
#   CLAIM: After activate(), websocket.create_connection is replaced with bigfoot interceptor.
#   PATH:  activate() -> _install_count == 0 -> store original -> install interceptor.
#   CHECK: websocket.create_connection is not the original after activate().
#   MUTATION: Skipping patch installation leaves original in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison proves replacement.
def test_sync_activate_installs_patches() -> None:
    import websocket as _wsc

    v, p = _make_sync_verifier_with_plugin()
    original = _wsc.create_connection
    p.activate()
    assert _wsc.create_connection is not original
    p.deactivate()


# ESCAPE: test_sync_deactivate_restores_patches
#   CLAIM: After activate() then deactivate(), websocket.create_connection is restored.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore original.
#   CHECK: websocket.create_connection is the original after deactivate().
#   MUTATION: Not restoring in deactivate() leaves bigfoot's interceptor in place.
#   ESCAPE: Nothing reasonable -- identity comparison against saved original.
def test_sync_deactivate_restores_patches() -> None:
    import websocket as _wsc

    v, p = _make_sync_verifier_with_plugin()
    original = _wsc.create_connection
    p.activate()
    p.deactivate()
    assert _wsc.create_connection is original


# ESCAPE: test_sync_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patch is removed.
#   PATH:  First activate -> _install_count=1; second -> _install_count=2 (no reinstall).
#          First deactivate -> _install_count=1 (patch remains).
#          Second deactivate -> _install_count=0 (original restored).
#   CHECK: After first deactivate, websocket.create_connection is still patched.
#          After second deactivate, it is the original.
#   MUTATION: Restoring on first deactivate fails the mid-point identity check.
#   ESCAPE: Nothing reasonable -- sequential identity checks.
def test_sync_reference_counting_nested() -> None:
    import websocket as _wsc

    v, p = _make_sync_verifier_with_plugin()
    original = _wsc.create_connection
    p.activate()
    p.activate()
    assert SyncWebSocketPlugin._install_count == 2

    p.deactivate()
    assert SyncWebSocketPlugin._install_count == 1
    assert _wsc.create_connection is not original

    p.deactivate()
    assert SyncWebSocketPlugin._install_count == 0
    assert _wsc.create_connection is original


# ---------------------------------------------------------------------------
# Basic session lifecycle: connect -> send -> recv -> close
# ---------------------------------------------------------------------------


# ESCAPE: test_sync_basic_connect_send_recv_close
#   CLAIM: A full session (connect, send, recv, close) completes without error;
#          each step returns the configured value.
#   PATH:  activate -> websocket.create_connection() -> pops handle -> execute "connect" step ->
#          ws.send() -> "send" step; ws.recv() -> "recv" step; ws.close() -> "close" step.
#   CHECK: ws is not None; send returns None; recv returns "hello"; close returns None.
#   MUTATION: Returning wrong value from any step fails the corresponding equality check.
#   ESCAPE: recv returning b"hello" instead of "hello" fails the equality check.
def test_sync_basic_connect_send_recv_close() -> None:
    v, p = _make_sync_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("send", returns=None)
    session.expect("recv", returns="hello")
    session.expect("close", returns=None)

    with v.sandbox():
        ws = websocket.create_connection("ws://localhost:8765")
        send_result = ws.send("ping")
        recv_result = ws.recv()
        close_result = ws.close()

    assert send_result is None
    assert recv_result == "hello"
    assert close_result is None

    v.assert_interaction(p.connect, uri="ws://localhost:8765")
    v.assert_interaction(p.send, message="ping")
    v.assert_interaction(p.recv, message="hello")
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# InvalidStateError: wrong state
# ---------------------------------------------------------------------------


# ESCAPE: test_sync_recv_before_connect_raises_invalid_state
#   CLAIM: Calling _execute_step with "recv" from state "connecting" raises InvalidStateError.
#   PATH:  _execute_step(handle, "recv", ...) with handle._state="connecting"
#          -> "connecting" not in recv's transitions -> InvalidStateError.
#   CHECK: InvalidStateError raised; exc.source_id == "websocket:sync:recv";
#          exc.method == "recv"; exc.current_state == "connecting";
#          exc.valid_states == frozenset({"open"}).
#   MUTATION: Not checking current state allows the call through without raising.
#   ESCAPE: Raising with wrong method name fails the attribute check.
def test_sync_recv_before_connect_raises_invalid_state() -> None:
    v, p = _make_sync_verifier_with_plugin()
    session = p.new_session()
    fake_obj = object()
    handle = p._bind_connection(fake_obj)
    assert handle._state == "connecting"

    with pytest.raises(InvalidStateError) as exc_info:
        p._execute_step(handle, "recv", (), {}, "websocket:sync:recv")

    exc = exc_info.value
    assert exc.source_id == "websocket:sync:recv"
    assert exc.method == "recv"
    assert exc.current_state == "connecting"
    assert exc.valid_states == frozenset({"open"})


# ---------------------------------------------------------------------------
# ImportError when websocket-client not installed
# ---------------------------------------------------------------------------


# ESCAPE: test_sync_importerror_flag
#   CLAIM: _WEBSOCKET_CLIENT_AVAILABLE is True when websocket-client is importable.
#   PATH:  Module-level try/except import guard.
#   CHECK: _WEBSOCKET_CLIENT_AVAILABLE == True (since pytest.importorskip ensured it).
#   MUTATION: Setting it to False when websocket-client IS importable fails the check.
#   ESCAPE: Nothing reasonable -- exact boolean equality.
def test_sync_importerror_flag() -> None:
    assert _WEBSOCKET_CLIENT_AVAILABLE is True


# ESCAPE: test_sync_activate_raises_when_unavailable
#   CLAIM: If _WEBSOCKET_CLIENT_AVAILABLE is False, calling activate() raises ImportError
#          with the correct installation hint message.
#   PATH:  activate() -> check _WEBSOCKET_CLIENT_AVAILABLE -> False -> raise ImportError.
#   CHECK: ImportError raised; str(exc) == exact message string.
#   MUTATION: Not checking the flag and proceeding normally would not raise.
#   ESCAPE: Raising ImportError with a different message fails the exact string check.
def test_sync_activate_raises_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import bigfoot.plugins.websocket_plugin as _wsp

    v, p = _make_sync_verifier_with_plugin()
    monkeypatch.setattr(_wsp, "_WEBSOCKET_CLIENT_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install bigfoot[websocket-client] to use SyncWebSocketPlugin: "
        "pip install bigfoot[websocket-client]"
    )


# ---------------------------------------------------------------------------
# UnmockedInteractionError when no session queued at connect time
# ---------------------------------------------------------------------------


# ESCAPE: test_sync_connect_with_empty_queue_raises_unmocked
#   CLAIM: If no session is queued when websocket.create_connection() fires,
#          UnmockedInteractionError is raised with source_id == "websocket:sync:connect".
#   PATH:  _patched_create_connection -> _get_sync_websocket_plugin() ->
#          _session_queue empty -> raise UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised; exc.source_id == "websocket:sync:connect".
#   MUTATION: Returning a dummy session for empty queue would not raise at all.
#   ESCAPE: Raising with source_id == "websocket:async:connect" fails the source_id check.
def test_sync_connect_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_sync_verifier_with_plugin()
    # No session registered

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            websocket.create_connection("ws://localhost:8765")

    assert exc_info.value.source_id == "websocket:sync:connect"


# ---------------------------------------------------------------------------
# close() releases session
# ---------------------------------------------------------------------------


# ESCAPE: test_sync_close_releases_session
#   CLAIM: After ws.close() is called, the session is removed from _active_sessions
#          and get_unused_mocks() returns nothing (all steps consumed).
#   PATH:  create_connection -> bind; close -> _execute_step("close") ->
#          _release_session(fake_ws) -> key removed from _active_sessions.
#   CHECK: len(p._active_sessions) == 0 after sandbox; p.get_unused_mocks() == [].
#   MUTATION: Not calling _release_session in close interceptor leaves session in _active_sessions.
#   ESCAPE: _active_sessions having len > 0 would fail the length check.
def test_sync_close_releases_session() -> None:
    v, p = _make_sync_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        ws = websocket.create_connection("ws://localhost:8765")
        ws.close()

    assert len(p._active_sessions) == 0
    assert p.get_unused_mocks() == []

    v.assert_interaction(p.connect, uri="ws://localhost:8765")
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# FIFO ordering: two sequential sync sessions
# ---------------------------------------------------------------------------


# ESCAPE: test_sync_fifo_two_sessions
#   CLAIM: Two sessions are consumed in registration order.
#   PATH:  new_session x2 -> two handles in _session_queue (FIFO deque).
#          First create_connection -> pops first handle immediately.
#          Second create_connection -> pops second handle immediately.
#   CHECK: first_recv_result == "first"; second_recv_result == "second".
#   MUTATION: Reversing FIFO order (LIFO) swaps the returned values; both checks fail.
#   ESCAPE: Nothing reasonable -- exact string equality on distinct values.
def test_sync_fifo_two_sessions() -> None:
    v, p = _make_sync_verifier_with_plugin()
    session1 = p.new_session()
    session1.expect("connect", returns=None)
    session1.expect("recv", returns="first")
    session1.expect("close", returns=None)

    session2 = p.new_session()
    session2.expect("connect", returns=None)
    session2.expect("recv", returns="second")
    session2.expect("close", returns=None)

    with v.sandbox():
        ws1 = websocket.create_connection("ws://localhost:8765")
        ws2 = websocket.create_connection("ws://localhost:8766")
        first_recv_result = ws1.recv()
        second_recv_result = ws2.recv()
        ws1.close()
        ws2.close()

    assert first_recv_result == "first"
    assert second_recv_result == "second"

    v.assert_interaction(p.connect, uri="ws://localhost:8765")
    v.assert_interaction(p.connect, uri="ws://localhost:8766")
    v.assert_interaction(p.recv, message="first")
    v.assert_interaction(p.recv, message="second")
    v.assert_interaction(p.close)
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.sync_websocket_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_sync_websocket_mock_proxy_new_session
#   CLAIM: bigfoot.sync_websocket_mock.new_session() returns a SessionHandle.
#   PATH:  _SyncWebSocketProxy.__getattr__("new_session") -> get verifier ->
#          find/create SyncWebSocketPlugin -> return plugin.new_session.
#   CHECK: session is a SessionHandle instance; chaining .expect() returns self.
#   MUTATION: Returning None instead of a SessionHandle fails isinstance check.
#   ESCAPE: Nothing reasonable -- both isinstance and chained .expect() call check it.
def test_sync_websocket_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    session = bigfoot.sync_websocket_mock.new_session()
    assert isinstance(session, SessionHandle)
    result = session.expect("connect", returns=None, required=False)
    assert result is session


# ESCAPE: test_sync_websocket_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.sync_websocket_mock outside a test context raises
#          NoActiveVerifierError.
#   PATH:  _SyncWebSocketProxy.__getattr__ -> _get_test_verifier_or_raise ->
#          NoActiveVerifierError.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Silently returning None would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_sync_websocket_mock_proxy_raises_outside_context() -> None:
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.sync_websocket_mock.new_session
    finally:
        _current_test_verifier.reset(token)
