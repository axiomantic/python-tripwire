"""Unit tests for SocketPlugin."""

from __future__ import annotations

import socket

import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import InvalidStateError, UnmockedInteractionError
from bigfoot._state_machine_plugin import ScriptStep
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.socket_plugin import (
    _SOCKET_CLOSE_ORIGINAL,
    _SOCKET_CONNECT_ORIGINAL,
    _SOCKET_RECV_ORIGINAL,
    _SOCKET_SEND_ORIGINAL,
    _SOCKET_SENDALL_ORIGINAL,
    SocketPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, SocketPlugin]:
    """Return (verifier, plugin) with plugin registered but NOT activated."""
    v = StrictVerifier()
    p = SocketPlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with SocketPlugin._install_lock:
        SocketPlugin._install_count = 0
        if SocketPlugin._original_connect is not None:
            socket.socket.connect = SocketPlugin._original_connect
            SocketPlugin._original_connect = None
        if SocketPlugin._original_send is not None:
            socket.socket.send = SocketPlugin._original_send
            SocketPlugin._original_send = None
        if SocketPlugin._original_sendall is not None:
            socket.socket.sendall = SocketPlugin._original_sendall
            SocketPlugin._original_sendall = None
        if SocketPlugin._original_recv is not None:
            socket.socket.recv = SocketPlugin._original_recv
            SocketPlugin._original_recv = None
        if SocketPlugin._original_close is not None:
            socket.socket.close = SocketPlugin._original_close
            SocketPlugin._original_close = None


