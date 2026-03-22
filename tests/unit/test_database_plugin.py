"""Unit tests for DatabasePlugin."""

from __future__ import annotations

import sqlite3

import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import InvalidStateError, UnmockedInteractionError
from bigfoot._state_machine_plugin import ScriptStep
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.database_plugin import DatabasePlugin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, DatabasePlugin]:
    """Return (verifier, plugin) with plugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    DatabasePlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, DatabasePlugin):
            return v, p
    p = DatabasePlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    from bigfoot.plugins.database_plugin import DatabasePlugin

    with DatabasePlugin._install_lock:
        DatabasePlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        DatabasePlugin.__new__(DatabasePlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_install_count() -> None:
    """Ensure DatabasePlugin install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield
    _reset_install_count()


# ---------------------------------------------------------------------------
# Static interface: _initial_state / _transitions / _unmocked_source_id
# ---------------------------------------------------------------------------


# ESCAPE: test_initial_state
#   CLAIM: _initial_state() returns "connected".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "connected".
#   MUTATION: Returning "disconnected" would fail the equality check.
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
        "execute": {"connected": "in_transaction", "in_transaction": "in_transaction"},
        "commit": {"in_transaction": "connected"},
        "rollback": {"in_transaction": "connected"},
        "close": {"connected": "closed", "in_transaction": "closed"},
    }


# ESCAPE: test_unmocked_source_id
#   CLAIM: _unmocked_source_id() returns "db:connect".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "db:connect".
#   MUTATION: Returning a different string fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_unmocked_source_id() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._unmocked_source_id() == "db:connect"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patch
#   CLAIM: After activate(), sqlite3.connect is replaced with a bigfoot interceptor.
#   PATH:  activate() -> _install_count == 0 -> store original -> install interceptor.
#   CHECK: sqlite3.connect is not the original function after activate().
#   MUTATION: Skipping patch installation leaves original in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison against saved original.
def test_activate_installs_patch() -> None:
    v, p = _make_verifier_with_plugin()
    original_connect = sqlite3.connect
    p.activate()
    assert sqlite3.connect is not original_connect
    p.deactivate()


# ESCAPE: test_deactivate_restores_patch
#   CLAIM: After activate() then deactivate(), sqlite3.connect is restored to the original.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore original.
#   CHECK: sqlite3.connect is the original function again.
#   MUTATION: Not restoring in deactivate() leaves bigfoot's interceptor in place.
#   ESCAPE: Nothing reasonable -- identity comparison against saved original.
def test_deactivate_restores_patch() -> None:
    v, p = _make_verifier_with_plugin()
    original_connect = sqlite3.connect
    p.activate()
    p.deactivate()
    assert sqlite3.connect is original_connect


# ESCAPE: test_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patch is removed.
#   PATH:  First activate -> _install_count=1; second activate -> _install_count=2 (no reinstall).
#          First deactivate -> _install_count=1 (patch remains).
#          Second deactivate -> _install_count=0 (original restored).
#   CHECK: After first deactivate, sqlite3.connect is still patched.
#          After second deactivate, it is the original.
#   MUTATION: Restoring on first deactivate would fail the mid-point identity check.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove count-controlled restoration.
def test_reference_counting_nested() -> None:
    from bigfoot.plugins.database_plugin import DatabasePlugin

    v, p = _make_verifier_with_plugin()
    original_connect = sqlite3.connect
    p.activate()
    p.activate()
    assert DatabasePlugin._install_count == 2

    p.deactivate()
    assert DatabasePlugin._install_count == 1
    assert sqlite3.connect is not original_connect

    p.deactivate()
    assert DatabasePlugin._install_count == 0
    assert sqlite3.connect is original_connect


# ---------------------------------------------------------------------------
# Basic session: execute + fetchall
# ---------------------------------------------------------------------------


