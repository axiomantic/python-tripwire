"""Unit tests for bigfoot SubprocessPlugin."""

import shutil
import subprocess
from subprocess import TimeoutExpired
from unittest.mock import MagicMock

import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import (
    ConflictError,
    UnassertedInteractionsError,
    UnmockedInteractionError,
    UnusedMocksError,
)
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.subprocess import (
    _SHUTIL_WHICH_ORIGINAL,
    _SUBPROCESS_RUN_ORIGINAL,
    SubprocessPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, SubprocessPlugin]:
    """Return (verifier, plugin) with plugin registered but not activated."""
    v = StrictVerifier()
    p = SubprocessPlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with SubprocessPlugin._install_lock:
        SubprocessPlugin._install_count = 0
        if SubprocessPlugin._original_subprocess_run is not None:
            subprocess.run = SubprocessPlugin._original_subprocess_run
            SubprocessPlugin._original_subprocess_run = None
        if SubprocessPlugin._original_shutil_which is not None:
            shutil.which = SubprocessPlugin._original_shutil_which
            SubprocessPlugin._original_shutil_which = None
        # Reset module-level interceptor references
        import bigfoot.plugins.subprocess as _sp_mod

        _sp_mod._bigfoot_subprocess_run = None
        _sp_mod._bigfoot_shutil_which = None


