"""Unit tests for Psycopg2Plugin."""

from __future__ import annotations

psycopg2 = __import__("pytest").importorskip("psycopg2")

import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import InvalidStateError, UnmockedInteractionError
from bigfoot._state_machine_plugin import ScriptStep
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.psycopg2_plugin import Psycopg2Plugin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, Psycopg2Plugin]:
    """Return (verifier, plugin) with plugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    Psycopg2Plugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, Psycopg2Plugin):
            return v, p
    p = Psycopg2Plugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with Psycopg2Plugin._install_lock:
        Psycopg2Plugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        Psycopg2Plugin.__new__(Psycopg2Plugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_install_count():
    """Ensure Psycopg2Plugin install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield
    _reset_install_count()


# ---------------------------------------------------------------------------
# Static interface: _initial_state / _transitions / _unmocked_source_id
# ---------------------------------------------------------------------------


def test_initial_state() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._initial_state() == "disconnected"


def test_transitions_structure() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._transitions() == {
        "connect": {"disconnected": "connected"},
        "execute": {"connected": "in_transaction", "in_transaction": "in_transaction"},
        "commit": {"in_transaction": "connected"},
        "rollback": {"in_transaction": "connected"},
        "close": {"connected": "closed", "in_transaction": "closed"},
    }


def test_unmocked_source_id() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._unmocked_source_id() == "psycopg2:connect"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patch() -> None:
    v, p = _make_verifier_with_plugin()
    original_connect = psycopg2.connect
    p.activate()
    assert psycopg2.connect is not original_connect
    p.deactivate()


def test_deactivate_restores_patch() -> None:
    v, p = _make_verifier_with_plugin()
    original_connect = psycopg2.connect
    p.activate()
    p.deactivate()
    assert psycopg2.connect is original_connect


def test_reference_counting_nested() -> None:
    v, p = _make_verifier_with_plugin()
    original_connect = psycopg2.connect
    p.activate()
    p.activate()
    assert Psycopg2Plugin._install_count == 2

    p.deactivate()
    assert Psycopg2Plugin._install_count == 1
    assert psycopg2.connect is not original_connect

    p.deactivate()
    assert Psycopg2Plugin._install_count == 0
    assert psycopg2.connect is original_connect


# ---------------------------------------------------------------------------
# Basic session: cursor.execute() + fetchall
# ---------------------------------------------------------------------------


def test_basic_cursor_execute_fetchall() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[[1, "Alice"], [2, "Bob"]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM users")
        rows = cur.fetchall()
        conn.close()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="SELECT id, name FROM users", parameters=None)
    v.assert_interaction(p.close)
    assert rows == [[1, "Alice"], [2, "Bob"]]


# ---------------------------------------------------------------------------
# cursor.execute() + fetchone
# ---------------------------------------------------------------------------


def test_cursor_execute_fetchone() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[[1, "Alice"], [2, "Bob"]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM users")
        first = cur.fetchone()
        second = cur.fetchone()
        conn.close()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="SELECT id, name FROM users", parameters=None)
    v.assert_interaction(p.close)
    assert first == [1, "Alice"]
    assert second == [2, "Bob"]


# ---------------------------------------------------------------------------
# fetchmany
# ---------------------------------------------------------------------------


def test_fetchmany() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[[1], [2], [3], [4]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("SELECT val FROM t")
        first_batch = cur.fetchmany(2)
        second_batch = cur.fetchmany(2)
        conn.close()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="SELECT val FROM t", parameters=None)
    v.assert_interaction(p.close)
    assert first_batch == [[1], [2]]
    assert second_batch == [[3], [4]]


# ---------------------------------------------------------------------------
# commit state transition
# ---------------------------------------------------------------------------


def test_commit_state_transition() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("commit", returns=None)
    session.expect("execute", returns=[])  # only valid if commit reset state to "connected"
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        cur.execute("INSERT INTO t VALUES (2)")
        conn.close()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="INSERT INTO t VALUES (1)", parameters=None)
    v.assert_interaction(p.commit)
    v.assert_interaction(p.execute, sql="INSERT INTO t VALUES (2)", parameters=None)
    v.assert_interaction(p.close)
    assert p.get_unused_mocks() == []