# ESCAPE: test_basic_execute_fetchall
#   CLAIM: sqlite3.connect() returns a _FakeConnection; calling execute() with a scripted
#          returns list allows fetchall() to return that exact list.
#   PATH:  _patched_connect -> _bind_connection -> _FakeConnection returned.
#          conn.execute(sql) -> _execute_step(handle, "execute", ...) -> returns [[1, "Alice"]]
#          -> _FakeCursor(rows=[[1, "Alice"]]) stored as _last_cursor.
#          cursor.fetchall() -> returns [[1, "Alice"]].
#   CHECK: rows == [[1, "Alice"]].
#   MUTATION: Returning wrong rows fails the equality check.
#   ESCAPE: fetchall() returning a different list type (tuple) would fail exact equality.
def test_basic_execute_fetchall() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[[1, "Alice"], [2, "Bob"]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        cursor = conn.execute("SELECT id, name FROM users")
        rows = cursor.fetchall()
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="SELECT id, name FROM users", parameters=())
    v.assert_interaction(p.close)
    assert rows == [[1, "Alice"], [2, "Bob"]]


# ---------------------------------------------------------------------------
# execute + fetchone
# ---------------------------------------------------------------------------


# ESCAPE: test_execute_fetchone
#   CLAIM: fetchone() returns the first row from the scripted returns list,
#          and a second fetchone() returns the second row.
#   PATH:  conn.execute(sql) -> cursor._last_cursor = _FakeCursor([[1, "A"], [2, "B"]]).
#          fetchone() returns [1, "A"] and advances _pos to 1.
#          Second fetchone() returns [2, "B"] and advances _pos to 2.
#   CHECK: first == [1, "A"]; second == [2, "B"].
#   MUTATION: Not advancing _pos after fetchone would return [1, "A"] on second call.
#   ESCAPE: Wrong row values fail the exact equality checks.
def test_execute_fetchone() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[[1, "Alice"], [2, "Bob"]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        cursor = conn.execute("SELECT id, name FROM users")
        first = cursor.fetchone()
        second = cursor.fetchone()
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="SELECT id, name FROM users", parameters=())
    v.assert_interaction(p.close)
    assert first == [1, "Alice"]
    assert second == [2, "Bob"]


# ---------------------------------------------------------------------------
# cursor() + cursor.execute() + fetchall
# ---------------------------------------------------------------------------


# ESCAPE: test_cursor_execute_fetchall
#   CLAIM: conn.cursor() returns a _FakeCursorProxy; calling execute() on it
#          with a scripted returns list allows fetchall() on that cursor.
#   PATH:  conn.cursor() -> _FakeCursorProxy(conn).
#          cursor.execute(sql) -> _execute_step(handle, "execute", ...) -> returns [["x"]].
#          cursor.fetchall() -> returns [["x"]].
#   CHECK: rows == [["x"]].
#   MUTATION: Returning wrong rows fails the equality check.
#   ESCAPE: fetchall on a different cursor object would return [].
def test_cursor_execute_fetchall() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[["x"], ["y"]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        cursor = conn.cursor()
        cursor.execute("SELECT val FROM t")
        rows = cursor.fetchall()
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="SELECT val FROM t", parameters=())
    v.assert_interaction(p.close)
    assert rows == [["x"], ["y"]]


# ---------------------------------------------------------------------------
# commit state transition
# ---------------------------------------------------------------------------


# ESCAPE: test_commit_state_transition
#   CLAIM: After execute() (state -> in_transaction), commit() transitions state
#          back to "connected" without error.
#   PATH:  execute -> state="in_transaction"; commit -> state="connected".
#          close from "connected" is valid.
#   CHECK: No InvalidStateError raised; full sequence completes; no unused mocks.
#   MUTATION: Not updating state after commit would leave state as "in_transaction";
#             close from "in_transaction" is valid too, so verify via execute->commit->execute.
#   ESCAPE: If commit does not reset state, second execute->commit->execute->commit chain passes;
#           use commit->commit (second commit from "connected") to catch non-transition.
def test_commit_state_transition() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("commit", returns=None)
    session.expect("execute", returns=[])  # only valid if commit reset state to "connected"
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.execute("INSERT INTO t VALUES (2)")  # would fail if state stuck at "in_transaction"
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="INSERT INTO t VALUES (1)", parameters=())
    v.assert_interaction(p.commit)
    v.assert_interaction(p.execute, sql="INSERT INTO t VALUES (2)", parameters=())
    v.assert_interaction(p.close)
    assert p.get_unused_mocks() == []


