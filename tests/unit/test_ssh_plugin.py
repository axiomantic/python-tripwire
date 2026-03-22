"""Unit tests for SshPlugin."""

from __future__ import annotations

import paramiko
import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import InvalidStateError, UnmockedInteractionError
from bigfoot._state_machine_plugin import ScriptStep
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.ssh_plugin import (
    _PARAMIKO_AVAILABLE,
    SshPlugin,
    _FakeSFTPClient,
    _FakeSSHClient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, SshPlugin]:
    """Return (verifier, plugin) with plugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    SshPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, SshPlugin):
            return v, p
    p = SshPlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore paramiko if leaked."""
    with SshPlugin._install_lock:
        SshPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        SshPlugin.__new__(SshPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_install_count() -> None:
    """Ensure SshPlugin install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield  # type: ignore[misc]
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
        "exec_command": {"connected": "connected"},
        "open_sftp": {"connected": "connected"},
        "sftp_get": {"connected": "connected"},
        "sftp_put": {"connected": "connected"},
        "sftp_listdir": {"connected": "connected"},
        "sftp_stat": {"connected": "connected"},
        "sftp_mkdir": {"connected": "connected"},
        "sftp_remove": {"connected": "connected"},
        "close": {"connected": "closed"},
    }


# ESCAPE: test_unmocked_source_id
#   CLAIM: _unmocked_source_id() returns "ssh:connect".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "ssh:connect".
#   MUTATION: Returning a different string fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_unmocked_source_id() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._unmocked_source_id() == "ssh:connect"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patch
#   CLAIM: After activate(), paramiko.SSHClient is replaced with _FakeSSHClient.
#   PATH:  activate() -> _install_count == 0 -> store original -> install _FakeSSHClient.
#   CHECK: paramiko.SSHClient is _FakeSSHClient.
#   MUTATION: Skipping patch installation leaves original in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison against _FakeSSHClient class.
def test_activate_installs_patch() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert paramiko.SSHClient is _FakeSSHClient


# ESCAPE: test_deactivate_restores_patch
#   CLAIM: After activate() then deactivate(), paramiko.SSHClient is the original again.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore original.
#   CHECK: paramiko.SSHClient is NOT _FakeSSHClient.
#   MUTATION: Not restoring in deactivate() leaves _FakeSSHClient in place.
#   ESCAPE: Nothing reasonable -- identity comparison against original class.
def test_deactivate_restores_patch() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert paramiko.SSHClient is not _FakeSSHClient


# ESCAPE: test_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patch is removed.
#   PATH:  First activate -> _install_count=1; second activate -> _install_count=2.
#          First deactivate -> _install_count=1 (patch remains).
#          Second deactivate -> _install_count=0 (original restored).
#   CHECK: After first deactivate, paramiko.SSHClient is still _FakeSSHClient.
#          After second deactivate, paramiko.SSHClient is not _FakeSSHClient.
#   MUTATION: Restoring on first deactivate would fail the mid-point identity check.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove count-controlled restoration.
def test_reference_counting_nested() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert SshPlugin._install_count == 2

    p.deactivate()
    assert SshPlugin._install_count == 1
    assert paramiko.SSHClient is _FakeSSHClient

    p.deactivate()
    assert SshPlugin._install_count == 0
    assert paramiko.SSHClient is not _FakeSSHClient


# ---------------------------------------------------------------------------
# 1. Basic interception: connect, exec_command, open_sftp, sftp ops, close
# ---------------------------------------------------------------------------


# ESCAPE: test_full_exec_command_flow
#   CLAIM: A complete SSH flow (connect -> exec_command -> close) consumes
#          steps in order, returns scripted values, and ends in "closed" state.
#   PATH:  sandbox -> activate -> _FakeSSHClient.connect triggers connect step
#          (state: connected); exec_command -> state: connected (self-loop);
#          close() -> state: closed.
#   CHECK: exec_command returns the scripted tuple; close returns None;
#          session released after close.
#   MUTATION: Wrong return value for any step fails the exact equality check.
#   ESCAPE: Nothing reasonable -- exact equality on all returns.
def test_full_exec_command_flow() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("exec_command", returns=("stdin", "stdout", "stderr"))
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost.example.com", port=22, username="deploy")
        result = client.exec_command("ls -la")
        client.close()

    assert result == ("stdin", "stdout", "stderr")
    assert len(p._active_sessions) == 0

    v.assert_interaction(
        p.connect, hostname="myhost.example.com", port=22, username="deploy", auth_method="password"
    )
    v.assert_interaction(p.exec_command, command="ls -la")
    v.assert_interaction(p.close)


# ESCAPE: test_sftp_flow
#   CLAIM: A complete SFTP flow (connect -> open_sftp -> sftp_get -> sftp_put -> close)
#          works correctly through fake classes.
#   PATH:  connect -> open_sftp (returns _FakeSFTPClient) -> sftp_get -> sftp_put -> close.
#   CHECK: open_sftp returns _FakeSFTPClient; get/put return None; close succeeds.
#   MUTATION: Wrong return type from open_sftp would fail isinstance check.
#   ESCAPE: Nothing reasonable -- identity and equality checks at each step.
def test_sftp_flow() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_get", returns=None)
    session.expect("sftp_put", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost.example.com", port=22, username="deploy")
        sftp = client.open_sftp()
        assert isinstance(sftp, _FakeSFTPClient)
        sftp.get("/remote/file.txt", "/local/file.txt")
        sftp.put("/local/upload.txt", "/remote/upload.txt")
        client.close()

    assert len(p._active_sessions) == 0

    v.assert_interaction(
        p.connect, hostname="myhost.example.com", port=22, username="deploy", auth_method="password"
    )
    v.assert_interaction(p.open_sftp)
    v.assert_interaction(p.sftp_get, remotepath="/remote/file.txt", localpath="/local/file.txt")
    v.assert_interaction(p.sftp_put, localpath="/local/upload.txt", remotepath="/remote/upload.txt")
    v.assert_interaction(p.close)


# ESCAPE: test_sftp_listdir_stat_mkdir_remove_flow
#   CLAIM: SFTP directory operations (listdir, stat, mkdir, remove) work as self-transitions.
#   PATH:  connect -> open_sftp -> sftp_listdir -> sftp_stat -> sftp_mkdir -> sftp_remove -> close.
#   CHECK: Each operation returns scripted values; all complete without error.
#   MUTATION: Missing any of these in transitions would raise InvalidStateError.
#   ESCAPE: Nothing reasonable -- exact equality on all returns.
def test_sftp_listdir_stat_mkdir_remove_flow() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_listdir", returns=["file1.txt", "file2.txt"])
    session.expect("sftp_stat", returns="fake_stat_result")
    session.expect("sftp_mkdir", returns=None)
    session.expect("sftp_remove", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("server.example.com", port=22, username="admin")
        sftp = client.open_sftp()
        listing = sftp.listdir("/remote/dir")
        stat_result = sftp.stat("/remote/file.txt")
        sftp.mkdir("/remote/newdir")
        sftp.remove("/remote/oldfile.txt")
        client.close()

    assert listing == ["file1.txt", "file2.txt"]
    assert stat_result == "fake_stat_result"
    assert len(p._active_sessions) == 0

    v.assert_interaction(
        p.connect, hostname="server.example.com", port=22, username="admin", auth_method="password"
    )
    v.assert_interaction(p.open_sftp)
    v.assert_interaction(p.sftp_listdir, path="/remote/dir")
    v.assert_interaction(p.sftp_stat, path="/remote/file.txt")
    v.assert_interaction(p.sftp_mkdir, path="/remote/newdir")
    v.assert_interaction(p.sftp_remove, path="/remote/oldfile.txt")
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# 2. Full assertion certainty (assertable_fields)
# ---------------------------------------------------------------------------


# ESCAPE: test_assertable_fields_connect
#   CLAIM: assertable_fields for a "ssh:connect" interaction returns
#          {"hostname", "port", "username", "auth_method"}.
#   PATH:  Record a connect interaction, call assertable_fields.
#   CHECK: result == frozenset({"hostname", "port", "username", "auth_method"}).
#   MUTATION: Returning empty frozenset or missing a field fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_connect() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        client.close()

    interactions = v._timeline._interactions
    connect_interaction = [i for i in interactions if i.source_id == "ssh:connect"][0]
    assert p.assertable_fields(connect_interaction) == frozenset(
        {"hostname", "port", "username", "auth_method"}
    )


# ESCAPE: test_assertable_fields_exec_command
#   CLAIM: assertable_fields for a "ssh:exec_command" interaction returns {"command"}.
#   PATH:  Record an exec_command interaction, call assertable_fields.
#   CHECK: result == frozenset({"command"}).
#   MUTATION: Missing command fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_exec_command() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("exec_command", returns=("stdin", "stdout", "stderr"))
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        client.exec_command("ls -la")
        client.close()

    interactions = v._timeline._interactions
    exec_interaction = [i for i in interactions if i.source_id == "ssh:exec_command"][0]
    assert p.assertable_fields(exec_interaction) == frozenset({"command"})


# ESCAPE: test_assertable_fields_open_sftp
#   CLAIM: assertable_fields for a "ssh:open_sftp" interaction returns frozenset()
#          because open_sftp is a state-transition-only step with no data fields.
#   PATH:  Record an open_sftp interaction, call assertable_fields.
#   CHECK: result == frozenset().
#   MUTATION: Returning non-empty frozenset fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_open_sftp() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        client.open_sftp()
        client.close()

    interactions = v._timeline._interactions
    sftp_interaction = [i for i in interactions if i.source_id == "ssh:open_sftp"][0]
    assert p.assertable_fields(sftp_interaction) == frozenset()


# ESCAPE: test_assertable_fields_sftp_get
#   CLAIM: assertable_fields for a "ssh:sftp_get" interaction returns
#          {"remotepath", "localpath"}.
#   PATH:  Record a sftp_get interaction, call assertable_fields.
#   CHECK: result == frozenset({"remotepath", "localpath"}).
#   MUTATION: Missing a field fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_sftp_get() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_get", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        sftp = client.open_sftp()
        sftp.get("/remote/file.txt", "/local/file.txt")
        client.close()

    interactions = v._timeline._interactions
    get_interaction = [i for i in interactions if i.source_id == "ssh:sftp_get"][0]
    assert p.assertable_fields(get_interaction) == frozenset({"remotepath", "localpath"})


# ESCAPE: test_assertable_fields_sftp_put
#   CLAIM: assertable_fields for a "ssh:sftp_put" interaction returns
#          {"localpath", "remotepath"}.
#   PATH:  Record a sftp_put interaction, call assertable_fields.
#   CHECK: result == frozenset({"localpath", "remotepath"}).
#   MUTATION: Missing a field fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_sftp_put() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_put", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        sftp = client.open_sftp()
        sftp.put("/local/file.txt", "/remote/file.txt")
        client.close()

    interactions = v._timeline._interactions
    put_interaction = [i for i in interactions if i.source_id == "ssh:sftp_put"][0]
    assert p.assertable_fields(put_interaction) == frozenset({"localpath", "remotepath"})


# ESCAPE: test_assertable_fields_sftp_listdir
#   CLAIM: assertable_fields for a "ssh:sftp_listdir" interaction returns {"path"}.
#   PATH:  Record a sftp_listdir interaction, call assertable_fields.
#   CHECK: result == frozenset({"path"}).
#   MUTATION: Missing path fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_sftp_listdir() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_listdir", returns=["a.txt"])
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        sftp = client.open_sftp()
        sftp.listdir("/remote/dir")
        client.close()

    interactions = v._timeline._interactions
    listdir_interaction = [i for i in interactions if i.source_id == "ssh:sftp_listdir"][0]
    assert p.assertable_fields(listdir_interaction) == frozenset({"path"})


# ESCAPE: test_assertable_fields_sftp_stat
#   CLAIM: assertable_fields for a "ssh:sftp_stat" interaction returns {"path"}.
#   PATH:  Record a sftp_stat interaction, call assertable_fields.
#   CHECK: result == frozenset({"path"}).
#   MUTATION: Missing path fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_sftp_stat() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_stat", returns="stat_result")
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        sftp = client.open_sftp()
        sftp.stat("/remote/file.txt")
        client.close()

    interactions = v._timeline._interactions
    stat_interaction = [i for i in interactions if i.source_id == "ssh:sftp_stat"][0]
    assert p.assertable_fields(stat_interaction) == frozenset({"path"})


# ESCAPE: test_assertable_fields_sftp_mkdir
#   CLAIM: assertable_fields for a "ssh:sftp_mkdir" interaction returns {"path"}.
#   PATH:  Record a sftp_mkdir interaction, call assertable_fields.
#   CHECK: result == frozenset({"path"}).
#   MUTATION: Missing path fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_sftp_mkdir() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_mkdir", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        sftp = client.open_sftp()
        sftp.mkdir("/remote/newdir")
        client.close()

    interactions = v._timeline._interactions
    mkdir_interaction = [i for i in interactions if i.source_id == "ssh:sftp_mkdir"][0]
    assert p.assertable_fields(mkdir_interaction) == frozenset({"path"})


# ESCAPE: test_assertable_fields_sftp_remove
#   CLAIM: assertable_fields for a "ssh:sftp_remove" interaction returns {"path"}.
#   PATH:  Record a sftp_remove interaction, call assertable_fields.
#   CHECK: result == frozenset({"path"}).
#   MUTATION: Missing path fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_sftp_remove() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_remove", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        sftp = client.open_sftp()
        sftp.remove("/remote/file.txt")
        client.close()

    interactions = v._timeline._interactions
    remove_interaction = [i for i in interactions if i.source_id == "ssh:sftp_remove"][0]
    assert p.assertable_fields(remove_interaction) == frozenset({"path"})


# ESCAPE: test_assertable_fields_close
#   CLAIM: assertable_fields for a "ssh:close" interaction returns frozenset()
#          because close is a state-transition-only step with no data fields.
#   PATH:  Record a close interaction, call assertable_fields.
#   CHECK: result == frozenset().
#   MUTATION: Returning non-empty frozenset fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_close() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        client.close()

    interactions = v._timeline._interactions
    close_interaction = [i for i in interactions if i.source_id == "ssh:close"][0]
    assert p.assertable_fields(close_interaction) == frozenset()


# ---------------------------------------------------------------------------
# 3. Unmocked interaction error
# ---------------------------------------------------------------------------


# ESCAPE: test_connection_with_empty_queue_raises_unmocked
#   CLAIM: If no session is queued when paramiko.SSHClient().connect() fires,
#          UnmockedInteractionError is raised with source_id == "ssh:connect".
#   PATH:  _FakeSSHClient.connect() -> _bind_connection -> queue empty ->
#          UnmockedInteractionError(source_id="ssh:connect").
#   CHECK: UnmockedInteractionError raised; exc.source_id == "ssh:connect".
#   MUTATION: Returning a dummy session for empty queue would not raise.
#   ESCAPE: Raising with wrong source_id fails the source_id check.
def test_connection_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_verifier_with_plugin()
    # No session registered

    with v.sandbox():
        client = paramiko.SSHClient()
        with pytest.raises(UnmockedInteractionError) as exc_info:
            client.connect("myhost", port=22, username="user")

    assert exc_info.value.source_id == "ssh:connect"


# ---------------------------------------------------------------------------
# 4. Unused mock warning
# ---------------------------------------------------------------------------


# ESCAPE: test_get_unused_mocks_unconsumed_steps
#   CLAIM: When exec_command and close steps are never consumed, get_unused_mocks() returns them.
#   PATH:  new_session with connect + exec_command + close steps -> connect consumed in
#          _FakeSSHClient.connect() -> session in _active_sessions with two remaining required
#          steps -> get_unused_mocks() returns them.
#   CHECK: len(unused) == 2; unused[0].method == "exec_command"; unused[1].method == "close".
#   MUTATION: Not scanning _active_sessions for remaining steps would return [].
#   ESCAPE: Returning all three including connect would give len == 3; fails count check.
def test_get_unused_mocks_unconsumed_steps() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("exec_command", returns=("stdin", "stdout", "stderr"))  # will NOT be consumed
    session.expect("close", returns=None)  # will NOT be consumed

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        # deliberately NOT calling exec_command or close

    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "exec_command"
    assert unused[1].method == "close"


# ESCAPE: test_get_unused_mocks_queued_session_never_bound
#   CLAIM: A session queued but never bound (no connect() called) has all
#          its required steps returned by get_unused_mocks().
#   PATH:  new_session with connect + exec_command enqueued -> no connect() call ->
#          _session_queue still holds handle -> get_unused_mocks() iterates _session_queue.
#   CHECK: len(unused) == 2; methods are ["connect", "exec_command"] in order.
#   MUTATION: Not iterating _session_queue would return [].
#   ESCAPE: Returning items in LIFO order would fail the method ordering check.
def test_get_unused_mocks_queued_session_never_bound() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("exec_command", returns=("stdin", "stdout", "stderr"))

    # Never call connect; the session stays in the queue
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "connect"
    assert unused[1].method == "exec_command"


# ---------------------------------------------------------------------------
# 5. Missing fields error (assert_interaction with wrong fields)
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_interaction_missing_fields_raises
#   CLAIM: Calling assert_interaction for a connect step with missing fields raises
#          MissingAssertionFieldsError.
#   PATH:  Record connect interaction with {hostname, port, username, auth_method} ->
#          assert_interaction with only hostname= -> MissingAssertionFieldsError.
#   CHECK: MissingAssertionFieldsError raised.
#   MUTATION: Returning frozenset() from assertable_fields would skip field validation.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_assert_interaction_missing_fields_raises() -> None:
    from bigfoot._errors import MissingAssertionFieldsError

    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        client.close()

    # Assert connect with only hostname -- missing port, username, auth_method
    with pytest.raises(MissingAssertionFieldsError):
        v.assert_interaction(p.connect, hostname="myhost")


# ---------------------------------------------------------------------------
# 6. Typed assertion helpers
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_connect_helper
#   CLAIM: assert_connect() typed helper correctly asserts a connect interaction.
#   PATH:  Record connect interaction -> assert_connect with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong hostname/port/username/auth_method would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_connect_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.ssh_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        client = paramiko.SSHClient()
        client.connect("server.example.com", port=22, username="deploy")
        client.close()

    bigfoot.ssh_mock.assert_connect(
        hostname="server.example.com", port=22, username="deploy", auth_method="password"
    )
    bigfoot.ssh_mock.assert_close()


# ESCAPE: test_assert_exec_command_helper
#   CLAIM: assert_exec_command() typed helper correctly asserts an exec_command interaction.
#   PATH:  Record exec_command interaction -> assert_exec_command with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong command would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_exec_command_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.ssh_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("exec_command", returns=("stdin", "stdout", "stderr"))
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        client = paramiko.SSHClient()
        client.connect("server.example.com", port=22, username="deploy")
        client.exec_command("uptime")
        client.close()

    bigfoot.ssh_mock.assert_connect(
        hostname="server.example.com", port=22, username="deploy", auth_method="password"
    )
    bigfoot.ssh_mock.assert_exec_command(command="uptime")
    bigfoot.ssh_mock.assert_close()


# ESCAPE: test_assert_sftp_get_helper
#   CLAIM: assert_sftp_get() typed helper correctly asserts a sftp_get interaction.
#   PATH:  Record sftp_get interaction -> assert_sftp_get with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong remotepath/localpath would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_sftp_get_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.ssh_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_get", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        client = paramiko.SSHClient()
        client.connect("server.example.com", port=22, username="deploy")
        sftp = client.open_sftp()
        sftp.get("/remote/data.csv", "/local/data.csv")
        client.close()

    bigfoot.ssh_mock.assert_connect(
        hostname="server.example.com", port=22, username="deploy", auth_method="password"
    )
    bigfoot.ssh_mock.assert_open_sftp()
    bigfoot.ssh_mock.assert_sftp_get(remotepath="/remote/data.csv", localpath="/local/data.csv")
    bigfoot.ssh_mock.assert_close()


# ESCAPE: test_assert_sftp_put_helper
#   CLAIM: assert_sftp_put() typed helper correctly asserts a sftp_put interaction.
#   PATH:  Record sftp_put interaction -> assert_sftp_put with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong localpath/remotepath would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_sftp_put_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.ssh_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_put", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        client = paramiko.SSHClient()
        client.connect("server.example.com", port=22, username="deploy")
        sftp = client.open_sftp()
        sftp.put("/local/upload.txt", "/remote/upload.txt")
        client.close()

    bigfoot.ssh_mock.assert_connect(
        hostname="server.example.com", port=22, username="deploy", auth_method="password"
    )
    bigfoot.ssh_mock.assert_open_sftp()
    bigfoot.ssh_mock.assert_sftp_put(localpath="/local/upload.txt", remotepath="/remote/upload.txt")
    bigfoot.ssh_mock.assert_close()


# ---------------------------------------------------------------------------
# Negative tests for typed assertion helpers (Fix 5 pattern)
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_interaction_connect_rejects_wrong_values
#   CLAIM: assert_connect() raises when passed wrong field values.
#   PATH:  Record connect with hostname="myhost" -> assert_connect with hostname="wrong" ->
#          InteractionMismatchError.
#   CHECK: Exception raised.
#   MUTATION: A no-op assert_connect that never checks would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_assert_interaction_connect_rejects_wrong_values() -> None:
    from bigfoot._errors import InteractionMismatchError

    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        client.close()

    with pytest.raises(InteractionMismatchError):
        v.assert_interaction(
            p.connect, hostname="wrong_host", port=22, username="user", auth_method="password"
        )


# ESCAPE: test_assert_interaction_exec_command_rejects_wrong_values
#   CLAIM: assert_exec_command() raises when passed wrong command.
#   PATH:  Record exec_command with command="ls" -> assert_exec_command with command="pwd" ->
#          InteractionMismatchError.
#   CHECK: Exception raised.
#   MUTATION: A no-op assert_exec_command that never checks would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_assert_interaction_exec_command_rejects_wrong_values() -> None:
    from bigfoot._errors import InteractionMismatchError

    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("exec_command", returns=("stdin", "stdout", "stderr"))
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        client.exec_command("ls")
        client.close()

    v.assert_interaction(
        p.connect, hostname="myhost", port=22, username="user", auth_method="password"
    )
    with pytest.raises(InteractionMismatchError):
        v.assert_interaction(p.exec_command, command="wrong_command")


# ESCAPE: test_assert_interaction_sftp_get_rejects_wrong_values
#   CLAIM: assert_sftp_get() raises when passed wrong remotepath.
#   PATH:  Record sftp_get with remotepath="/remote/file.txt" ->
#          assert_sftp_get with remotepath="/wrong/path" -> InteractionMismatchError.
#   CHECK: Exception raised.
#   MUTATION: A no-op assert_sftp_get that never checks would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_assert_interaction_sftp_get_rejects_wrong_values() -> None:
    from bigfoot._errors import InteractionMismatchError

    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_get", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        sftp = client.open_sftp()
        sftp.get("/remote/file.txt", "/local/file.txt")
        client.close()

    v.assert_interaction(
        p.connect, hostname="myhost", port=22, username="user", auth_method="password"
    )
    v.assert_interaction(p.open_sftp)
    with pytest.raises(InteractionMismatchError):
        v.assert_interaction(
            p.sftp_get, remotepath="/wrong/path", localpath="/local/file.txt"
        )


# ESCAPE: test_assert_interaction_sftp_put_rejects_wrong_values
#   CLAIM: assert_sftp_put() raises when passed wrong localpath.
#   PATH:  Record sftp_put with localpath="/local/file.txt" ->
#          assert_sftp_put with localpath="/wrong/path" -> InteractionMismatchError.
#   CHECK: Exception raised.
#   MUTATION: A no-op assert_sftp_put that never checks would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_assert_interaction_sftp_put_rejects_wrong_values() -> None:
    from bigfoot._errors import InteractionMismatchError

    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_put", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        sftp = client.open_sftp()
        sftp.put("/local/file.txt", "/remote/file.txt")
        client.close()

    v.assert_interaction(
        p.connect, hostname="myhost", port=22, username="user", auth_method="password"
    )
    v.assert_interaction(p.open_sftp)
    with pytest.raises(InteractionMismatchError):
        v.assert_interaction(
            p.sftp_put, localpath="/wrong/path", remotepath="/remote/file.txt"
        )


# ---------------------------------------------------------------------------
# 8. Exception propagation
# ---------------------------------------------------------------------------


# ESCAPE: test_exception_propagation
#   CLAIM: When a step has raises= set, that exception is propagated during execution.
#   PATH:  connect step -> exec_command step with raises=ConnectionError("ssh failed") ->
#          _execute_step raises ConnectionError.
#   CHECK: ConnectionError raised with exact message "ssh failed".
#   MUTATION: Not raising the exception would return the step.returns value instead.
#   ESCAPE: Raising a different exception type or message fails the assertion.
def test_exception_propagation() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect(
        "exec_command", returns=None, raises=ConnectionError("ssh failed")
    )

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        with pytest.raises(ConnectionError) as exc_info:
            client.exec_command("bad_command")

    assert str(exc_info.value) == "ssh failed"


# ---------------------------------------------------------------------------
# 9. Graceful degradation
# ---------------------------------------------------------------------------


# ESCAPE: test_paramiko_available_flag
#   CLAIM: _PARAMIKO_AVAILABLE is True when paramiko is installed.
#   PATH:  Module-level try/except import check.
#   CHECK: _PARAMIKO_AVAILABLE == True.
#   MUTATION: Not importing paramiko at module level would leave flag False.
#   ESCAPE: Nothing reasonable -- exact boolean equality.
def test_paramiko_available_flag() -> None:
    assert _PARAMIKO_AVAILABLE is True


# ESCAPE: test_ssh_mock_proxy_raises_import_error_when_unavailable
#   CLAIM: Accessing bigfoot.ssh_mock raises ImportError when paramiko is not installed.
#   PATH:  _SshProxy.__getattr__ -> checks _PARAMIKO_AVAILABLE -> raises ImportError.
#   CHECK: ImportError raised with exact expected message.
#   MUTATION: Not checking _PARAMIKO_AVAILABLE would defer the error.
#   ESCAPE: Wrong message would fail the string check.
def test_ssh_mock_proxy_raises_import_error_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bigfoot.plugins.ssh_plugin as ssh_mod

    monkeypatch.setattr(ssh_mod, "_PARAMIKO_AVAILABLE", False)

    with pytest.raises(ImportError) as exc_info:
        _ = bigfoot.ssh_mock.new_session  # noqa: B018

    assert str(exc_info.value) == (
        "bigfoot[ssh] is required to use bigfoot.ssh_mock. "
        "Install it with: pip install bigfoot[ssh]"
    )


# ---------------------------------------------------------------------------
# 10. State transition validation
# ---------------------------------------------------------------------------


# ESCAPE: test_exec_command_before_connect_raises_invalid_state
#   CLAIM: Calling exec_command when state is "disconnected" raises InvalidStateError.
#   PATH:  exec_command method not valid from "disconnected" -> InvalidStateError.
#   CHECK: InvalidStateError raised with correct method, current_state, valid_states.
#   MUTATION: Allowing exec_command from disconnected would not raise.
#   ESCAPE: Wrong valid_states fails the attribute check.
def test_exec_command_before_connect_raises_invalid_state() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    handle = session
    with pytest.raises(InvalidStateError) as exc_info:
        p._execute_step(
            handle, "exec_command", (), {}, "ssh:exec_command",
            details={"command": "ls"},
        )

    exc = exc_info.value
    assert exc.source_id == "ssh:exec_command"
    assert exc.method == "exec_command"
    assert exc.current_state == "disconnected"
    assert exc.valid_states == frozenset({"connected"})


# ESCAPE: test_close_from_disconnected_raises_invalid_state
#   CLAIM: Calling close when state is "disconnected" raises InvalidStateError.
#   PATH:  close method not valid from "disconnected" -> InvalidStateError.
#   CHECK: InvalidStateError raised with correct method, current_state, valid_states.
#   MUTATION: Allowing close from disconnected would not raise.
#   ESCAPE: Wrong valid_states fails the attribute check.
def test_close_from_disconnected_raises_invalid_state() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    handle = session
    with pytest.raises(InvalidStateError) as exc_info:
        p._execute_step(
            handle, "close", (), {}, "ssh:close",
            details={},
        )

    exc = exc_info.value
    assert exc.source_id == "ssh:close"
    assert exc.method == "close"
    assert exc.current_state == "disconnected"
    assert exc.valid_states == frozenset({"connected"})


# ---------------------------------------------------------------------------
# 11. Session lifecycle
# ---------------------------------------------------------------------------


# ESCAPE: test_session_lifecycle
#   CLAIM: Full session lifecycle: new_session -> expect -> bind -> execute -> release
#          works correctly through the sandbox context manager.
#   PATH:  new_session creates SessionHandle -> expect chains configure script ->
#          sandbox activate installs patch -> SSHClient.connect binds session ->
#          operations execute steps -> close releases session -> deactivate restores.
#   CHECK: All scripted returns match; active_sessions empty after close;
#          patch restored after sandbox exit.
#   MUTATION: Missing any lifecycle step would leave sessions dangling or patch installed.
#   ESCAPE: Nothing reasonable -- multiple exact equality checks at each stage.
def test_session_lifecycle() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("exec_command", returns=("stdin", "stdout", "stderr"))
    session.expect("close", returns=None)

    # Before sandbox: session is queued
    assert len(p._session_queue) == 1

    with v.sandbox():
        # After activate: patch installed
        assert paramiko.SSHClient is _FakeSSHClient

        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")

        # After connect: session bound, queue empty
        assert len(p._session_queue) == 0
        assert len(p._active_sessions) == 1

        result = client.exec_command("uptime")
        assert result == ("stdin", "stdout", "stderr")
        client.close()

        # After close: session released
        assert len(p._active_sessions) == 0

    # After sandbox: patch restored
    assert paramiko.SSHClient is not _FakeSSHClient


# ---------------------------------------------------------------------------
# 12. Multiple sessions
# ---------------------------------------------------------------------------


# ESCAPE: test_multiple_sequential_sessions
#   CLAIM: Two sequential sessions on the same plugin work correctly.
#   PATH:  First session: connect -> exec_command -> close.
#          Second session: connect -> open_sftp -> sftp_get -> close.
#          Both are queued and consumed in order.
#   CHECK: Both sessions execute fully; active_sessions empty after both close.
#   MUTATION: Queue not being FIFO would bind sessions in wrong order.
#   ESCAPE: Wrong scripted return on second session's sftp_get would fail equality check.
def test_multiple_sequential_sessions() -> None:
    v, p = _make_verifier_with_plugin()

    # First session
    s1 = p.new_session()
    s1.expect("connect", returns=None)
    s1.expect("exec_command", returns=("stdin", "stdout", "stderr"))
    s1.expect("close", returns=None)

    # Second session
    s2 = p.new_session()
    s2.expect("connect", returns=None)
    s2.expect("open_sftp", returns=None)
    s2.expect("sftp_get", returns=None)
    s2.expect("close", returns=None)

    with v.sandbox():
        # First connection
        client1 = paramiko.SSHClient()
        client1.connect("host1", port=22, username="user1")
        result1 = client1.exec_command("ls")
        client1.close()

        # Second connection
        client2 = paramiko.SSHClient()
        client2.connect("host2", port=22, username="user2")
        sftp = client2.open_sftp()
        sftp.get("/remote/file.txt", "/local/file.txt")
        client2.close()

    assert result1 == ("stdin", "stdout", "stderr")
    assert len(p._active_sessions) == 0
    assert len(p._session_queue) == 0

    # Assert first session interactions
    v.assert_interaction(
        p.connect, hostname="host1", port=22, username="user1", auth_method="password"
    )
    v.assert_interaction(p.exec_command, command="ls")
    v.assert_interaction(p.close)

    # Assert second session interactions
    v.assert_interaction(
        p.connect, hostname="host2", port=22, username="user2", auth_method="password"
    )
    v.assert_interaction(p.open_sftp)
    v.assert_interaction(p.sftp_get, remotepath="/remote/file.txt", localpath="/local/file.txt")
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# matches() override
# ---------------------------------------------------------------------------


# ESCAPE: test_matches_field_by_field
#   CLAIM: matches() compares field-by-field and returns True only when all fields match.
#   PATH:  Record a connect interaction, call matches with correct and incorrect expected dicts.
#   CHECK: matches returns True for correct values, False for incorrect values.
#   MUTATION: A placeholder matches() that always returns True would pass the True case
#             but fail the False case.
#   ESCAPE: Nothing reasonable -- both True and False paths checked.
def test_matches_field_by_field() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        client.close()

    interactions = v._timeline._interactions
    connect_interaction = [i for i in interactions if i.source_id == "ssh:connect"][0]

    # Correct match
    assert p.matches(
        connect_interaction,
        {"hostname": "myhost", "port": 22, "username": "user", "auth_method": "password"},
    ) is True

    # Wrong hostname
    assert p.matches(
        connect_interaction,
        {"hostname": "wrong", "port": 22, "username": "user", "auth_method": "password"},
    ) is False

    # Wrong port
    assert p.matches(
        connect_interaction,
        {"hostname": "myhost", "port": 9999, "username": "user", "auth_method": "password"},
    ) is False

    # Wrong username
    assert p.matches(
        connect_interaction,
        {"hostname": "myhost", "port": 22, "username": "wrong", "auth_method": "password"},
    ) is False

    # Wrong auth_method
    assert p.matches(
        connect_interaction,
        {"hostname": "myhost", "port": 22, "username": "user", "auth_method": "key"},
    ) is False


# ---------------------------------------------------------------------------
# auth_method extraction
# ---------------------------------------------------------------------------


# ESCAPE: test_connect_with_pkey_sets_auth_method_key
#   CLAIM: When pkey= is provided to connect(), auth_method is recorded as "key".
#   PATH:  _FakeSSHClient.connect(pkey=...) -> auth_method="key" in details.
#   CHECK: assert_interaction verifies auth_method=="key".
#   MUTATION: Ignoring pkey kwarg would leave auth_method as "password".
#   ESCAPE: Nothing reasonable -- exact field equality.
def test_connect_with_pkey_sets_auth_method_key() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user", pkey="fake_key")
        client.close()

    interactions = v._timeline._interactions
    connect_interaction = [i for i in interactions if i.source_id == "ssh:connect"][0]
    assert connect_interaction.details["auth_method"] == "key"


# ESCAPE: test_connect_with_key_filename_sets_auth_method_key
#   CLAIM: When key_filename= is provided to connect(), auth_method is recorded as "key".
#   PATH:  _FakeSSHClient.connect(key_filename=...) -> auth_method="key" in details.
#   CHECK: assert_interaction verifies auth_method=="key".
#   MUTATION: Ignoring key_filename kwarg would leave auth_method as "password".
#   ESCAPE: Nothing reasonable -- exact field equality.
def test_connect_with_key_filename_sets_auth_method_key() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user", key_filename="/path/to/key")
        client.close()

    interactions = v._timeline._interactions
    connect_interaction = [i for i in interactions if i.source_id == "ssh:connect"][0]
    assert connect_interaction.details["auth_method"] == "key"


# ESCAPE: test_connect_without_key_sets_auth_method_password
#   CLAIM: When neither pkey= nor key_filename= is provided, auth_method is "password".
#   PATH:  _FakeSSHClient.connect() without pkey/key_filename -> auth_method="password".
#   CHECK: assert_interaction verifies auth_method=="password".
#   MUTATION: Always setting auth_method to "key" would fail this check.
#   ESCAPE: Nothing reasonable -- exact field equality.
def test_connect_without_key_sets_auth_method_password() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        client = paramiko.SSHClient()
        client.connect("myhost", port=22, username="user")
        client.close()

    interactions = v._timeline._interactions
    connect_interaction = [i for i in interactions if i.source_id == "ssh:connect"][0]
    assert connect_interaction.details["auth_method"] == "password"


# ---------------------------------------------------------------------------
# Sentinel properties
# ---------------------------------------------------------------------------


# ESCAPE: test_sentinel_properties
#   CLAIM: All sentinel properties return _StepSentinel instances with correct source_ids.
#   PATH:  Access each property on the plugin instance.
#   CHECK: Each sentinel.source_id == expected source_id string.
#   MUTATION: Wrong source_id string fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality on each.
def test_sentinel_properties() -> None:
    from bigfoot._state_machine_plugin import _StepSentinel

    v, p = _make_verifier_with_plugin()

    assert isinstance(p.connect, _StepSentinel)
    assert p.connect.source_id == "ssh:connect"

    assert isinstance(p.exec_command, _StepSentinel)
    assert p.exec_command.source_id == "ssh:exec_command"

    assert isinstance(p.open_sftp, _StepSentinel)
    assert p.open_sftp.source_id == "ssh:open_sftp"

    assert isinstance(p.sftp_get, _StepSentinel)
    assert p.sftp_get.source_id == "ssh:sftp_get"

    assert isinstance(p.sftp_put, _StepSentinel)
    assert p.sftp_put.source_id == "ssh:sftp_put"

    assert isinstance(p.sftp_listdir, _StepSentinel)
    assert p.sftp_listdir.source_id == "ssh:sftp_listdir"

    assert isinstance(p.sftp_stat, _StepSentinel)
    assert p.sftp_stat.source_id == "ssh:sftp_stat"

    assert isinstance(p.sftp_mkdir, _StepSentinel)
    assert p.sftp_mkdir.source_id == "ssh:sftp_mkdir"

    assert isinstance(p.sftp_remove, _StepSentinel)
    assert p.sftp_remove.source_id == "ssh:sftp_remove"

    assert isinstance(p.close, _StepSentinel)
    assert p.close.source_id == "ssh:close"


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.ssh_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_ssh_mock_proxy_new_session
#   CLAIM: bigfoot.ssh_mock.new_session() returns a SessionHandle.
#   PATH:  _SshProxy.__getattr__("new_session") -> get verifier -> find/create SshPlugin ->
#          return plugin.new_session.
#   CHECK: session is a SessionHandle instance; chaining .expect() does not raise.
#   MUTATION: Returning None instead of a SessionHandle would fail isinstance check.
#   ESCAPE: Nothing reasonable -- both the isinstance and the chained .expect() call check it.
def test_ssh_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    from bigfoot._state_machine_plugin import SessionHandle

    session = bigfoot.ssh_mock.new_session()
    assert isinstance(session, SessionHandle)
    result = session.expect("connect", returns=None, required=False)
    assert result is session  # expect() returns self for chaining


# ESCAPE: test_ssh_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.ssh_mock outside a test context raises NoActiveVerifierError.
#   PATH:  _SshProxy.__getattr__ -> _get_test_verifier_or_raise -> NoActiveVerifierError.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Silently returning None would not raise and hide context failures.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_ssh_mock_proxy_raises_outside_context() -> None:
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.ssh_mock.new_session  # noqa: B018
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# Flow tests with assert_interaction() calls
# ---------------------------------------------------------------------------


# ESCAPE: test_full_exec_command_flow_assertions
#   CLAIM: A complete SSH exec_command flow records correct interaction details.
#   PATH:  sandbox -> connect -> exec_command -> close -> assert each interaction.
#   CHECK: assert_interaction verifies every assertable field for every step.
#   MUTATION: Wrong detail values in any step fail the assertion.
#   ESCAPE: Nothing reasonable -- full field coverage on all assertable steps.
def test_full_exec_command_flow_assertions(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.ssh_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("exec_command", returns=("stdin", "stdout", "stderr"))
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        client = paramiko.SSHClient()
        client.connect("server.example.com", port=2222, username="admin")
        client.exec_command("whoami")
        client.close()

    bigfoot.ssh_mock.assert_connect(
        hostname="server.example.com", port=2222, username="admin", auth_method="password"
    )
    bigfoot.ssh_mock.assert_exec_command(command="whoami")
    bigfoot.ssh_mock.assert_close()


# ESCAPE: test_sftp_flow_assertions
#   CLAIM: A complete SFTP flow records correct interaction details.
#   PATH:  sandbox -> connect -> open_sftp -> sftp_get -> sftp_put -> close -> assert each.
#   CHECK: assert_interaction verifies every assertable field for every step.
#   MUTATION: Wrong detail values in any step fail the assertion.
#   ESCAPE: Nothing reasonable -- full field coverage on all assertable steps.
def test_sftp_flow_assertions(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.ssh_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_get", returns=None)
    session.expect("sftp_put", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        client = paramiko.SSHClient()
        client.connect("sftp.example.com", port=22, username="transfer")
        sftp = client.open_sftp()
        sftp.get("/remote/data.csv", "/local/data.csv")
        sftp.put("/local/results.csv", "/remote/results.csv")
        client.close()

    bigfoot.ssh_mock.assert_connect(
        hostname="sftp.example.com", port=22, username="transfer", auth_method="password"
    )
    bigfoot.ssh_mock.assert_open_sftp()
    bigfoot.ssh_mock.assert_sftp_get(remotepath="/remote/data.csv", localpath="/local/data.csv")
    bigfoot.ssh_mock.assert_sftp_put(localpath="/local/results.csv", remotepath="/remote/results.csv")
    bigfoot.ssh_mock.assert_close()


# ESCAPE: test_multiple_sequential_sessions_assertions
#   CLAIM: Two sequential sessions record correct interaction details for each session.
#   PATH:  Two sessions queued -> first: connect/exec_command/close;
#          second: connect/open_sftp/sftp_get/close -> assert all interactions.
#   CHECK: assert_interaction verifies fields for every step in both sessions.
#   MUTATION: Wrong hostname or command values fail the assertion.
#   ESCAPE: Nothing reasonable -- full field coverage on both sessions.
def test_multiple_sequential_sessions_assertions(bigfoot_verifier: StrictVerifier) -> None:
    # First session
    s1 = bigfoot.ssh_mock.new_session()
    s1.expect("connect", returns=None)
    s1.expect("exec_command", returns=("stdin", "stdout", "stderr"))
    s1.expect("close", returns=None)

    # Second session
    s2 = bigfoot.ssh_mock.new_session()
    s2.expect("connect", returns=None)
    s2.expect("open_sftp", returns=None)
    s2.expect("sftp_get", returns=None)
    s2.expect("close", returns=None)

    with bigfoot.sandbox():
        # First connection
        client1 = paramiko.SSHClient()
        client1.connect("host1", port=22, username="user1")
        client1.exec_command("ls")
        client1.close()

        # Second connection
        client2 = paramiko.SSHClient()
        client2.connect("host2", port=22, username="user2")
        sftp = client2.open_sftp()
        sftp.get("/remote/file.txt", "/local/file.txt")
        client2.close()

    # Assert first session interactions
    bigfoot.ssh_mock.assert_connect(
        hostname="host1", port=22, username="user1", auth_method="password"
    )
    bigfoot.ssh_mock.assert_exec_command(command="ls")
    bigfoot.ssh_mock.assert_close()

    # Assert second session interactions
    bigfoot.ssh_mock.assert_connect(
        hostname="host2", port=22, username="user2", auth_method="password"
    )
    bigfoot.ssh_mock.assert_open_sftp()
    bigfoot.ssh_mock.assert_sftp_get(remotepath="/remote/file.txt", localpath="/local/file.txt")
    bigfoot.ssh_mock.assert_close()


# ---------------------------------------------------------------------------
# format_* method tests
# ---------------------------------------------------------------------------


# ESCAPE: test_format_interaction_connect
#   CLAIM: format_interaction for a connect interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="ssh:connect" and details -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_connect() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:connect",
        sequence=0,
        details={"hostname": "myhost", "port": 22, "username": "user", "auth_method": "password"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] ssh.connect(hostname='myhost', port=22, username='user')"


# ESCAPE: test_format_interaction_exec_command
#   CLAIM: format_interaction for an exec_command interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="ssh:exec_command" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_exec_command() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:exec_command",
        sequence=0,
        details={"command": "ls -la"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] ssh.exec_command(command='ls -la')"


# ESCAPE: test_format_interaction_open_sftp
#   CLAIM: format_interaction for an open_sftp interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="ssh:open_sftp" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_open_sftp() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:open_sftp",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] ssh.open_sftp()"


# ESCAPE: test_format_interaction_sftp_get
#   CLAIM: format_interaction for a sftp_get interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="ssh:sftp_get" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_sftp_get() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_get",
        sequence=0,
        details={"remotepath": "/remote/file.txt", "localpath": "/local/file.txt"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] sftp.get(remotepath='/remote/file.txt', localpath='/local/file.txt')"


# ESCAPE: test_format_interaction_sftp_put
#   CLAIM: format_interaction for a sftp_put interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="ssh:sftp_put" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_sftp_put() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_put",
        sequence=0,
        details={"localpath": "/local/file.txt", "remotepath": "/remote/file.txt"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] sftp.put(localpath='/local/file.txt', remotepath='/remote/file.txt')"


# ESCAPE: test_format_interaction_sftp_listdir
#   CLAIM: format_interaction for a sftp_listdir interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="ssh:sftp_listdir" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_sftp_listdir() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_listdir",
        sequence=0,
        details={"path": "/remote/dir"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] sftp.listdir(path='/remote/dir')"


# ESCAPE: test_format_interaction_sftp_stat
#   CLAIM: format_interaction for a sftp_stat interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="ssh:sftp_stat" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_sftp_stat() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_stat",
        sequence=0,
        details={"path": "/remote/file.txt"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] sftp.stat(path='/remote/file.txt')"


# ESCAPE: test_format_interaction_sftp_mkdir
#   CLAIM: format_interaction for a sftp_mkdir interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="ssh:sftp_mkdir" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_sftp_mkdir() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_mkdir",
        sequence=0,
        details={"path": "/remote/newdir"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] sftp.mkdir(path='/remote/newdir')"


# ESCAPE: test_format_interaction_sftp_remove
#   CLAIM: format_interaction for a sftp_remove interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="ssh:sftp_remove" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_sftp_remove() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_remove",
        sequence=0,
        details={"path": "/remote/oldfile.txt"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] sftp.remove(path='/remote/oldfile.txt')"


# ESCAPE: test_format_interaction_close
#   CLAIM: format_interaction for a close interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="ssh:close" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_close() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:close",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] ssh.close()"


# ESCAPE: test_format_interaction_unknown
#   CLAIM: format_interaction for an unknown source_id returns the fallback string.
#   PATH:  Create Interaction with source_id="ssh:unknown_method" -> format_interaction.
#   CHECK: result == exact expected fallback string.
#   MUTATION: Wrong fallback format fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_unknown() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:unknown_method",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[SshPlugin] ssh.unknown_method(...)"


# ESCAPE: test_format_mock_hint
#   CLAIM: format_mock_hint returns copy-pasteable code to mock the interaction.
#   PATH:  format_mock_hint(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_mock_hint() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:exec_command",
        sequence=0,
        details={"command": "ls"},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.ssh_mock.new_session().expect('exec_command', returns=...)"


# ESCAPE: test_format_mock_hint_connect
#   CLAIM: format_mock_hint for a connect interaction returns the correct hint.
#   PATH:  format_mock_hint(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong method name in hint fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_mock_hint_connect() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:connect",
        sequence=0,
        details={"hostname": "myhost", "port": 22, "username": "user", "auth_method": "password"},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.ssh_mock.new_session().expect('connect', returns=...)"


# ESCAPE: test_format_unmocked_hint
#   CLAIM: format_unmocked_hint returns copy-pasteable code for an unmocked call.
#   PATH:  format_unmocked_hint(source_id, args, kwargs) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("ssh:connect", (), {})
    assert result == (
        "paramiko.SSHClient.connect(...) was called but no session was queued.\n"
        "Register a session with:\n"
        "    bigfoot.ssh_mock.new_session().expect('connect', returns=...)"
    )


# ESCAPE: test_format_unmocked_hint_exec_command
#   CLAIM: format_unmocked_hint for exec_command returns the correct hint.
#   PATH:  format_unmocked_hint("ssh:exec_command", ...) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong method name fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_unmocked_hint_exec_command() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("ssh:exec_command", (), {})
    assert result == (
        "paramiko.SSHClient.exec_command(...) was called but no session was queued.\n"
        "Register a session with:\n"
        "    bigfoot.ssh_mock.new_session().expect('exec_command', returns=...)"
    )


# ESCAPE: test_format_assert_hint_connect
#   CLAIM: format_assert_hint for connect returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_connect syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_connect() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:connect",
        sequence=0,
        details={"hostname": "myhost", "port": 22, "username": "user", "auth_method": "password"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.ssh_mock.assert_connect("
        "hostname='myhost', port=22, username='user', auth_method='password')"
    )


# ESCAPE: test_format_assert_hint_exec_command
#   CLAIM: format_assert_hint for exec_command returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_exec_command syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_exec_command() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:exec_command",
        sequence=0,
        details={"command": "uptime"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.ssh_mock.assert_exec_command(command='uptime')"


# ESCAPE: test_format_assert_hint_open_sftp
#   CLAIM: format_assert_hint for open_sftp returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_open_sftp syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_open_sftp() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:open_sftp",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.ssh_mock.assert_open_sftp()"


# ESCAPE: test_format_assert_hint_sftp_get
#   CLAIM: format_assert_hint for sftp_get returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_sftp_get syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_sftp_get() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_get",
        sequence=0,
        details={"remotepath": "/remote/file.txt", "localpath": "/local/file.txt"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.ssh_mock.assert_sftp_get("
        "remotepath='/remote/file.txt', localpath='/local/file.txt')"
    )


# ESCAPE: test_format_assert_hint_sftp_put
#   CLAIM: format_assert_hint for sftp_put returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_sftp_put syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_sftp_put() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_put",
        sequence=0,
        details={"localpath": "/local/file.txt", "remotepath": "/remote/file.txt"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.ssh_mock.assert_sftp_put("
        "localpath='/local/file.txt', remotepath='/remote/file.txt')"
    )


# ESCAPE: test_format_assert_hint_close
#   CLAIM: format_assert_hint for close returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_close syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_close() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:close",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.ssh_mock.assert_close()"


# ESCAPE: test_format_assert_hint_unknown
#   CLAIM: format_assert_hint for an unknown source_id returns the fallback string.
#   PATH:  format_assert_hint(interaction) -> fallback string.
#   CHECK: result == exact expected fallback string.
#   MUTATION: Wrong fallback format fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_unknown() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:unknown_op",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    # bigfoot.ssh_mock: unknown source_id='ssh:unknown_op'"


# ESCAPE: test_format_unused_mock_hint
#   CLAIM: format_unused_mock_hint returns hint containing method name and traceback.
#   PATH:  format_unused_mock_hint(mock_config) -> string.
#   CHECK: result == exact expected string including registration_traceback.
#   MUTATION: Wrong prefix text fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    step = ScriptStep(method="exec_command", returns=None)
    result = p.format_unused_mock_hint(step)
    expected_prefix = (
        "paramiko.SSHClient.exec_command(...) was mocked (required=True) but never called.\n"
        "Registered at:\n"
    )
    assert result == expected_prefix + step.registration_traceback


# ---------------------------------------------------------------------------
# Fix 5: format_assert_hint tests for sftp_listdir, sftp_stat, sftp_mkdir, sftp_remove
# ---------------------------------------------------------------------------


# ESCAPE: test_format_assert_hint_sftp_listdir
#   CLAIM: format_assert_hint for sftp_listdir returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_sftp_listdir syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_sftp_listdir() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_listdir",
        sequence=0,
        details={"path": "/remote/dir"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.ssh_mock.assert_sftp_listdir(path='/remote/dir')"


# ESCAPE: test_format_assert_hint_sftp_stat
#   CLAIM: format_assert_hint for sftp_stat returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_sftp_stat syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_sftp_stat() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_stat",
        sequence=0,
        details={"path": "/remote/file.txt"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.ssh_mock.assert_sftp_stat(path='/remote/file.txt')"


# ESCAPE: test_format_assert_hint_sftp_mkdir
#   CLAIM: format_assert_hint for sftp_mkdir returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_sftp_mkdir syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_sftp_mkdir() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_mkdir",
        sequence=0,
        details={"path": "/remote/newdir"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.ssh_mock.assert_sftp_mkdir(path='/remote/newdir')"


# ESCAPE: test_format_assert_hint_sftp_remove
#   CLAIM: format_assert_hint for sftp_remove returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_sftp_remove syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_sftp_remove() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="ssh:sftp_remove",
        sequence=0,
        details={"path": "/remote/oldfile.txt"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.ssh_mock.assert_sftp_remove(path='/remote/oldfile.txt')"


# ---------------------------------------------------------------------------
# Fix 6: Typed helper tests for sftp_listdir, sftp_stat, sftp_mkdir, sftp_remove
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_sftp_listdir_helper
#   CLAIM: assert_sftp_listdir() typed helper correctly asserts a sftp_listdir interaction.
#   PATH:  Record sftp_listdir interaction -> assert_sftp_listdir with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong path would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_sftp_listdir_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.ssh_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_listdir", returns=["file1.txt", "file2.txt"])
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        client = paramiko.SSHClient()
        client.connect("server.example.com", port=22, username="deploy")
        sftp = client.open_sftp()
        sftp.listdir("/remote/dir")
        client.close()

    bigfoot.ssh_mock.assert_connect(
        hostname="server.example.com", port=22, username="deploy", auth_method="password"
    )
    bigfoot.ssh_mock.assert_open_sftp()
    bigfoot.ssh_mock.assert_sftp_listdir(path="/remote/dir")
    bigfoot.ssh_mock.assert_close()


# ESCAPE: test_assert_sftp_stat_helper
#   CLAIM: assert_sftp_stat() typed helper correctly asserts a sftp_stat interaction.
#   PATH:  Record sftp_stat interaction -> assert_sftp_stat with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong path would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_sftp_stat_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.ssh_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_stat", returns="fake_stat_result")
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        client = paramiko.SSHClient()
        client.connect("server.example.com", port=22, username="deploy")
        sftp = client.open_sftp()
        sftp.stat("/remote/file.txt")
        client.close()

    bigfoot.ssh_mock.assert_connect(
        hostname="server.example.com", port=22, username="deploy", auth_method="password"
    )
    bigfoot.ssh_mock.assert_open_sftp()
    bigfoot.ssh_mock.assert_sftp_stat(path="/remote/file.txt")
    bigfoot.ssh_mock.assert_close()


# ESCAPE: test_assert_sftp_mkdir_helper
#   CLAIM: assert_sftp_mkdir() typed helper correctly asserts a sftp_mkdir interaction.
#   PATH:  Record sftp_mkdir interaction -> assert_sftp_mkdir with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong path would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_sftp_mkdir_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.ssh_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_mkdir", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        client = paramiko.SSHClient()
        client.connect("server.example.com", port=22, username="deploy")
        sftp = client.open_sftp()
        sftp.mkdir("/remote/newdir")
        client.close()

    bigfoot.ssh_mock.assert_connect(
        hostname="server.example.com", port=22, username="deploy", auth_method="password"
    )
    bigfoot.ssh_mock.assert_open_sftp()
    bigfoot.ssh_mock.assert_sftp_mkdir(path="/remote/newdir")
    bigfoot.ssh_mock.assert_close()


# ESCAPE: test_assert_sftp_remove_helper
#   CLAIM: assert_sftp_remove() typed helper correctly asserts a sftp_remove interaction.
#   PATH:  Record sftp_remove interaction -> assert_sftp_remove with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong path would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_sftp_remove_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.ssh_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("open_sftp", returns=None)
    session.expect("sftp_remove", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        client = paramiko.SSHClient()
        client.connect("server.example.com", port=22, username="deploy")
        sftp = client.open_sftp()
        sftp.remove("/remote/oldfile.txt")
        client.close()

    bigfoot.ssh_mock.assert_connect(
        hostname="server.example.com", port=22, username="deploy", auth_method="password"
    )
    bigfoot.ssh_mock.assert_open_sftp()
    bigfoot.ssh_mock.assert_sftp_remove(path="/remote/oldfile.txt")
    bigfoot.ssh_mock.assert_close()


# ---------------------------------------------------------------------------
# Fix 7: set_missing_host_key_policy test
# ---------------------------------------------------------------------------


# ESCAPE: test_set_missing_host_key_policy_no_op
#   CLAIM: _FakeSSHClient.set_missing_host_key_policy() is a no-op that doesn't raise.
#   PATH:  Instantiate _FakeSSHClient -> call set_missing_host_key_policy -> no exception.
#   CHECK: No exception raised; method returns None.
#   MUTATION: Removing the method would raise AttributeError.
#   ESCAPE: Nothing reasonable -- verifies the no-op method exists and is callable.
def test_set_missing_host_key_policy_no_op() -> None:
    client = _FakeSSHClient()
    result = client.set_missing_host_key_policy("any_policy")
    assert result is None