# ---------------------------------------------------------------------------
# rollback state transition
# ---------------------------------------------------------------------------


def test_rollback_state_transition() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("rollback", returns=None)
    session.expect("execute", returns=[])  # only valid if rollback reset state
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("INSERT INTO t VALUES (1)")
        conn.rollback()
        cur.execute("INSERT INTO t VALUES (2)")
        conn.close()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="INSERT INTO t VALUES (1)", parameters=None)
    v.assert_interaction(p.rollback)
    v.assert_interaction(p.execute, sql="INSERT INTO t VALUES (2)", parameters=None)
    v.assert_interaction(p.close)
    assert p.get_unused_mocks() == []


# ---------------------------------------------------------------------------
# close() releases session
# ---------------------------------------------------------------------------


def test_close_releases_session() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="SELECT 1", parameters=None)
    v.assert_interaction(p.close)
    assert len(p._active_sessions) == 0
    assert p.get_unused_mocks() == []


# ---------------------------------------------------------------------------
# InvalidStateError: commit before execute
# ---------------------------------------------------------------------------


def test_commit_before_execute_raises_invalid_state() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        with pytest.raises(InvalidStateError) as exc_info:
            conn.commit()
        conn.close()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.close)
    exc = exc_info.value
    assert exc.source_id == "psycopg2:commit"
    assert exc.method == "commit"
    assert exc.current_state == "connected"
    assert exc.valid_states == frozenset({"in_transaction"})


# ---------------------------------------------------------------------------
# get_unused_mocks: unconsumed required steps
# ---------------------------------------------------------------------------


def test_get_unused_mocks_returns_unconsumed_steps() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("commit", returns=None)  # will NOT be consumed

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("SELECT 1")
        # deliberately NOT calling commit or close

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="SELECT 1", parameters=None)
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].method == "commit"


def test_get_unused_mocks_queued_session() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("execute", returns=[])
    session.expect("close", returns=None)

    # Never call psycopg2.connect; the session stays in the queue
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "execute"
    assert unused[1].method == "close"


# ---------------------------------------------------------------------------
# UnmockedInteractionError when no session queued
# ---------------------------------------------------------------------------


def test_connect_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            psycopg2.connect(dsn="dbname=test")

    assert exc_info.value.source_id == "psycopg2:connect"


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.psycopg2_mock
# ---------------------------------------------------------------------------


def test_psycopg2_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    from bigfoot._state_machine_plugin import SessionHandle

    session = bigfoot.psycopg2_mock.new_session()
    assert isinstance(session, SessionHandle)
    result = session.expect("execute", returns=[], required=False)
    assert result is session


def test_psycopg2_mock_proxy_raises_outside_context() -> None:
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.psycopg2_mock.new_session
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# fetchone exhaustion returns None
# ---------------------------------------------------------------------------


def test_fetchone_exhaustion_returns_none() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[[42]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("SELECT val FROM t")
        first = cur.fetchone()
        second = cur.fetchone()
        conn.close()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="SELECT val FROM t", parameters=None)
    v.assert_interaction(p.close)
    assert first == [42]
    assert second is None


# ---------------------------------------------------------------------------
# cursor iteration (__iter__)
# ---------------------------------------------------------------------------


def test_cursor_iter() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[[1], [2], [3]])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("SELECT val FROM t")
        collected = list(cur)
        conn.close()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="SELECT val FROM t", parameters=None)
    v.assert_interaction(p.close)
    assert collected == [[1], [2], [3]]


# ---------------------------------------------------------------------------
# Connect with kwargs (host, port, dbname, user)
# ---------------------------------------------------------------------------


def test_connect_with_kwargs() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(host="localhost", port=5432, dbname="mydb", user="admin")
        conn.close()

    v.assert_interaction(
        p.connect, host="localhost", port=5432, dbname="mydb", user="admin"
    )
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# assertable_fields per step type
# ---------------------------------------------------------------------------