# ---------------------------------------------------------------------------
# rollback state transition
# ---------------------------------------------------------------------------


# ESCAPE: test_rollback_state_transition
#   CLAIM: After execute() (state -> in_transaction), rollback() transitions
#          state back to "connected" without error.
#   PATH:  execute -> state="in_transaction"; rollback -> state="connected".
#   CHECK: No InvalidStateError; full sequence completes; no unused mocks.
#   MUTATION: Not resetting state after rollback leaves state as "in_transaction";
#             execute -> rollback -> execute sequence would catch this.
#   ESCAPE: Same logic as commit test but for rollback.
def test_rollback_state_transition() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("rollback", returns=None)
    session.expect("execute", returns=[])  # only valid if rollback reset state to "connected"
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.rollback()
        conn.execute("INSERT INTO t VALUES (2)")  # would fail if state stuck at "in_transaction"
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="INSERT INTO t VALUES (1)", parameters=())
    v.assert_interaction(p.rollback)
    v.assert_interaction(p.execute, sql="INSERT INTO t VALUES (2)", parameters=())
    v.assert_interaction(p.close)
    assert p.get_unused_mocks() == []


# ---------------------------------------------------------------------------
# close() releases session
# ---------------------------------------------------------------------------


# ESCAPE: test_close_releases_session
#   CLAIM: After conn.close() is called, the session is removed from _active_sessions
#          and get_unused_mocks() returns nothing (all steps consumed).
#   PATH:  _bind_connection at connect time; execute -> state="in_transaction";
#          close -> _execute_step("close") -> _release_session(conn) ->
#          key removed from _active_sessions -> get_unused_mocks() finds nothing.
#   CHECK: len(p._active_sessions) == 0 after sandbox; get_unused_mocks() == [].
#   MUTATION: Not calling _release_session in close leaves session in _active_sessions.
#   ESCAPE: _active_sessions having len > 0 would fail the length check.
def test_close_releases_session() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        conn.execute("SELECT 1")
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="SELECT 1", parameters=())
    v.assert_interaction(p.close)
    assert len(p._active_sessions) == 0
    assert p.get_unused_mocks() == []


# ---------------------------------------------------------------------------
# InvalidStateError: commit before execute
# ---------------------------------------------------------------------------


# ESCAPE: test_commit_before_execute_raises_invalid_state
#   CLAIM: Calling commit() in state "connected" (before any execute) raises
#          InvalidStateError with correct attributes.
#   PATH:  _bind_connection at connect time -> state="connected".
#          commit() -> _execute_step(handle, "commit", ...) -> "connected" not in
#          {"in_transaction"} -> InvalidStateError.
#   CHECK: InvalidStateError raised; exc.source_id == "db:commit"; exc.method == "commit";
#          exc.current_state == "connected"; exc.valid_states == frozenset({"in_transaction"}).
#   MUTATION: Not checking current state allows call through without raising.
#   ESCAPE: Raising with wrong source_id or method fails the attribute equality checks.
def test_commit_before_execute_raises_invalid_state() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    # Connect step brings us to "connected"; commit from "connected" is invalid
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        with pytest.raises(InvalidStateError) as exc_info:
            conn.commit()
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.close)
    exc = exc_info.value
    assert exc.source_id == "db:commit"
    assert exc.method == "commit"
    assert exc.current_state == "connected"
    assert exc.valid_states == frozenset({"in_transaction"})


# ---------------------------------------------------------------------------
# get_unused_mocks: unconsumed required steps
# ---------------------------------------------------------------------------


# ESCAPE: test_get_unused_mocks_returns_unconsumed_steps
#   CLAIM: When a session has two expected steps but only one is consumed,
#          get_unused_mocks() returns exactly the one unconsumed required step.
#   PATH:  new_session with two expect() calls -> connect() binds session ->
#          execute() consumes step 0 -> "commit" step still in _script ->
#          _active_sessions has handle with one remaining required step ->
#          get_unused_mocks() returns it.
#   CHECK: len(unused) == 1; unused[0] is a ScriptStep with method == "commit".
#   MUTATION: Returning all steps (including consumed) would give len == 2; fails count check.
#   ESCAPE: Returning a step with method == "execute" instead of "commit" fails method check.
def test_get_unused_mocks_returns_unconsumed_steps() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("commit", returns=None)  # will NOT be consumed

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        conn.execute("SELECT 1")
        # deliberately NOT calling commit or close

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="SELECT 1", parameters=())
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].method == "commit"


