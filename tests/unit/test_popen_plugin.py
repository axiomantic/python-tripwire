"""Unit tests for PopenPlugin."""

from __future__ import annotations

import shutil
import subprocess

import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import InvalidStateError, UnmockedInteractionError
from bigfoot._state_machine_plugin import ScriptStep
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.popen_plugin import (
    _ORIGINAL_POPEN,
    PopenPlugin,
    _FakePopen,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, PopenPlugin]:
    """Return (verifier, plugin) with plugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    PopenPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, PopenPlugin):
            return v, p
    p = PopenPlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore Popen if leaked."""
    with PopenPlugin._install_lock:
        PopenPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        PopenPlugin.__new__(PopenPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_install_count() -> None:
    """Ensure PopenPlugin install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield
    _reset_install_count()


# ---------------------------------------------------------------------------
# Static interface: _initial_state / _transitions / _unmocked_source_id
# ---------------------------------------------------------------------------


# ESCAPE: test_initial_state
#   CLAIM: _initial_state() returns "created".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "created".
#   MUTATION: Returning "running" would fail the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_initial_state() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._initial_state() == "created"


# ESCAPE: test_transitions_structure
#   CLAIM: _transitions() returns the exact expected dict.
#   PATH:  Direct call on plugin instance.
#   CHECK: result == exact dict mapping method names to {from_state: to_state}.
#   MUTATION: Any missing key or wrong state name fails the equality check.
#   ESCAPE: Extra keys in the dict would also fail the equality check.
def test_transitions_structure() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._transitions() == {
        "spawn": {"created": "running"},
        "communicate": {"running": "terminated"},
        "wait": {"running": "terminated"},
    }


# ESCAPE: test_unmocked_source_id
#   CLAIM: _unmocked_source_id() returns "subprocess:popen:spawn".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "subprocess:popen:spawn".
#   MUTATION: Returning a different string fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_unmocked_source_id() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._unmocked_source_id() == "subprocess:popen:spawn"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patch
#   CLAIM: After activate(), subprocess.Popen is replaced with _FakePopen.
#   PATH:  activate() -> _install_count == 0 -> store original -> install _FakePopen.
#   CHECK: subprocess.Popen is _FakePopen (the fake class, not the original).
#   MUTATION: Skipping patch installation leaves original in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison against _FakePopen class.
def test_activate_installs_patch() -> None:
    v, p = _make_verifier_with_plugin()
    assert subprocess.Popen is _ORIGINAL_POPEN
    p.activate()
    assert subprocess.Popen is _FakePopen


# ESCAPE: test_deactivate_restores_patch
#   CLAIM: After activate() then deactivate(), subprocess.Popen is the original again.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore original Popen.
#   CHECK: subprocess.Popen is _ORIGINAL_POPEN.
#   MUTATION: Not restoring in deactivate() leaves _FakePopen in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison against import-time constant.
def test_deactivate_restores_patch() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert subprocess.Popen is _ORIGINAL_POPEN


# ESCAPE: test_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patch is removed.
#   PATH:  First activate -> _install_count=1; second activate -> _install_count=2 (no reinstall).
#          First deactivate -> _install_count=1 (patch remains).
#          Second deactivate -> _install_count=0 (original restored).
#   CHECK: After first deactivate, subprocess.Popen is still _FakePopen.
#          After second deactivate, subprocess.Popen is _ORIGINAL_POPEN.
#   MUTATION: Restoring on first deactivate would fail the mid-point identity check.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove count-controlled restoration.
def test_reference_counting_nested() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert PopenPlugin._install_count == 2

    p.deactivate()
    assert PopenPlugin._install_count == 1
    assert subprocess.Popen is _FakePopen

    p.deactivate()
    assert PopenPlugin._install_count == 0
    assert subprocess.Popen is _ORIGINAL_POPEN


# ---------------------------------------------------------------------------
# Basic subprocess.Popen() call: spawn step
# ---------------------------------------------------------------------------


# ESCAPE: test_popen_spawn_step_consumed
#   CLAIM: subprocess.Popen(["cmd"]) inside a sandbox consumes the "spawn" step and
#          returns a _FakePopen instance.
#   PATH:  sandbox -> activate -> _FakePopen.__init__ -> _bind_connection ->
#          _execute_step(handle, "spawn", ...) -> step consumed -> state = "running".
#   CHECK: result is an instance of _FakePopen; handle state is "running" after spawn.
#   MUTATION: Not consuming the "spawn" step leaves it in _script; state stays "created".
#   ESCAPE: Returning an instance that is not _FakePopen would fail the isinstance check.
def test_popen_spawn_step_consumed() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)

    with v.sandbox():
        proc = subprocess.Popen(["ls", "-la"])

    v.assert_interaction(p.spawn, command=["ls", "-la"], stdin=None)

    assert type(proc).__name__ == "_FakePopen"
    assert proc.pid == 12345
    # Session should be in "running" state; check via _active_sessions
    assert len(p._active_sessions) == 1
    handle = list(p._active_sessions.values())[0]
    assert handle._state == "running"


# ---------------------------------------------------------------------------
# stdin.write() -- no-op, not recorded on timeline
# ---------------------------------------------------------------------------


# ESCAPE: test_stdin_write_noop
#   CLAIM: proc.stdin.write(b"data") returns 0 and does NOT consume any script step.
#   PATH:  _FakeStream.write() -> returns 0 directly, no _execute_step call.
#   CHECK: write_result == 0; state stays "running" (no additional step consumed).
#   MUTATION: Returning a different value (e.g., 5) fails the equality check.
#   ESCAPE: Nothing reasonable -- exact integer equality.
def test_stdin_write_noop() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)

    with v.sandbox():
        proc = subprocess.Popen(["cmd"], stdin=subprocess.PIPE)
        write_result = proc.stdin.write(b"hello")

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)

    assert write_result == 0
    assert len(p._active_sessions) == 1
    handle = list(p._active_sessions.values())[0]
    assert handle._state == "running"


# ---------------------------------------------------------------------------
# stdout.read() -- no-op, not recorded on timeline
# ---------------------------------------------------------------------------


# ESCAPE: test_stdout_read_noop
#   CLAIM: proc.stdout.read() returns b"" and does NOT consume any script step.
#   PATH:  _FakeStream.read() -> returns b"" directly, no _execute_step call.
#   CHECK: read_result == b""; state stays "running".
#   MUTATION: Returning b"something" instead fails the equality check.
#   ESCAPE: Nothing reasonable -- exact bytes equality.
def test_stdout_read_noop() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)

    with v.sandbox():
        proc = subprocess.Popen(["cmd"], stdout=subprocess.PIPE)
        read_result = proc.stdout.read()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)

    assert read_result == b""
    assert len(p._active_sessions) == 1
    handle = list(p._active_sessions.values())[0]
    assert handle._state == "running"


# ---------------------------------------------------------------------------
# stderr.read() -- no-op, not recorded on timeline
# ---------------------------------------------------------------------------


# ESCAPE: test_stderr_read_noop
#   CLAIM: proc.stderr.read() returns b"" and does NOT consume any script step.
#   PATH:  _FakeStream.read() -> returns b"" directly, no _execute_step call.
#   CHECK: read_result == b""; state stays "running".
#   MUTATION: Returning b"other error" instead fails the equality check.
#   ESCAPE: Nothing reasonable -- exact bytes equality.
def test_stderr_read_noop() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)

    with v.sandbox():
        proc = subprocess.Popen(["cmd"], stderr=subprocess.PIPE)
        read_result = proc.stderr.read()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)

    assert read_result == b""
    assert len(p._active_sessions) == 1
    handle = list(p._active_sessions.values())[0]
    assert handle._state == "running"


# ---------------------------------------------------------------------------
# communicate() step
# ---------------------------------------------------------------------------


# ESCAPE: test_communicate_step
#   CLAIM: proc.communicate() inside a sandbox consumes the "communicate" step,
#          returns (stdout, stderr) tuple, sets proc.returncode, and transitions
#          state to "terminated".
#   PATH:  _FakePopen.__init__ -> spawn step; communicate -> _execute_step
#          (handle, "communicate", ...) -> 3-tuple (stdout, stderr, returncode) ->
#          proc.returncode set; state = "terminated".
#   CHECK: stdout == b"out"; stderr == b"err"; proc.returncode == 0;
#          communicate() return == (b"out", b"err"); state == "terminated".
#   MUTATION: Not setting proc.returncode from the tuple fails the returncode check.
#   ESCAPE: Returning (b"out", b"wrong") would fail the stderr portion of the tuple check.
def test_communicate_step() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"out", b"err", 0))

    with v.sandbox():
        proc = subprocess.Popen(["cmd"])
        stdout, stderr = proc.communicate()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.communicate, input=None)

    assert stdout == b"out"
    assert stderr == b"err"
    assert proc.returncode == 0
    handle = list(p._active_sessions.values())[0]
    assert handle._state == "terminated"


# ESCAPE: test_communicate_nonzero_returncode
#   CLAIM: communicate() with a non-zero returncode in the scripted tuple correctly
#          sets proc.returncode to the non-zero value.
#   PATH:  communicate -> 3-tuple -> proc.returncode = 1.
#   CHECK: proc.returncode == 1; communicate return == (b"", b"fail output").
#   MUTATION: Hardcoding returncode = 0 would fail the proc.returncode == 1 check.
#   ESCAPE: Nothing reasonable -- exact integer equality.
def test_communicate_nonzero_returncode() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"", b"fail output", 1))

    with v.sandbox():
        proc = subprocess.Popen(["cmd"])
        stdout, stderr = proc.communicate()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.communicate, input=None)

    assert stdout == b""
    assert stderr == b"fail output"
    assert proc.returncode == 1


# ---------------------------------------------------------------------------
# wait() step
# ---------------------------------------------------------------------------


# ESCAPE: test_wait_step
#   CLAIM: proc.wait() inside a sandbox consumes the "wait" step, returns the
#          configured returncode int, sets proc.returncode, and transitions state
#          to "terminated". The session remains in _active_sessions (wait() is
#          idempotent and does not release the session).
#   PATH:  _FakePopen.__init__ -> spawn step; wait -> _execute_step
#          (handle, "wait", ...) -> int returncode -> proc.returncode set ->
#          state = "terminated".
#   CHECK: wait_result == 42; proc.returncode == 42; session state == "terminated".
#   MUTATION: Returning 0 instead of 42 would fail wait_result and returncode checks.
#   ESCAPE: Nothing reasonable -- exact integer equality.
def test_wait_step() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("wait", returns=42)

    with v.sandbox():
        proc = subprocess.Popen(["cmd"])
        wait_result = proc.wait()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.wait)

    assert wait_result == 42
    assert proc.returncode == 42
    handle = list(p._active_sessions.values())[0]
    assert handle._state == "terminated"


# ESCAPE: test_wait_is_idempotent
#   CLAIM: Calling proc.wait() multiple times returns the same returncode on each
#          call without consuming additional script steps.
#   PATH:  First wait() -> _execute_step -> returncode set -> state "terminated".
#          Second wait() -> self.returncode is not None -> early return, no step consumed.
#   CHECK: Both calls return 7; proc.returncode == 7 after both calls; only one
#          script step consumed (the session script is empty after the first wait).
#   MUTATION: Not guarding against repeated calls would attempt to consume a second
#             step from an empty script, raising UnmockedInteractionError.
#   ESCAPE: Returning different values on successive calls fails the equality checks.
def test_wait_is_idempotent() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("wait", returns=7)

    with v.sandbox():
        proc = subprocess.Popen(["cmd"])
        first = proc.wait()
        second = proc.wait()
        third = proc.wait()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.wait)

    assert first == 7
    assert second == 7
    assert third == 7
    assert proc.returncode == 7


# ---------------------------------------------------------------------------
# poll() -- no step consumed
# ---------------------------------------------------------------------------


# ESCAPE: test_poll_returns_returncode_without_consuming_step
#   CLAIM: proc.poll() returns proc.returncode without consuming any script step.
#          Before communicate/wait, returncode is None. After, it reflects the set value.
#   PATH:  poll() reads self.returncode directly; no _execute_step call.
#   CHECK: Before communicate: poll() is None. After communicate: poll() == 0.
#          No additional steps consumed (script is empty after communicate).
#   MUTATION: Calling _execute_step in poll() would consume an extra step, breaking this test.
#   ESCAPE: poll() returning a hardcoded value would fail post-communicate check for
#           non-zero returncodes.
def test_poll_returns_returncode_without_consuming_step() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"", b"", 0))

    with v.sandbox():
        proc = subprocess.Popen(["cmd"])
        assert proc.poll() is None  # returncode not yet set
        proc.communicate()
        assert proc.poll() == 0  # returncode set by communicate

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.communicate, input=None)


# ---------------------------------------------------------------------------
# pid attribute
# ---------------------------------------------------------------------------


# ESCAPE: test_fake_popen_pid_attribute
#   CLAIM: _FakePopen instances have a .pid attribute set to 12345 (fake PID).
#   PATH:  _FakePopen.__init__ sets self.pid = 12345.
#   CHECK: proc.pid == 12345.
#   MUTATION: Not setting self.pid or setting it to 0 would fail the equality check.
#   ESCAPE: Nothing reasonable -- exact integer equality.
def test_fake_popen_pid_attribute() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)

    with v.sandbox():
        proc = subprocess.Popen(["cmd"])

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)

    assert proc.pid == 12345


# ---------------------------------------------------------------------------
# InvalidStateError: communicate after terminate
# ---------------------------------------------------------------------------


# ESCAPE: test_communicate_twice_raises_invalid_state
#   CLAIM: Calling communicate() on an already-terminated session (state="terminated")
#          raises InvalidStateError, because communicate only allows "running" -> "terminated".
#   PATH:  First communicate -> state = "terminated"; second communicate -> _execute_step ->
#          state "terminated" not in method_transitions["communicate"] -> InvalidStateError.
#   CHECK: InvalidStateError raised; exc.method == "communicate";
#          exc.current_state == "terminated"; exc.valid_states == frozenset({"running"}).
#   MUTATION: Not checking from-state would allow the call through without raising.
#   ESCAPE: Raising InvalidStateError with wrong current_state would fail the attribute check.
def test_communicate_twice_raises_invalid_state() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"out", b"", 0))
    # Second communicate: no step registered (irrelevant -- InvalidStateError fires first)

    with v.sandbox():
        proc = subprocess.Popen(["cmd"])
        proc.communicate()
        with pytest.raises(InvalidStateError) as exc_info:
            proc.communicate()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.communicate, input=None)

    exc = exc_info.value
    assert exc.method == "communicate"
    assert exc.current_state == "terminated"
    assert exc.valid_states == frozenset({"running"})


# ---------------------------------------------------------------------------
# get_unused_mocks: unconsumed steps
# ---------------------------------------------------------------------------


# ESCAPE: test_get_unused_mocks_unconsumed_steps
#   CLAIM: When two steps are expected but only "spawn" is consumed (no communicate/wait),
#          get_unused_mocks() returns the one unconsumed required step.
#   PATH:  new_session with two steps -> spawn consumed -> session in _active_sessions
#          with one remaining required step -> get_unused_mocks() returns it.
#   CHECK: len(unused) == 1; unused[0].method == "communicate".
#   MUTATION: Not scanning _active_sessions for remaining steps would return [].
#   ESCAPE: Returning both steps (including consumed) would give len == 2; fails count check.
def test_get_unused_mocks_unconsumed_steps() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"", b"", 0))  # will NOT be consumed

    with v.sandbox():
        subprocess.Popen(["cmd"])
        # deliberately NOT calling communicate or wait

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].method == "communicate"


# ESCAPE: test_get_unused_mocks_queued_session_never_bound
#   CLAIM: A session queued but never bound (no Popen() called) has all its required
#          steps returned by get_unused_mocks().
#   PATH:  new_session with two steps enqueued -> no Popen() call -> _session_queue
#          still holds handle -> get_unused_mocks() iterates _session_queue.
#   CHECK: len(unused) == 2; methods are ["spawn", "communicate"] in order.
#   MUTATION: Not iterating _session_queue would return [].
#   ESCAPE: Returning items in LIFO order would fail the method ordering check.
def test_get_unused_mocks_queued_session_never_bound() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"", b"", 0))

    # Never call Popen; the session stays in the queue
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "spawn"
    assert unused[1].method == "communicate"


# ---------------------------------------------------------------------------
# UnmockedInteractionError when no session queued
# ---------------------------------------------------------------------------


# ESCAPE: test_popen_with_empty_queue_raises_unmocked
#   CLAIM: If no session is queued when subprocess.Popen() fires, UnmockedInteractionError
#          is raised with source_id == "subprocess:popen:spawn".
#   PATH:  _FakePopen.__init__ -> _bind_connection -> queue empty ->
#          UnmockedInteractionError(source_id="subprocess:popen:spawn").
#   CHECK: UnmockedInteractionError raised; exc.source_id == "subprocess:popen:spawn".
#   MUTATION: Returning a dummy session for empty queue would not raise.
#   ESCAPE: Raising with wrong source_id fails the source_id check.
def test_popen_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_verifier_with_plugin()
    # No session registered

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            subprocess.Popen(["cmd"])

    assert exc_info.value.source_id == "subprocess:popen:spawn"


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.popen_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_popen_mock_proxy_new_session
#   CLAIM: bigfoot.popen_mock.new_session() returns a SessionHandle that can
#          be used to configure a session without importing PopenPlugin directly.
#   PATH:  _PopenProxy.__getattr__("new_session") -> get verifier -> find/create PopenPlugin ->
#          return plugin.new_session.
#   CHECK: session is a SessionHandle instance; chaining .expect() does not raise.
#   MUTATION: Returning None instead of a SessionHandle would fail isinstance check.
#   ESCAPE: Nothing reasonable -- both the isinstance and the chained .expect() call check it.
def test_popen_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    from bigfoot._state_machine_plugin import SessionHandle

    session = bigfoot.popen_mock.new_session()
    assert isinstance(session, SessionHandle)
    result = session.expect("spawn", returns=None, required=False)
    assert result is session  # expect() returns self for chaining


# ESCAPE: test_popen_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.popen_mock outside a test context raises NoActiveVerifierError.
#   PATH:  _PopenProxy.__getattr__ -> _get_test_verifier_or_raise -> NoActiveVerifierError.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Silently returning None would not raise and hide context failures.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_popen_mock_proxy_raises_outside_context() -> None:
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.popen_mock.new_session
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# Coexistence with SubprocessPlugin
# ---------------------------------------------------------------------------


# ESCAPE: test_popen_and_subprocess_coexist
#   CLAIM: PopenPlugin and SubprocessPlugin can both be active simultaneously.
#          SubprocessPlugin handles subprocess.run; PopenPlugin handles subprocess.Popen.
#          Activating both does not clobber either patch.
#   PATH:  activate SubprocessPlugin (patches subprocess.run) -> activate PopenPlugin
#          (patches subprocess.Popen); both intercept correctly; deactivate both.
#   CHECK: While both active: subprocess.run is NOT _SUBPROCESS_RUN_ORIGINAL;
#          subprocess.Popen is _FakePopen.
#          After deactivating both: subprocess.run is _SUBPROCESS_RUN_ORIGINAL;
#          subprocess.Popen is _ORIGINAL_POPEN.
#   MUTATION: PopenPlugin clobbering subprocess.run would make run-original check fail.
#   ESCAPE: Nothing reasonable -- four identity checks cover all four states.
def test_popen_and_subprocess_coexist() -> None:
    import bigfoot.plugins.subprocess as _sp_mod
    from bigfoot.plugins.subprocess import (
        _SUBPROCESS_RUN_ORIGINAL,
        SubprocessPlugin,
    )

    # Save guard fixture state (session fixture may have installed patches)
    saved_count = SubprocessPlugin._install_count
    saved_original_run = SubprocessPlugin._original_subprocess_run
    saved_original_which = SubprocessPlugin._original_shutil_which
    saved_bigfoot_run = _sp_mod._bigfoot_subprocess_run
    saved_bigfoot_which = _sp_mod._bigfoot_shutil_which
    saved_run = subprocess.run
    saved_which = shutil.which

    # Reset SubprocessPlugin via its own _restore_patches() (autouse fixture only handles PopenPlugin)
    with SubprocessPlugin._install_lock:
        SubprocessPlugin._install_count = 0
        SubprocessPlugin.__new__(SubprocessPlugin).restore_patches()

    v = StrictVerifier()
    sp = SubprocessPlugin(v)
    pp = PopenPlugin(v)

    sp.activate()
    pp.activate()

    try:
        assert subprocess.run is not _SUBPROCESS_RUN_ORIGINAL
        assert subprocess.Popen is _FakePopen
    finally:
        pp.deactivate()
        sp.deactivate()

    assert subprocess.run is _SUBPROCESS_RUN_ORIGINAL
    assert subprocess.Popen is _ORIGINAL_POPEN

    # Restore guard fixture state
    with SubprocessPlugin._install_lock:
        SubprocessPlugin._install_count = saved_count
        SubprocessPlugin._original_subprocess_run = saved_original_run
        SubprocessPlugin._original_shutil_which = saved_original_which
        _sp_mod._bigfoot_subprocess_run = saved_bigfoot_run
        _sp_mod._bigfoot_shutil_which = saved_bigfoot_which
        subprocess.run = saved_run
        shutil.which = saved_which  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# ConflictError: foreign Popen patch
# ---------------------------------------------------------------------------


# ESCAPE: test_conflict_error_popen_already_patched
#   CLAIM: If subprocess.Popen is replaced with a foreign object before activate(),
#          ConflictError is raised.
#   PATH:  activate -> _install_count == 0 -> _check_conflicts ->
#          subprocess.Popen is not _ORIGINAL_POPEN and not _FakePopen -> ConflictError.
#   CHECK: ConflictError raised; exc.target == "subprocess.Popen".
#   MUTATION: Not checking for foreign patchers silently allows conflict.
#   ESCAPE: Nothing reasonable -- exact exception type and target attribute.
def test_conflict_error_popen_already_patched() -> None:
    from unittest.mock import MagicMock

    from bigfoot._errors import ConflictError

    v, p = _make_verifier_with_plugin()
    foreign_patch = MagicMock()
    original = subprocess.Popen
    try:
        subprocess.Popen = foreign_patch  # type: ignore[misc]
        with pytest.raises(ConflictError) as exc_info:
            p.activate()
        assert exc_info.value.target == "subprocess.Popen"
    finally:
        subprocess.Popen = original  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Full session via module-level API: bigfoot.sandbox()
# ---------------------------------------------------------------------------


# ESCAPE: test_full_session_via_sandbox
#   CLAIM: A complete Popen session (spawn -> communicate) runs end-to-end through
#          the module-level bigfoot.sandbox() API, returning the scripted values.
#   PATH:  bigfoot.popen_mock.new_session() -> sandbox -> _FakePopen.__init__ -> communicate.
#   CHECK: stdout == b"build output"; stderr == b""; proc.returncode == 0.
#   MUTATION: Returning wrong stdout bytes would fail the equality check.
#   ESCAPE: Nothing reasonable -- exact bytes equality on all three fields.
def test_full_session_via_sandbox(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.popen_mock.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"build output", b"", 0))

    with bigfoot.sandbox():
        proc = subprocess.Popen(["make", "all"])
        stdout, stderr = proc.communicate()

    bigfoot.popen_mock.assert_spawn(command=["make", "all"], stdin=None)
    bigfoot.popen_mock.assert_communicate(input=None)

    assert stdout == b"build output"
    assert stderr == b""
    assert proc.returncode == 0