@pytest.fixture(autouse=True)
def clean_install_count() -> None:
    """Ensure SocketPlugin install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield
    _reset_install_count()


# ---------------------------------------------------------------------------
# Static interface: _initial_state / _transitions / _unmocked_source_id
# ---------------------------------------------------------------------------


# ESCAPE: test_initial_state
#   CLAIM: _initial_state() returns "disconnected".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "disconnected".
#   MUTATION: Returning "connected" would fail the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_initial_state() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._initial_state() == "disconnected"


# ESCAPE: test_transitions_structure
#   CLAIM: _transitions() returns the exact expected dict.
#   PATH:  Direct call on plugin instance.
#   CHECK: result == exact dict mapping method names to {from_state: to_state}.
#   MUTATION: Any missing key or wrong state name fails the equality check.
#   ESCAPE: Extra keys in the dict would also fail the equality check.
def test_transitions_structure() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._transitions() == {
        "connect": {"disconnected": "connected"},
        "send": {"connected": "connected"},
        "sendall": {"connected": "connected"},
        "recv": {"connected": "connected"},
        "close": {"connected": "closed"},
    }


# ESCAPE: test_unmocked_source_id
#   CLAIM: _unmocked_source_id() returns "socket:connect".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "socket:connect".
#   MUTATION: Returning a different string fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_unmocked_source_id() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._unmocked_source_id() == "socket:connect"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patches
#   CLAIM: After activate(), socket.socket.connect/send/sendall/recv/close are
#          replaced with bigfoot interceptors (not the originals anymore).
#   PATH:  activate() -> _install_count == 0 -> store originals -> install interceptors.
#   CHECK: Each method is not the same object as the import-time original.
#   MUTATION: Skipping patch installation leaves originals in place; identity checks fail.
#   ESCAPE: Nothing reasonable -- identity comparison against import-time constants.
def test_activate_installs_patches() -> None:
    v, p = _make_verifier_with_plugin()
    assert socket.socket.connect is _SOCKET_CONNECT_ORIGINAL
    assert socket.socket.send is _SOCKET_SEND_ORIGINAL
    assert socket.socket.sendall is _SOCKET_SENDALL_ORIGINAL
    assert socket.socket.recv is _SOCKET_RECV_ORIGINAL
    assert socket.socket.close is _SOCKET_CLOSE_ORIGINAL
    p.activate()
    assert socket.socket.connect is not _SOCKET_CONNECT_ORIGINAL
    assert socket.socket.send is not _SOCKET_SEND_ORIGINAL
    assert socket.socket.sendall is not _SOCKET_SENDALL_ORIGINAL
    assert socket.socket.recv is not _SOCKET_RECV_ORIGINAL
    assert socket.socket.close is not _SOCKET_CLOSE_ORIGINAL


# ESCAPE: test_deactivate_restores_patches
#   CLAIM: After activate() then deactivate(), all five socket.socket methods
#          are restored to the import-time originals.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore originals.
#   CHECK: All five methods are the import-time originals again.
#   MUTATION: Not restoring in deactivate() leaves bigfoot's interceptors in place.
#   ESCAPE: Nothing reasonable -- identity comparison against import-time constants.
def test_deactivate_restores_patches() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert socket.socket.connect is _SOCKET_CONNECT_ORIGINAL
    assert socket.socket.send is _SOCKET_SEND_ORIGINAL
    assert socket.socket.sendall is _SOCKET_SENDALL_ORIGINAL
    assert socket.socket.recv is _SOCKET_RECV_ORIGINAL
    assert socket.socket.close is _SOCKET_CLOSE_ORIGINAL


# ESCAPE: test_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patches are removed.
#   PATH:  First activate -> _install_count=1; second activate -> _install_count=2 (no reinstall).
#          First deactivate -> _install_count=1 (patches remain).
#          Second deactivate -> _install_count=0 (originals restored).
#   CHECK: After first deactivate, socket.socket.connect is still patched.
#          After second deactivate, it is the original.
#   MUTATION: Restoring on first deactivate would fail the mid-point identity check.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove count-controlled restoration.
def test_reference_counting_nested() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert SocketPlugin._install_count == 2

    p.deactivate()
    assert SocketPlugin._install_count == 1
    assert socket.socket.connect is not _SOCKET_CONNECT_ORIGINAL

    p.deactivate()
    assert SocketPlugin._install_count == 0
    assert socket.socket.connect is _SOCKET_CONNECT_ORIGINAL


# ---------------------------------------------------------------------------
# Basic session lifecycle: connect -> send -> recv -> close
# ---------------------------------------------------------------------------


# ESCAPE: test_basic_connect_send_recv_close
#   CLAIM: A full session (connect, send, recv, close) completes without error;
#          each step returns the configured value and advances state correctly.
#   PATH:  activate -> _bind_connection -> _execute_step(connect) -> state=connected;
#          _execute_step(send) -> state=connected; _execute_step(recv) -> state=connected;
#          _execute_step(close) -> state=closed; _release_session.
#   CHECK: connect returns None; send returns 4; recv returns b"pong"; close returns None.
#          After the sandbox, no UnusedMocksError or InvalidStateError is raised.
#   MUTATION: Returning wrong values from any step fails the corresponding equality check.
#   ESCAPE: A step returning b"wrong" instead of b"pong" passes connect/send/close
#           but fails the recv assertion.
def test_basic_connect_send_recv_close() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("send", returns=4)
    session.expect("recv", returns=b"pong")
    session.expect("close", returns=None)

    sock = socket.socket()
    with v.sandbox():
        connect_result = sock.connect(("127.0.0.1", 9999))
        send_result = sock.send(b"ping")
        recv_result = sock.recv(1024)
        close_result = sock.close()

    assert connect_result is None
    assert send_result == 4
    assert recv_result == b"pong"
    assert close_result is None


# ---------------------------------------------------------------------------
# InvalidStateError: recv before connect
# ---------------------------------------------------------------------------


# ESCAPE: test_recv_before_connect_raises_invalid_state
#   CLAIM: Calling recv on a fresh socket (state="disconnected") before connect
#          raises InvalidStateError with the correct attributes.
#   PATH:  activate -> _bind_connection (via connect interceptor... wait, recv would
#          not be bound yet). Actually: _patched_recv calls _lookup_session which raises
#          UnmockedInteractionError if not bound yet. But the intent is state machine.
#          The state machine test: call connect first (bind), then call recv immediately
#          to verify recv is valid from "connected". But if we want recv-before-connect:
#          we must bind the connection first via connect, then check send->close sequence.
#
#          Alternatively: use new_session(), bind_connection manually, then call _execute_step
#          with "recv" from state "disconnected" to trigger InvalidStateError.
#
#   PATH (revised): _execute_step(handle, "recv", ...) with handle._state="disconnected"
#          -> method "recv" exists but "disconnected" not in method_transitions -> InvalidStateError.
#   CHECK: InvalidStateError raised; exc.source_id == "socket:recv";
#          exc.method == "recv"; exc.current_state == "disconnected";
#          exc.valid_states == frozenset({"connected"}).
#   MUTATION: Not checking current state and allowing the call through would not raise.
#   ESCAPE: Raising InvalidStateError with wrong source_id, method, or current_state
#           would fail the corresponding attribute equality checks.
def test_recv_before_connect_raises_invalid_state() -> None:
    v, p = _make_verifier_with_plugin()
    # Create a handle manually in "disconnected" state (initial)
    session = p.new_session()
    # Bind the session without going through connect (inject directly)
    sock = socket.socket()
    # Manually bind by calling _bind_connection directly (bypasses patched connect)
    handle = p._bind_connection(sock)
    # handle._state is "disconnected" at this point
    assert handle._state == "disconnected"

    with pytest.raises(InvalidStateError) as exc_info:
        p._execute_step(handle, "recv", (1024,), {}, "socket:recv")

    exc = exc_info.value
    assert exc.source_id == "socket:recv"
    assert exc.method == "recv"
    assert exc.current_state == "disconnected"
    assert exc.valid_states == frozenset({"connected"})


# ---------------------------------------------------------------------------
# FIFO ordering: two sequential sessions
# ---------------------------------------------------------------------------


# ESCAPE: test_fifo_two_sessions
#   CLAIM: Two sessions are consumed in registration order; the first socket gets
#          the first session's script, the second socket gets the second session's script.
#   PATH:  new_session x2 -> two handles in _session_queue (FIFO deque).
#          First sock.connect -> _bind_connection pops first handle.
#          Second sock.connect -> _bind_connection pops second handle.
#          Each recv returns its own configured value.
#   CHECK: first_recv_result == b"first"; second_recv_result == b"second".
#   MUTATION: Reversing FIFO order (LIFO) would swap the returned values; both checks fail.
#   ESCAPE: Nothing reasonable -- exact bytes equality on distinct values.
def test_fifo_two_sessions() -> None:
    v, p = _make_verifier_with_plugin()
    session1 = p.new_session()
    session1.expect("connect", returns=None)
    session1.expect("recv", returns=b"first")
    session1.expect("close", returns=None)

    session2 = p.new_session()
    session2.expect("connect", returns=None)
    session2.expect("recv", returns=b"second")
    session2.expect("close", returns=None)

    sock1 = socket.socket()
    sock2 = socket.socket()
    with v.sandbox():
        sock1.connect(("127.0.0.1", 9999))
        sock2.connect(("127.0.0.1", 9998))
        first_recv_result = sock1.recv(1024)
        second_recv_result = sock2.recv(1024)
        sock1.close()
        sock2.close()

    assert first_recv_result == b"first"
    assert second_recv_result == b"second"


# ---------------------------------------------------------------------------
# get_unused_mocks: unconsumed required steps
# ---------------------------------------------------------------------------


# ESCAPE: test_get_unused_mocks_returns_unconsumed_steps
#   CLAIM: When a session has two expected steps but only one is consumed,
#          get_unused_mocks() returns exactly the one unconsumed required step.
#   PATH:  new_session with two expect() calls -> connect() consumes step 0 ->
#          session still has step 1 in _script -> _active_sessions has handle with
#          one remaining required step -> get_unused_mocks() returns it.
#   CHECK: len(unused) == 1; unused[0] is a ScriptStep with method == "recv".
#   MUTATION: Returning all steps (including consumed) would give len == 2; fails count check.
#   ESCAPE: Returning a step with method == "connect" instead of "recv" fails method check.
def test_get_unused_mocks_returns_unconsumed_steps() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("recv", returns=b"data")  # will NOT be consumed

    sock = socket.socket()
    with v.sandbox():
        sock.connect(("127.0.0.1", 9999))
        # deliberately NOT calling recv or close

    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].method == "recv"


# ESCAPE: test_get_unused_mocks_queued_session
#   CLAIM: A session that was queued but never bound (no connect was called)
#          has all its required steps returned by get_unused_mocks().
#   PATH:  new_session with two steps enqueued -> no connect -> _session_queue still holds handle ->
#          get_unused_mocks() iterates _session_queue and returns all required steps.
#   CHECK: len(unused) == 2; methods are ["connect", "recv"] in order.
#   MUTATION: Not iterating _session_queue would return [] instead of 2 items.
#   ESCAPE: Returning items in wrong order (LIFO) would fail the method ordering check.
def test_get_unused_mocks_queued_session() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("recv", returns=b"data")

    # Never call connect; the session stays in the queue
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "connect"
    assert unused[1].method == "recv"


# ---------------------------------------------------------------------------
# UnmockedInteractionError when no session queued at connect time
# ---------------------------------------------------------------------------


# ESCAPE: test_connect_with_empty_queue_raises_unmocked
#   CLAIM: If no session is queued when a socket.connect() fires,
#          UnmockedInteractionError is raised with source_id == "socket:connect".
#   PATH:  _patched_connect -> _get_socket_plugin() -> plugin._bind_connection(sock) ->
#          _session_queue empty -> raise UnmockedInteractionError(source_id="socket:connect").
#   CHECK: UnmockedInteractionError raised; exc.source_id == "socket:connect".
#   MUTATION: Returning a dummy session for empty queue would not raise at all.
#   ESCAPE: Raising with source_id == "socket:send" instead would fail the source_id check.
def test_connect_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_verifier_with_plugin()
    # No session registered

    sock = socket.socket()
    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            sock.connect(("127.0.0.1", 9999))

    assert exc_info.value.source_id == "socket:connect"


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.socket_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_socket_mock_proxy_new_session
#   CLAIM: bigfoot.socket_mock.new_session() returns a SessionHandle that can
#          be used to configure a session without importing SocketPlugin directly.
#   PATH:  _SocketProxy.__getattr__("new_session") -> get verifier -> find/create SocketPlugin ->
#          return plugin.new_session.
#   CHECK: session is a SessionHandle instance (no AttributeError, no None).
#          Chaining .expect() on it does not raise.
#   MUTATION: Returning None instead of a SessionHandle would fail isinstance check.
#   ESCAPE: Nothing reasonable -- both the isinstance and the chained .expect() call check it.
def test_socket_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    from bigfoot._state_machine_plugin import SessionHandle

    session = bigfoot.socket_mock.new_session()
    assert isinstance(session, SessionHandle)
    # Chaining expect() with required=False so it doesn't trigger UnusedMocksError at teardown.
    result = session.expect("connect", returns=None, required=False)
    assert result is session  # expect() returns self for chaining


# ESCAPE: test_socket_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.socket_mock outside a test context raises NoActiveVerifierError.
#   PATH:  _SocketProxy.__getattr__ -> _get_test_verifier_or_raise -> NoActiveVerifierError.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Silently returning None would not raise and hide context failures.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_socket_mock_proxy_raises_outside_context() -> None:
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.socket_mock.new_session
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# sendall interceptor
# ---------------------------------------------------------------------------


# ESCAPE: test_sendall_step
#   CLAIM: sock.sendall() inside a sandbox returns the configured value and
#          advances state from "connected" to "connected".
#   PATH:  connect -> bind -> state=connected; sendall -> _execute_step("sendall") ->
#          state stays "connected" -> return configured returns value.
#   CHECK: sendall_result is None (sendall conventionally returns None on success).
#   MUTATION: Returning b"something" instead of None fails the `is None` check.
#   ESCAPE: Nothing reasonable -- exact None identity.
def test_sendall_step() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("sendall", returns=None)
    session.expect("close", returns=None)

    sock = socket.socket()
    with v.sandbox():
        sock.connect(("127.0.0.1", 9999))
        sendall_result = sock.sendall(b"hello world")
        sock.close()

    assert sendall_result is None


# ---------------------------------------------------------------------------
# close() releases session
# ---------------------------------------------------------------------------


# ESCAPE: test_close_releases_session
#   CLAIM: After sock.close() is called, the session is removed from _active_sessions,
#          and get_unused_mocks() returns nothing (all steps consumed).
#   PATH:  connect -> bind; close -> _execute_step("close") -> _release_session(sock) ->
#          key removed from _active_sessions -> get_unused_mocks() finds no active sessions.
#   CHECK: len(p._active_sessions) == 0 after sandbox; get_unused_mocks() == [].
#   MUTATION: Not calling _release_session in close interceptor leaves session in _active_sessions.
#   ESCAPE: _active_sessions having len > 0 would fail the length check.
def test_close_releases_session() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    sock = socket.socket()
    with v.sandbox():
        sock.connect(("127.0.0.1", 9999))
        sock.close()

    assert len(p._active_sessions) == 0
    assert p.get_unused_mocks() == []