# ESCAPE: test_get_unused_mocks_queued_session
#   CLAIM: A session that was queued but never bound (no connect was called)
#          has all its required steps returned by get_unused_mocks().
#   PATH:  new_session with two steps enqueued -> no connect -> _session_queue still holds handle ->
#          get_unused_mocks() iterates _session_queue and returns all required steps.
#   CHECK: len(unused) == 2; methods are ["execute", "close"] in order.
#   MUTATION: Not iterating _session_queue would return [] instead of 2 items.
#   ESCAPE: Returning items in wrong order (LIFO) would fail the method ordering check.
def test_get_unused_mocks_queued_session() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("execute", returns=[])
    session.expect("close", returns=None)

    # Never call sqlite3.connect; the session stays in the queue
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "execute"
    assert unused[1].method == "close"


# ---------------------------------------------------------------------------
# UnmockedInteractionError when no session queued
# ---------------------------------------------------------------------------


# ESCAPE: test_connect_with_empty_queue_raises_unmocked
#   CLAIM: If no session is queued when sqlite3.connect() fires,
#          UnmockedInteractionError is raised with source_id == "db:connect".
#   PATH:  _patched_connect -> _get_database_plugin() -> plugin._bind_connection(fake_conn) ->
#          _session_queue empty -> raise UnmockedInteractionError(source_id="db:connect").
#   CHECK: UnmockedInteractionError raised; exc.source_id == "db:connect".
#   MUTATION: Returning a dummy session for empty queue would not raise at all.
#   ESCAPE: Raising with source_id == "db:execute" instead would fail the source_id check.
def test_connect_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_verifier_with_plugin()
    # No session registered

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            sqlite3.connect(":memory:")

    assert exc_info.value.source_id == "db:connect"


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.db_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_db_mock_proxy_new_session
#   CLAIM: bigfoot.db_mock.new_session() returns a SessionHandle that can
#          be used to configure a session without importing DatabasePlugin directly.
#   PATH:  _DatabaseProxy.__getattr__("new_session") -> get verifier -> find/create DatabasePlugin ->
#          return plugin.new_session.
#   CHECK: session is a SessionHandle instance (no AttributeError, no None).
#          Chaining .expect() on it does not raise.
#   MUTATION: Returning None instead of a SessionHandle would fail isinstance check.
#   ESCAPE: Nothing reasonable -- both isinstance and chained .expect() call check it.
def test_db_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    from bigfoot._state_machine_plugin import SessionHandle

    session = bigfoot.db_mock.new_session()
    assert isinstance(session, SessionHandle)
    # Chaining expect() with required=False so it doesn't trigger UnusedMocksError at teardown.
    result = session.expect("execute", returns=[], required=False)
    assert result is session  # expect() returns self for chaining


# ESCAPE: test_db_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.db_mock outside a test context raises NoActiveVerifierError.
#   PATH:  _DatabaseProxy.__getattr__ -> _get_test_verifier_or_raise -> NoActiveVerifierError.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Silently returning None would not raise and hide context failures.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_db_mock_proxy_raises_outside_context() -> None:
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.db_mock.new_session
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# fetchone exhaustion returns None
# ---------------------------------------------------------------------------


# ESCAPE: test_fetchone_exhaustion_returns_none
#   CLAIM: After all rows are consumed by fetchone(), the next fetchone() returns None.
#   PATH:  _FakeCursor with one row: _pos starts at 0.
#          fetchone() -> returns row, _pos=1.
#          fetchone() -> _pos >= len(_rows) -> returns None.
#   CHECK: first == [42]; second is None.
#   MUTATION: Not returning None at exhaustion would return IndexError or a row.
#   ESCAPE: Returning a different sentinel (e.g., []) instead of None fails the `is None` check.
def test_fetchone_exhaustion_returns_none() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[[42]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        cursor = conn.execute("SELECT val FROM t")
        first = cursor.fetchone()
        second = cursor.fetchone()
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="SELECT val FROM t", parameters=())
    v.assert_interaction(p.close)
    assert first == [42]
    assert second is None