@pytest.fixture(autouse=True)
def clean_install_count():
    """Ensure SubprocessPlugin install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield
    _reset_install_count()


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patches
#   CLAIM: After activate(), subprocess.run is replaced with bigfoot's interceptor.
#   PATH:  activate() -> _install_count == 0 -> _install_patches() -> subprocess.run = interceptor.
#   CHECK: subprocess.run is not _SUBPROCESS_RUN_ORIGINAL and shutil.which is not _SHUTIL_WHICH_ORIGINAL.
#   MUTATION: Skipping _install_patches() leaves originals in place; identity checks fail.
#   ESCAPE: Nothing reasonable -- identity comparison against import-time constants.
def test_activate_installs_patches() -> None:
    v, p = _make_verifier_with_plugin()
    assert subprocess.run is _SUBPROCESS_RUN_ORIGINAL
    assert shutil.which is _SHUTIL_WHICH_ORIGINAL
    p.activate()
    assert subprocess.run is not _SUBPROCESS_RUN_ORIGINAL
    assert shutil.which is not _SHUTIL_WHICH_ORIGINAL


# ESCAPE: test_deactivate_restores_patches
#   CLAIM: After activate() then deactivate(), subprocess.run and shutil.which are originals again.
#   PATH:  deactivate() -> _install_count reaches 0 -> _restore_patches() -> restore originals.
#   CHECK: Both functions restored to their import-time constants.
#   MUTATION: Not restoring in _restore_patches leaves bigfoot's interceptors in place.
#   ESCAPE: Nothing reasonable -- identity comparison against import-time constants.
def test_deactivate_restores_patches() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert subprocess.run is _SUBPROCESS_RUN_ORIGINAL
    assert shutil.which is _SHUTIL_WHICH_ORIGINAL


# ESCAPE: test_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patches are removed.
#   PATH:  First activate -> _install_count=1; second activate -> _install_count=2 (no reinstall).
#          First deactivate -> _install_count=1 (patches remain); second deactivate -> count=0 (restored).
#   CHECK: After first deactivate, subprocess.run is still patched.
#          After second deactivate, subprocess.run is back to original.
#   MUTATION: Restoring patches whenever count > 0 hits zero would restore too early.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove count-controlled restoration.
def test_reference_counting_nested() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert SubprocessPlugin._install_count == 2

    p.deactivate()
    assert SubprocessPlugin._install_count == 1
    # Patches must still be active after first deactivate
    assert subprocess.run is not _SUBPROCESS_RUN_ORIGINAL

    p.deactivate()
    assert SubprocessPlugin._install_count == 0
    # Originals must be restored after second deactivate
    assert subprocess.run is _SUBPROCESS_RUN_ORIGINAL


# ESCAPE: test_install_noop
#   CLAIM: install() can be called without raising.
#   PATH:  install() method body is intentionally empty.
#   CHECK: No exception raised.
#   MUTATION: Raising inside install() would fail this test.
#   ESCAPE: Nothing reasonable -- simply must not raise.
def test_install_noop() -> None:
    v, p = _make_verifier_with_plugin()
    p.install()  # Must not raise


# ---------------------------------------------------------------------------
# mock_run basic behavior
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_run_returns_completed_process
#   CLAIM: After mock_run registration, subprocess.run inside sandbox returns
#          CompletedProcess with the configured returncode, stdout, stderr, and args.
#   PATH:  sandbox -> activate -> interceptor -> _handle_run -> RunMockConfig popped
#          -> CompletedProcess returned.
#   CHECK: result.returncode == 0, result.stdout == "hello", result.stderr == "",
#          result.args == ["echo", "hello"].
#   MUTATION: Returning wrong returncode/stdout/args/stderr fails at least one assertion.
#   ESCAPE: A result with correct returncode but wrong stdout would pass returncode check
#           but fail stdout check.
def test_mock_run_returns_completed_process() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_run(["echo", "hello"], returncode=0, stdout="hello", stderr="")

    with v.sandbox():
        result = subprocess.run(["echo", "hello"])

    assert result.returncode == 0
    assert result.stdout == "hello"
    assert result.stderr == ""
    assert result.args == ["echo", "hello"]

    # Assert the interaction was recorded; use verifier directly to avoid unasserted error
    v.assert_interaction(p.run, command=["echo", "hello"], returncode=0, stdout="hello", stderr="")


# ESCAPE: test_mock_run_fifo_order
#   CLAIM: Two mock_run calls are consumed in registration order.
#          First subprocess.run call returns first config; second returns second config.
#   PATH:  _handle_run pops from _run_queue front (FIFO deque).
#   CHECK: First result.args == ["git", "status"]; second result.args == ["git", "log"].
#   MUTATION: Reversing order (LIFO) would fail because first result.args would be wrong.
#   ESCAPE: Nothing reasonable -- both args checks assert exact list equality.
def test_mock_run_fifo_order() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_run(["git", "status"], returncode=0, stdout="nothing to commit")
    p.mock_run(["git", "log"], returncode=0, stdout="abc123")

    with v.sandbox():
        result1 = subprocess.run(["git", "status"])
        result2 = subprocess.run(["git", "log"])

    assert result1.args == ["git", "status"]
    assert result1.returncode == 0
    assert result1.stdout == "nothing to commit"
    assert result2.args == ["git", "log"]
    assert result2.returncode == 0
    assert result2.stdout == "abc123"

    v.assert_interaction(p.run, command=["git", "status"], returncode=0, stdout="nothing to commit", stderr="")
    v.assert_interaction(p.run, command=["git", "log"], returncode=0, stdout="abc123", stderr="")


# ESCAPE: test_mock_run_command_mismatch_raises
#   CLAIM: If the actual command does not match the registered mock's command,
#          UnmockedInteractionError is raised immediately.
#   PATH:  _handle_run -> cmd_list != config.command -> UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised with source_id == "subprocess:run".
#   MUTATION: Returning a dummy response instead of raising hides mismatches.
#   ESCAPE: Test checks exception type AND source_id attribute.
def test_mock_run_command_mismatch_raises() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_run(["git", "status"], returncode=0)

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            subprocess.run(["git", "log"])

    assert exc_info.value.source_id == "subprocess:run"


# ESCAPE: test_mock_run_empty_queue_raises
#   CLAIM: subprocess.run inside sandbox with no registered mocks raises UnmockedInteractionError.
#   PATH:  _handle_run -> _run_queue is empty -> UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised with source_id == "subprocess:run".
#   MUTATION: Returning a blank CompletedProcess instead of raising hides empty queue.
#   ESCAPE: Nothing reasonable -- exact exception type and source_id.
def test_mock_run_empty_queue_raises() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            subprocess.run(["ls"])

    assert exc_info.value.source_id == "subprocess:run"


# ESCAPE: test_mock_run_raises_exception
#   CLAIM: When raises=TimeoutExpired is configured, the interceptor re-raises it
#          after recording the interaction.
#   PATH:  _handle_run -> record(interaction) -> config.raises is not None -> raise config.raises.
#   CHECK: TimeoutExpired is raised; the interaction is still on the timeline (recorded before raise).
#   MUTATION: Not re-raising would let the call silently succeed.
#   ESCAPE: Raising BEFORE recording would pass the exception check but fail the timeline check.
def test_mock_run_raises_exception() -> None:
    v, p = _make_verifier_with_plugin()
    timeout_error = TimeoutExpired(cmd=["sleep", "99"], timeout=5)
    p.mock_run(["sleep", "99"], raises=timeout_error, required=False)

    with v.sandbox():
        with pytest.raises(TimeoutExpired):
            subprocess.run(["sleep", "99"])

    # Interaction should still be recorded even though an exception was raised
    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "subprocess:run"
    assert interactions[0].details == {
        "command": ["sleep", "99"],
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }


# ---------------------------------------------------------------------------
# mock_run with BigFoot sandbox (module-level API)
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_run_in_sandbox
#   CLAIM: Using bigfoot.sandbox() context manager with mock_run registered before;
#          assert_interaction passes after sandbox exits.
#   PATH:  bigfoot.sandbox() -> SandboxContext using _current_test_verifier -> activate;
#          interceptor uses verifier from ContextVar -> assert_interaction checks timeline.
#   CHECK: assert_interaction does not raise; result has correct fields.
#   MUTATION: Recording interaction with wrong command would cause assert_interaction to raise.
#   ESCAPE: Nothing reasonable -- assert_interaction is the definitive check here.
def test_mock_run_in_sandbox(bigfoot_verifier: StrictVerifier) -> None:
    bigfoot.subprocess_mock.mock_run(["make", "build"], returncode=0, stdout="ok")

    with bigfoot.sandbox():
        result = subprocess.run(["make", "build"])

    assert result.returncode == 0
    assert result.stdout == "ok"
    assert result.stderr == ""
    assert result.args == ["make", "build"]

    bigfoot.assert_interaction(bigfoot.subprocess_mock.run, command=["make", "build"], returncode=0, stdout="ok", stderr="")


# ESCAPE: test_unregistered_run_in_sandbox_raises
#   CLAIM: subprocess.run inside bigfoot.sandbox() with no mock raises UnmockedInteractionError.
#   PATH:  interceptor -> _handle_run -> empty queue -> UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised; source_id == "subprocess:run".
#   MUTATION: Returning a default response silently lets unmocked calls through.
#   ESCAPE: Nothing reasonable -- exact exception type and source_id.
def test_unregistered_run_in_sandbox_raises(bigfoot_verifier: StrictVerifier) -> None:
    # Access subprocess_mock to ensure SubprocessPlugin is created and registered
    bigfoot.subprocess_mock.install()

    with bigfoot.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            subprocess.run(["cargo", "build"])

    assert exc_info.value.source_id == "subprocess:run"


# ESCAPE: test_unused_required_run_raises
#   CLAIM: Exiting the sandbox with an unconsumed required mock raises UnusedMocksError
#          when verify_all() is called.
#   PATH:  sandbox.__exit__ -> deactivate; verify_all() -> get_unused_mocks() ->
#          required mock still in _run_queue -> UnusedMocksError.
#   CHECK: UnusedMocksError raised; the mock details include the expected command.
#   MUTATION: Not appending to unused in get_unused_mocks() when required=True lets it pass.
#   ESCAPE: A mock with required=False would not be reported; test verifies required=True is caught.
def test_unused_required_run_raises() -> None:
    # Use a standalone verifier (not the autouse one) so we control the full lifecycle.
    v, p = _make_verifier_with_plugin()
    p.mock_run(["pip", "install", "foo"], returncode=0, required=True)

    with v.sandbox():
        pass  # subprocess.run never called

    with pytest.raises(UnusedMocksError) as exc_info:
        v.verify_all()

    assert len(exc_info.value.mocks) == 1
    # The mock tuple is (source_id, details, registration_traceback)
    source_id, details, _tb = exc_info.value.mocks[0]
    assert source_id == "subprocess:run"
    assert details == {"command": ["pip", "install", "foo"]}


# ---------------------------------------------------------------------------
# mock_which behavior
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_which_registered_returns_path
#   CLAIM: After mock_which("git", returns="/usr/bin/git"), shutil.which("git")
#          returns "/usr/bin/git" inside the sandbox.
#   PATH:  _handle_which -> name in _which_mocks -> record interaction -> return config.returns.
#   CHECK: result == "/usr/bin/git".
#   MUTATION: Returning None instead of the configured path fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_mock_which_registered_returns_path() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_which("git", returns="/usr/bin/git")

    with v.sandbox():
        result = shutil.which("git")

    assert result == "/usr/bin/git"

    v.assert_interaction(p.which, name="git", returns="/usr/bin/git")


# ESCAPE: test_mock_which_unregistered_returns_none
#   CLAIM: shutil.which("unknown_binary") returns None silently when unregistered.
#   PATH:  _handle_which -> name not in _which_mocks -> return None (no recording).
#   CHECK: result is None; no interactions in timeline.
#   MUTATION: Raising UnmockedInteractionError for unregistered names would break this.
#   ESCAPE: Returning empty string instead of None would pass None check incorrectly --
#           but the assertion uses `is None` which distinguishes.
def test_mock_which_unregistered_returns_none() -> None:
    v, p = _make_verifier_with_plugin()
    # No mock registered for "unknown_binary"

    with v.sandbox():
        result = shutil.which("unknown_binary")

    assert result is None
    # No interactions recorded for unregistered names
    assert v._timeline.all_unasserted() == []


# ESCAPE: test_mock_which_registered_none_returns_none
#   CLAIM: mock_which("notfound", returns=None) causes shutil.which("notfound") to return None.
#          This is distinct from unregistered: the interaction IS recorded.
#   PATH:  _handle_which -> name in _which_mocks -> record interaction -> return None.
#   CHECK: result is None; one interaction recorded with name="notfound" and returns=None.
#   MUTATION: Not recording interaction for returns=None mocks would fail the timeline check.
#   ESCAPE: Treating registered-None and unregistered identically would pass result check
#           but fail timeline length and detail checks.
def test_mock_which_registered_none_returns_none() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_which("notfound", returns=None)

    with v.sandbox():
        result = shutil.which("notfound")

    assert result is None

    # Interaction IS recorded for registered names (even with returns=None)
    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "subprocess:which"
    assert interactions[0].details == {"name": "notfound", "returns": None}


# ---------------------------------------------------------------------------
# Timeline and assertions
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_interaction_run
#   CLAIM: After sandbox with mock_run, assert_interaction(subprocess_mock.run, command=...)
#          passes without raising.
#   PATH:  assert_interaction -> verifier._timeline.peek_next_unasserted ->
#          matches source_id + command -> mark asserted.
#   CHECK: No exception raised.
#   MUTATION: Not recording the interaction would cause assert_interaction to raise
#             InteractionMismatchError.
#   ESCAPE: Recording with wrong command would cause field match to fail.
def test_assert_interaction_run(bigfoot_verifier: StrictVerifier) -> None:
    bigfoot.subprocess_mock.mock_run(["pytest", "--tb=short"], returncode=0, stdout="passed")

    with bigfoot.sandbox():
        subprocess.run(["pytest", "--tb=short"])

    # Must not raise
    bigfoot.assert_interaction(bigfoot.subprocess_mock.run, command=["pytest", "--tb=short"], returncode=0, stdout="passed", stderr="")


# ESCAPE: test_assert_interaction_which
#   CLAIM: After sandbox with mock_which, assert_interaction(subprocess_mock.which, name=...)
#          passes without raising.
#   PATH:  assert_interaction -> matches source_id "subprocess:which" + name field.
#   CHECK: No exception raised.
#   MUTATION: Recording interaction with wrong name would cause field mismatch.
#   ESCAPE: Recording source_id as "subprocess:run" instead would fail source_id match.
def test_assert_interaction_which(bigfoot_verifier: StrictVerifier) -> None:
    bigfoot.subprocess_mock.mock_which("python3", returns="/usr/bin/python3")

    with bigfoot.sandbox():
        shutil.which("python3")

    # Must not raise
    bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="python3", returns="/usr/bin/python3")


# ---------------------------------------------------------------------------
# ConflictError detection
# ---------------------------------------------------------------------------


# ESCAPE: test_conflict_error_subprocess_run_already_patched
#   CLAIM: If subprocess.run is replaced with a MagicMock before bigfoot.sandbox(),
#          ConflictError is raised.
#   PATH:  sandbox -> activate -> _check_conflicts -> subprocess.run is not original
#          and not bigfoot's -> ConflictError.
#   CHECK: ConflictError raised (wrapped in BaseExceptionGroup from SandboxContext._enter).
#   MUTATION: Not checking for foreign patchers in _check_conflicts silently allows conflict.
#   ESCAPE: Nothing reasonable -- ConflictError is the definitive signal.
def test_conflict_error_subprocess_run_already_patched() -> None:
    v, p = _make_verifier_with_plugin()
    foreign_patch = MagicMock()
    original = subprocess.run
    try:
        subprocess.run = foreign_patch  # type: ignore[assignment]
        with pytest.raises(ConflictError):
            p._check_conflicts()
    finally:
        subprocess.run = original  # type: ignore[assignment]


# ESCAPE: test_conflict_error_shutil_which_already_patched
#   CLAIM: If shutil.which is replaced with a MagicMock before sandbox activation,
#          ConflictError is raised.
#   PATH:  _check_conflicts -> shutil.which is not original and not bigfoot's -> ConflictError.
#   CHECK: ConflictError raised.
#   MUTATION: Not checking shutil.which lets the conflict through silently.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_conflict_error_shutil_which_already_patched() -> None:
    v, p = _make_verifier_with_plugin()
    foreign_patch = MagicMock()
    original = shutil.which
    try:
        shutil.which = foreign_patch  # type: ignore[assignment]
        with pytest.raises(ConflictError):
            p._check_conflicts()
    finally:
        shutil.which = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Singleton behavior
# ---------------------------------------------------------------------------


# ESCAPE: test_subprocess_mock_proxy_raises_outside_sandbox
#   CLAIM: Accessing bigfoot.subprocess_mock.mock_run outside a pytest test context
#          raises NoActiveVerifierError (because _current_test_verifier is not set).
#   PATH:  _SubprocessProxy.__getattr__ -> _get_test_verifier_or_raise -> NoActiveVerifierError.
#   CHECK: NoActiveVerifierError (or subclass) raised when ContextVar is explicitly cleared.
#   MUTATION: Returning a dummy plugin instead of raising would hide context failures.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_subprocess_mock_proxy_raises_outside_sandbox() -> None:
    # The autouse _bigfoot_auto_verifier fixture sets _current_test_verifier.
    # We explicitly clear it to simulate "outside any test context".
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.subprocess_mock.mock_run
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# assertable_fields
# ---------------------------------------------------------------------------


# ESCAPE: test_assertable_fields_run
#   CLAIM: SubprocessPlugin.assertable_fields(interaction) returns all four run fields
#          when interaction.source_id == "subprocess:run".
#   PATH:  assertable_fields checks source_id == _SOURCE_RUN -> return frozenset({"command", "returncode", "stdout", "stderr"}).
#   CHECK: result == frozenset({"command", "returncode", "stdout", "stderr"}).
#   MUTATION: Returning only frozenset({"command"}) skips completeness enforcement on returncode/stdout/stderr.
#   ESCAPE: frozenset({"command"}) would fail the equality check.
def test_assertable_fields_run() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(source_id="subprocess:run", sequence=0, details={}, plugin=p)
    result = p.assertable_fields(interaction)
    assert result == frozenset({"command", "returncode", "stdout", "stderr"})


# ESCAPE: test_assertable_fields_which
#   CLAIM: SubprocessPlugin.assertable_fields(interaction) returns frozenset({"name", "returns"})
#          when interaction.source_id == "subprocess:which".
#   PATH:  assertable_fields checks source_id == _SOURCE_WHICH -> return frozenset({"name", "returns"}).
#   CHECK: result == frozenset({"name", "returns"}).
#   MUTATION: Returning frozenset({"name"}) skips completeness enforcement on the returns field.
#   ESCAPE: frozenset() (empty) would also fail the equality check.
def test_assertable_fields_which() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(source_id="subprocess:which", sequence=0, details={}, plugin=p)
    result = p.assertable_fields(interaction)
    assert result == frozenset({"name", "returns"})


# ESCAPE: test_assertable_fields_unknown_source
#   CLAIM: assertable_fields(interaction) returns frozenset() (empty) when source_id is unknown.
#   PATH:  assertable_fields -> none of the if-branches match -> return frozenset().
#   CHECK: result == frozenset().
#   MUTATION: Raising for unknown source_id would break the empty-return contract.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_unknown_source() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(source_id="subprocess:unknown", sequence=0, details={}, plugin=p)
    result = p.assertable_fields(interaction)
    assert result == frozenset()


# ---------------------------------------------------------------------------
# format_assert_hint (C-2 path) and unused-which tracking (C-3 path)
# ---------------------------------------------------------------------------


# ESCAPE: test_unasserted_interaction_raises_correctly
#   CLAIM: If subprocess.run is called inside a sandbox and assert_interaction is never called,
#          verify_all() raises UnassertedInteractionsError.
#   PATH:  sandbox exit -> deactivate (no verify_all); explicit verify_all() ->
#          unasserted timeline entry found -> UnassertedInteractionsError.
#   CHECK: UnassertedInteractionsError raised; interactions list has exactly one entry.
#   MUTATION: Silently marking all interactions as asserted on deactivate would hide this.
#   ESCAPE: Nothing reasonable -- exact exception type and interaction count.
def test_unasserted_interaction_raises_correctly(clean_install_count) -> None:
    v = StrictVerifier()
    p = SubprocessPlugin(v)
    p.mock_run(["git", "status"], returncode=0, required=False)

    with v.sandbox():
        subprocess.run(["git", "status"], check=False)
        # Do NOT call assert_interaction

    # sandbox.__exit__ does not call verify_all; we must call it explicitly.
    with pytest.raises(UnassertedInteractionsError) as exc_info:
        v.verify_all()

    assert len(exc_info.value.interactions) == 1
    assert exc_info.value.interactions[0].source_id == "subprocess:run"


# ESCAPE: test_unused_required_which_raises
#   CLAIM: Exiting the sandbox with a required which-mock that was never triggered raises
#          UnusedMocksError when verify_all() is called.
#   PATH:  sandbox exit -> deactivate; verify_all() -> get_unused_mocks() ->
#          name not in _which_called, required=True -> UnusedMocksError.
#   CHECK: UnusedMocksError raised; mock details include name="git".
#   MUTATION: Not tracking _which_called would report called mocks as unused too.
#   ESCAPE: A mock with required=False would not be reported; test uses required=True.
def test_unused_required_which_raises(clean_install_count) -> None:
    v = StrictVerifier()
    p = SubprocessPlugin(v)
    p.mock_which("git", returns="/usr/bin/git", required=True)

    with v.sandbox():
        pass  # shutil.which("git") never called

    with pytest.raises(UnusedMocksError) as exc_info:
        v.verify_all()

    assert len(exc_info.value.mocks) == 1
    source_id, details, _tb = exc_info.value.mocks[0]
    assert source_id == "subprocess:which"
    assert details == {"name": "git"}


# ESCAPE: test_called_required_which_does_not_raise
#   CLAIM: A required which-mock that IS called and asserted does not trigger UnusedMocksError.
#   PATH:  _handle_which -> name added to _which_called; get_unused_mocks() ->
#          config.name in _which_called -> not appended to unused list.
#   CHECK: verify_all() does not raise.
#   MUTATION: Not adding to _which_called would cause get_unused_mocks to report it as unused.
#   ESCAPE: Nothing reasonable -- if UnusedMocksError is raised the test fails.
def test_called_required_which_does_not_raise(clean_install_count) -> None:
    v = StrictVerifier()
    p = SubprocessPlugin(v)
    p.mock_which("git", returns="/usr/bin/git", required=True)

    with v.sandbox():
        result = shutil.which("git")

    assert result == "/usr/bin/git"

    # Assert the interaction to avoid UnassertedInteractionsError
    v.assert_interaction(p.which, name="git", returns="/usr/bin/git")

    # verify_all() must not raise: the mock was called and asserted
    v.verify_all()
