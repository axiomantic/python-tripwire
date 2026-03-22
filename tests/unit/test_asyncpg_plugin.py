"""Unit tests for AsyncpgPlugin."""

from __future__ import annotations

asyncpg = __import__("pytest").importorskip("asyncpg")

import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import UnmockedInteractionError
from bigfoot._state_machine_plugin import ScriptStep
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.asyncpg_plugin import AsyncpgPlugin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, AsyncpgPlugin]:
    """Return (verifier, plugin) with plugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    AsyncpgPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, AsyncpgPlugin):
            return v, p
    p = AsyncpgPlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with AsyncpgPlugin._install_lock:
        AsyncpgPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        AsyncpgPlugin.__new__(AsyncpgPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_install_count():
    """Ensure AsyncpgPlugin install count starts and ends at 0 for every test."""
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
        "execute": {"connected": "connected"},
        "fetch": {"connected": "connected"},
        "fetchrow": {"connected": "connected"},
        "fetchval": {"connected": "connected"},
        "close": {"connected": "closed"},
    }


def test_unmocked_source_id() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._unmocked_source_id() == "asyncpg:connect"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patch() -> None:
    v, p = _make_verifier_with_plugin()
    original_connect = asyncpg.connect
    p.activate()
    assert asyncpg.connect is not original_connect
    p.deactivate()


def test_deactivate_restores_patch() -> None:
    v, p = _make_verifier_with_plugin()
    original_connect = asyncpg.connect
    p.activate()
    p.deactivate()
    assert asyncpg.connect is original_connect


def test_reference_counting_nested() -> None:
    v, p = _make_verifier_with_plugin()
    original_connect = asyncpg.connect
    p.activate()
    p.activate()
    assert AsyncpgPlugin._install_count == 2

    p.deactivate()
    assert AsyncpgPlugin._install_count == 1
    assert asyncpg.connect is not original_connect

    p.deactivate()
    assert AsyncpgPlugin._install_count == 0
    assert asyncpg.connect is original_connect


# ---------------------------------------------------------------------------
# Basic session: execute
# ---------------------------------------------------------------------------


async def test_basic_execute() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns="INSERT 0 1")
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        result = await conn.execute("INSERT INTO users (name) VALUES ($1)", "Alice")
        await conn.close()

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.execute, query="INSERT INTO users (name) VALUES ($1)", args=["Alice"])
    v.assert_interaction(p.close)
    assert result == "INSERT 0 1"


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


async def test_fetch() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("fetch", returns=[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        rows = await conn.fetch("SELECT id, name FROM users")
        await conn.close()

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.fetch, query="SELECT id, name FROM users", args=[])
    v.assert_interaction(p.close)
    assert rows == [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]


# ---------------------------------------------------------------------------
# fetchrow
# ---------------------------------------------------------------------------


async def test_fetchrow() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("fetchrow", returns={"id": 1, "name": "Alice"})
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        row = await conn.fetchrow("SELECT id, name FROM users WHERE id = $1", 1)
        await conn.close()

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.fetchrow, query="SELECT id, name FROM users WHERE id = $1", args=[1])
    v.assert_interaction(p.close)
    assert row == {"id": 1, "name": "Alice"}


# ---------------------------------------------------------------------------
# fetchrow returns None
# ---------------------------------------------------------------------------


async def test_fetchrow_returns_none() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("fetchrow", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        row = await conn.fetchrow("SELECT id FROM users WHERE id = $1", 999)
        await conn.close()

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.fetchrow, query="SELECT id FROM users WHERE id = $1", args=[999])
    v.assert_interaction(p.close)
    assert row is None


# ---------------------------------------------------------------------------
# fetchval
# ---------------------------------------------------------------------------


async def test_fetchval() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("fetchval", returns=42)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        val = await conn.fetchval("SELECT count(*) FROM users")
        await conn.close()

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.fetchval, query="SELECT count(*) FROM users", args=[])
    v.assert_interaction(p.close)
    assert val == 42


# ---------------------------------------------------------------------------
# close releases session
# ---------------------------------------------------------------------------


async def test_close_releases_session() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns="SELECT 1")
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        await conn.execute("SELECT 1")
        await conn.close()

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.execute, query="SELECT 1", args=[])
    v.assert_interaction(p.close)
    assert len(p._active_sessions) == 0
    assert p.get_unused_mocks() == []


# ---------------------------------------------------------------------------
# InvalidStateError: close from disconnected
# ---------------------------------------------------------------------------


async def test_execute_after_close_raises_invalid_state() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)
    session.expect("execute", returns="")  # should never be reached

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        await conn.close()
        with pytest.raises(UnmockedInteractionError):
            # After close + _release_session, lookup should fail
            await conn.execute("SELECT 1")

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# get_unused_mocks: unconsumed required steps
# ---------------------------------------------------------------------------


async def test_get_unused_mocks_returns_unconsumed_steps() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns="")
    session.expect("fetch", returns=[])  # will NOT be consumed

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        await conn.execute("INSERT INTO t VALUES (1)")
        # deliberately NOT calling fetch or close

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.execute, query="INSERT INTO t VALUES (1)", args=[])
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].method == "fetch"


def test_get_unused_mocks_queued_session() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("execute", returns="")
    session.expect("close", returns=None)

    # Never call asyncpg.connect; the session stays in the queue
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "execute"
    assert unused[1].method == "close"


# ---------------------------------------------------------------------------
# UnmockedInteractionError when no session queued
# ---------------------------------------------------------------------------


async def test_connect_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            await asyncpg.connect(host="localhost")

    assert exc_info.value.source_id == "asyncpg:connect"


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.asyncpg_mock
# ---------------------------------------------------------------------------


def test_asyncpg_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    from bigfoot._state_machine_plugin import SessionHandle

    session = bigfoot.asyncpg_mock.new_session()
    assert isinstance(session, SessionHandle)
    result = session.expect("execute", returns="", required=False)
    assert result is session


def test_asyncpg_mock_proxy_raises_outside_context() -> None:
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.asyncpg_mock.new_session
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# assertable_fields per step type
# ---------------------------------------------------------------------------


async def test_assertable_fields_connect() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        await conn.close()

    timeline = v._timeline
    interactions = list(timeline._interactions)
    connect_interaction = interactions[0]
    assert connect_interaction.source_id == "asyncpg:connect"
    fields = p.assertable_fields(connect_interaction)
    assert "host" in fields
    assert "database" in fields
    assert "user" in fields

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.close)


async def test_assertable_fields_execute() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("execute", returns="")
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        await conn.execute("SELECT 1")
        await conn.close()

    timeline = v._timeline
    interactions = list(timeline._interactions)
    execute_interaction = interactions[1]
    assert execute_interaction.source_id == "asyncpg:execute"
    fields = p.assertable_fields(execute_interaction)
    assert fields == frozenset({"query", "args"})

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.execute, query="SELECT 1", args=[])
    v.assert_interaction(p.close)


async def test_assertable_fields_fetch() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("fetch", returns=[])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        await conn.fetch("SELECT 1")
        await conn.close()

    timeline = v._timeline
    interactions = list(timeline._interactions)
    fetch_interaction = interactions[1]
    assert fetch_interaction.source_id == "asyncpg:fetch"
    fields = p.assertable_fields(fetch_interaction)
    assert fields == frozenset({"query", "args"})

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.fetch, query="SELECT 1", args=[])
    v.assert_interaction(p.close)


async def test_assertable_fields_close_empty() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        await conn.close()

    timeline = v._timeline
    interactions = list(timeline._interactions)
    close_interaction = interactions[1]
    assert p.assertable_fields(close_interaction) == frozenset()

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# format hints
# ---------------------------------------------------------------------------


async def test_format_assert_hint_connect() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        await conn.close()

    interactions = list(v._timeline._interactions)
    hint = p.format_assert_hint(interactions[0])
    assert "assert_connect" in hint
    assert "host" in hint

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.close)


async def test_format_assert_hint_fetch() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("fetch", returns=[])
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect(host="localhost", database="testdb", user="admin")
        await conn.fetch("SELECT 1")
        await conn.close()

    interactions = list(v._timeline._interactions)
    hint = p.format_assert_hint(interactions[1])
    assert "assert_fetch" in hint
    assert "SELECT 1" in hint

    v.assert_interaction(p.connect, host="localhost", database="testdb", user="admin")
    v.assert_interaction(p.fetch, query="SELECT 1", args=[])
    v.assert_interaction(p.close)


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    hint = p.format_unmocked_hint("asyncpg:connect", (), {})
    assert "asyncpg.connect" in hint
    assert "new_session" in hint


# ---------------------------------------------------------------------------
# Multiple sessions
# ---------------------------------------------------------------------------


async def test_multiple_sessions() -> None:
    v, p = _make_verifier_with_plugin()

    s1 = p.new_session()
    s1.expect("connect", returns=None)
    s1.expect("fetch", returns=[{"id": 1}])
    s1.expect("close", returns=None)

    s2 = p.new_session()
    s2.expect("connect", returns=None)
    s2.expect("fetch", returns=[{"id": 2}])
    s2.expect("close", returns=None)

    with v.sandbox():
        conn1 = await asyncpg.connect(host="host1", database="db1", user="u")
        rows1 = await conn1.fetch("SELECT 1")
        await conn1.close()

        conn2 = await asyncpg.connect(host="host2", database="db2", user="u")
        rows2 = await conn2.fetch("SELECT 2")
        await conn2.close()

    v.assert_interaction(p.connect, host="host1", database="db1", user="u")
    v.assert_interaction(p.fetch, query="SELECT 1", args=[])
    v.assert_interaction(p.close)
    v.assert_interaction(p.connect, host="host2", database="db2", user="u")
    v.assert_interaction(p.fetch, query="SELECT 2", args=[])
    v.assert_interaction(p.close)

    assert rows1 == [{"id": 1}]
    assert rows2 == [{"id": 2}]


# ---------------------------------------------------------------------------
# Connect with DSN
# ---------------------------------------------------------------------------


async def test_connect_with_dsn() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = await asyncpg.connect("postgresql://admin@localhost/testdb")
        await conn.close()

    v.assert_interaction(p.connect, dsn="postgresql://admin@localhost/testdb")
    v.assert_interaction(p.close)


# ---------------------------------------------------------------------------
# AsyncpgPlugin is exposed as bigfoot.AsyncpgPlugin
# ---------------------------------------------------------------------------


def test_asyncpg_plugin_exported() -> None:
    assert bigfoot.AsyncpgPlugin is AsyncpgPlugin