def test_assertable_fields_connect_dsn() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        conn.close()

    timeline = v._timeline
    interactions = list(timeline._interactions)
    connect_interaction = interactions[0]
    assert connect_interaction.source_id == "psycopg2:connect"
    fields = p.assertable_fields(connect_interaction)
    assert "dsn" in fields

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.close)


def test_assertable_fields_execute() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()

    timeline = v._timeline
    interactions = list(timeline._interactions)
    execute_interaction = interactions[1]
    assert execute_interaction.source_id == "psycopg2:execute"
    fields = p.assertable_fields(execute_interaction)
    assert fields == frozenset({"sql", "parameters"})

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="SELECT 1", parameters=None)
    v.assert_interaction(p.close)


def test_assertable_fields_commit_rollback_close_empty() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("commit", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()

    timeline = v._timeline
    interactions = list(timeline._interactions)
    commit_interaction = interactions[2]
    close_interaction = interactions[3]
    assert p.assertable_fields(commit_interaction) == frozenset()
    assert p.assertable_fields(close_interaction) == frozenset()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="INSERT INTO t VALUES (1)", parameters=None)
    v.assert_interaction(p.commit)
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# format hints
# ---------------------------------------------------------------------------


def test_format_assert_hint_connect() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        conn.close()

    interactions = list(v._timeline._interactions)
    hint = p.format_assert_hint(interactions[0])
    assert "assert_connect" in hint
    assert "dsn" in hint

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.close)


def test_format_assert_hint_execute() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()

    interactions = list(v._timeline._interactions)
    hint = p.format_assert_hint(interactions[1])
    assert "assert_execute" in hint
    assert "SELECT 1" in hint

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(p.execute, sql="SELECT 1", parameters=None)
    v.assert_interaction(p.close)


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    hint = p.format_unmocked_hint("psycopg2:connect", (), {})
    assert "psycopg2.connect" in hint
    assert "new_session" in hint


# ---------------------------------------------------------------------------
# Multiple sessions
# ---------------------------------------------------------------------------


def test_multiple_sessions() -> None:
    v, p = _make_verifier_with_plugin()

    # Session 1
    s1 = p.new_session()
    s1.expect("connect", returns=None)
    s1.expect("execute", returns=[[1]])
    s1.expect("close", returns=None)

    # Session 2
    s2 = p.new_session()
    s2.expect("connect", returns=None)
    s2.expect("execute", returns=[[2]])
    s2.expect("close", returns=None)

    with v.sandbox():
        conn1 = psycopg2.connect(dsn="db1")
        cur1 = conn1.cursor()
        cur1.execute("SELECT 1")
        rows1 = cur1.fetchall()
        conn1.close()

        conn2 = psycopg2.connect(dsn="db2")
        cur2 = conn2.cursor()
        cur2.execute("SELECT 2")
        rows2 = cur2.fetchall()
        conn2.close()

    v.assert_interaction(p.connect, dsn="db1")
    v.assert_interaction(p.execute, sql="SELECT 1", parameters=None)
    v.assert_interaction(p.close)
    v.assert_interaction(p.connect, dsn="db2")
    v.assert_interaction(p.execute, sql="SELECT 2", parameters=None)
    v.assert_interaction(p.close)

    assert rows1 == [[1]]
    assert rows2 == [[2]]


# ---------------------------------------------------------------------------
# execute with params
# ---------------------------------------------------------------------------


def test_execute_with_params() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns=[])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = psycopg2.connect(dsn="dbname=test")
        cur = conn.cursor()
        cur.execute("INSERT INTO users (name) VALUES (%s)", ("Alice",))
        conn.close()

    v.assert_interaction(p.connect, dsn="dbname=test")
    v.assert_interaction(
        p.execute,
        sql="INSERT INTO users (name) VALUES (%s)",
        parameters=("Alice",),
    )
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# Psycopg2Plugin is exposed as bigfoot.Psycopg2Plugin
# ---------------------------------------------------------------------------


def test_psycopg2_plugin_exported() -> None:
    assert bigfoot.Psycopg2Plugin is Psycopg2Plugin