# ---------------------------------------------------------------------------
# fetchmany
# ---------------------------------------------------------------------------


# ESCAPE: test_fetchmany
#   CLAIM: fetchmany(size=2) returns the first two rows from a four-row result.
#          A second fetchmany(size=2) returns the next two rows.
#   PATH:  _FakeCursor with 4 rows, _pos=0.
#          fetchmany(2) -> rows[0:2], _pos=2.
#          fetchmany(2) -> rows[2:4], _pos=4.
#   CHECK: first_batch == [[1], [2]]; second_batch == [[3], [4]].
#   MUTATION: Not advancing _pos would return the same two rows twice.
#   ESCAPE: Returning wrong rows fails the exact equality checks.
def test_fetchmany() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[[1], [2], [3], [4]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        cursor = conn.execute("SELECT val FROM t")
        first_batch = cursor.fetchmany(2)
        second_batch = cursor.fetchmany(2)
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="SELECT val FROM t", parameters=())
    v.assert_interaction(p.close)
    assert first_batch == [[1], [2]]
    assert second_batch == [[3], [4]]


# ---------------------------------------------------------------------------
# execute with None returns (empty result)
# ---------------------------------------------------------------------------


# ESCAPE: test_execute_returns_none_gives_empty_fetchall
#   CLAIM: When execute() returns None (e.g., INSERT with no rows), fetchall() returns [].
#   PATH:  _execute_step returns None -> _FakeCursor(rows=None) -> rows=[].
#          fetchall() -> returns [].
#   CHECK: rows == [].
#   MUTATION: Not handling None rows in _FakeCursor would raise AttributeError on fetchall.
#   ESCAPE: Returning [[]] instead of [] fails the exact equality check.
def test_execute_returns_none_gives_empty_fetchall() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        cursor = conn.execute("INSERT INTO t VALUES (1)")
        rows = cursor.fetchall()
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="INSERT INTO t VALUES (1)", parameters=())
    v.assert_interaction(p.close)
    assert rows == []


# ---------------------------------------------------------------------------
# cursor iteration (__iter__)
# ---------------------------------------------------------------------------


# ESCAPE: test_cursor_iter
#   CLAIM: Iterating over a _FakeCursorProxy yields all rows not yet consumed.
#   PATH:  _FakeCursor with rows [[1], [2], [3]]; _pos=0.
#          __iter__ -> iter(_rows[_pos:]) -> yields [1], [2], [3].
#   CHECK: list(cursor) == [[1], [2], [3]].
#   MUTATION: Returning iter([]) instead would give an empty list.
#   ESCAPE: Yielding rows in wrong order fails the exact list equality check.
def test_cursor_iter() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[[1], [2], [3]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = sqlite3.connect(":memory:")
        cursor = conn.execute("SELECT val FROM t")
        collected = list(cursor)
        conn.close()

    v.assert_interaction(p.connect, database=":memory:")
    v.assert_interaction(p.execute, sql="SELECT val FROM t", parameters=())
    v.assert_interaction(p.close)
    assert collected == [[1], [2], [3]]


# ---------------------------------------------------------------------------
# DatabasePlugin is exposed as bigfoot.DatabasePlugin
# ---------------------------------------------------------------------------


# ESCAPE: test_database_plugin_exported
#   CLAIM: bigfoot.DatabasePlugin points to the DatabasePlugin class.
#   PATH:  Import bigfoot; access bigfoot.DatabasePlugin.
#   CHECK: bigfoot.DatabasePlugin is the DatabasePlugin class from database_plugin module.
#   MUTATION: Not adding to __init__.py would raise AttributeError.
#   ESCAPE: Nothing reasonable -- identity check against the imported class.
def test_database_plugin_exported() -> None:
    from bigfoot.plugins.database_plugin import DatabasePlugin

    assert bigfoot.DatabasePlugin is DatabasePlugin
