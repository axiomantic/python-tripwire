"""Unit tests for AsyncSubprocessPlugin."""

from __future__ import annotations

import asyncio

import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import InvalidStateError, UnmockedInteractionError
from bigfoot._state_machine_plugin import ScriptStep
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.async_subprocess_plugin import (
    _ORIGINAL_CREATE_SUBPROCESS_EXEC,
    _ORIGINAL_CREATE_SUBPROCESS_SHELL,
    AsyncSubprocessPlugin,
    _AsyncFakeProcess,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, AsyncSubprocessPlugin]:
    """Return (verifier, plugin) with plugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    AsyncSubprocessPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, AsyncSubprocessPlugin):
            return v, p
    p = AsyncSubprocessPlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore originals if leaked."""
    with AsyncSubprocessPlugin._install_lock:
        AsyncSubprocessPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        AsyncSubprocessPlugin.__new__(AsyncSubprocessPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_install_count() -> None:
    """Ensure AsyncSubprocessPlugin install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield  # type: ignore[misc]
    _reset_install_count()


# ---------------------------------------------------------------------------
# Static interface: _initial_state / _transitions / _unmocked_source_id
# ---------------------------------------------------------------------------


def test_initial_state() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._initial_state() == "created"


def test_transitions_structure() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._transitions() == {
        "spawn": {"created": "running"},
        "communicate": {"running": "terminated"},
        "wait": {"running": "terminated"},
    }


def test_unmocked_source_id() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._unmocked_source_id() == "asyncio:subprocess:spawn"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patches() -> None:
    v, p = _make_verifier_with_plugin()
    assert asyncio.create_subprocess_exec is _ORIGINAL_CREATE_SUBPROCESS_EXEC
    assert asyncio.create_subprocess_shell is _ORIGINAL_CREATE_SUBPROCESS_SHELL
    p.activate()
    assert asyncio.create_subprocess_exec is not _ORIGINAL_CREATE_SUBPROCESS_EXEC
    assert asyncio.create_subprocess_shell is not _ORIGINAL_CREATE_SUBPROCESS_SHELL


def test_deactivate_restores_patches() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert asyncio.create_subprocess_exec is _ORIGINAL_CREATE_SUBPROCESS_EXEC
    assert asyncio.create_subprocess_shell is _ORIGINAL_CREATE_SUBPROCESS_SHELL


def test_reference_counting_nested() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert AsyncSubprocessPlugin._install_count == 2

    p.deactivate()
    assert AsyncSubprocessPlugin._install_count == 1
    assert asyncio.create_subprocess_exec is not _ORIGINAL_CREATE_SUBPROCESS_EXEC

    p.deactivate()
    assert AsyncSubprocessPlugin._install_count == 0
    assert asyncio.create_subprocess_exec is _ORIGINAL_CREATE_SUBPROCESS_EXEC
    assert asyncio.create_subprocess_shell is _ORIGINAL_CREATE_SUBPROCESS_SHELL


# ---------------------------------------------------------------------------
# Basic create_subprocess_exec: spawn step
# ---------------------------------------------------------------------------


async def test_exec_spawn_step_consumed() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)

    with v.sandbox():
        proc = await asyncio.create_subprocess_exec("ls", "-la")

    v.assert_interaction(p.spawn, command=["ls", "-la"], stdin=None)

    assert isinstance(proc, _AsyncFakeProcess)
    assert proc.pid == 12345
    assert len(p._active_sessions) == 1
    handle = list(p._active_sessions.values())[0]
    assert handle._state == "running"


# ---------------------------------------------------------------------------
# Basic create_subprocess_shell: spawn step
# ---------------------------------------------------------------------------


async def test_shell_spawn_step_consumed() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)

    with v.sandbox():
        proc = await asyncio.create_subprocess_shell("ls -la | grep foo")

    v.assert_interaction(p.spawn, command="ls -la | grep foo", stdin=None)

    assert isinstance(proc, _AsyncFakeProcess)
    assert proc.pid == 12345
    assert len(p._active_sessions) == 1
    handle = list(p._active_sessions.values())[0]
    assert handle._state == "running"


# ---------------------------------------------------------------------------
# communicate() step
# ---------------------------------------------------------------------------


async def test_communicate_step() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"out", b"err", 0))

    with v.sandbox():
        proc = await asyncio.create_subprocess_exec("cmd")
        stdout, stderr = await proc.communicate()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.communicate, input=None)

    assert stdout == b"out"
    assert stderr == b"err"
    assert proc.returncode == 0
    handle = list(p._active_sessions.values())[0]
    assert handle._state == "terminated"


async def test_communicate_with_stdin_input() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"result", b"", 0))

    with v.sandbox():
        proc = await asyncio.create_subprocess_exec("cmd")
        stdout, stderr = await proc.communicate(input=b"hello")

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.communicate, input=b"hello")

    assert stdout == b"result"
    assert stderr == b""
    assert proc.returncode == 0


async def test_communicate_nonzero_returncode() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"", b"fail output", 1))

    with v.sandbox():
        proc = await asyncio.create_subprocess_exec("cmd")
        stdout, stderr = await proc.communicate()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.communicate, input=None)

    assert stdout == b""
    assert stderr == b"fail output"
    assert proc.returncode == 1


# ---------------------------------------------------------------------------
# wait() step
# ---------------------------------------------------------------------------


async def test_wait_step() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("wait", returns=42)

    with v.sandbox():
        proc = await asyncio.create_subprocess_exec("cmd")
        wait_result = await proc.wait()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.wait)

    assert wait_result == 42
    assert proc.returncode == 42
    handle = list(p._active_sessions.values())[0]
    assert handle._state == "terminated"


async def test_wait_is_idempotent() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("wait", returns=7)

    with v.sandbox():
        proc = await asyncio.create_subprocess_exec("cmd")
        first = await proc.wait()
        second = await proc.wait()
        third = await proc.wait()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.wait)

    assert first == 7
    assert second == 7
    assert third == 7
    assert proc.returncode == 7


# ---------------------------------------------------------------------------
# InvalidStateError: communicate after terminate
# ---------------------------------------------------------------------------


async def test_communicate_twice_raises_invalid_state() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"out", b"", 0))

    with v.sandbox():
        proc = await asyncio.create_subprocess_exec("cmd")
        await proc.communicate()
        with pytest.raises(InvalidStateError) as exc_info:
            await proc.communicate()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.communicate, input=None)

    exc = exc_info.value
    assert exc.method == "communicate"
    assert exc.current_state == "terminated"
    assert exc.valid_states == frozenset({"running"})


# ---------------------------------------------------------------------------
# UnmockedInteractionError when no session queued
# ---------------------------------------------------------------------------


async def test_exec_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            await asyncio.create_subprocess_exec("cmd")

    assert exc_info.value.source_id == "asyncio:subprocess:spawn"


async def test_shell_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            await asyncio.create_subprocess_shell("cmd")

    assert exc_info.value.source_id == "asyncio:subprocess:spawn"


# ---------------------------------------------------------------------------
# get_unused_mocks: unconsumed steps
# ---------------------------------------------------------------------------


async def test_get_unused_mocks_unconsumed_steps() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"", b"", 0))

    with v.sandbox():
        await asyncio.create_subprocess_exec("cmd")

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].method == "communicate"


def test_get_unused_mocks_queued_session_never_bound() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"", b"", 0))

    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "spawn"
    assert unused[1].method == "communicate"


# ---------------------------------------------------------------------------
# assertable_fields
# ---------------------------------------------------------------------------


async def test_assertable_fields_spawn() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)

    with v.sandbox():
        await asyncio.create_subprocess_exec("ls", "-la")

    interactions = v._timeline._interactions
    assert len(interactions) == 1
    fields = p.assertable_fields(interactions[0])
    assert fields == frozenset({"command", "stdin"})

    v.assert_interaction(p.spawn, command=["ls", "-la"], stdin=None)


async def test_assertable_fields_communicate() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"", b"", 0))

    with v.sandbox():
        proc = await asyncio.create_subprocess_exec("cmd")
        await proc.communicate()

    interactions = v._timeline._interactions
    assert len(interactions) == 2
    fields = p.assertable_fields(interactions[1])
    assert fields == frozenset({"input"})

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.communicate, input=None)


async def test_assertable_fields_wait() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("wait", returns=0)

    with v.sandbox():
        proc = await asyncio.create_subprocess_exec("cmd")
        await proc.wait()

    interactions = v._timeline._interactions
    assert len(interactions) == 2
    fields = p.assertable_fields(interactions[1])
    assert fields == frozenset()

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)
    v.assert_interaction(p.wait)


# ---------------------------------------------------------------------------
# Multiple sessions (sequential subprocess calls)
# ---------------------------------------------------------------------------


async def test_multiple_sessions() -> None:
    v, p = _make_verifier_with_plugin()

    session1 = p.new_session()
    session1.expect("spawn", returns=None)
    session1.expect("communicate", returns=(b"out1", b"", 0))

    session2 = p.new_session()
    session2.expect("spawn", returns=None)
    session2.expect("communicate", returns=(b"out2", b"", 0))

    with v.sandbox():
        proc1 = await asyncio.create_subprocess_exec("cmd1")
        stdout1, _ = await proc1.communicate()
        proc2 = await asyncio.create_subprocess_exec("cmd2")
        stdout2, _ = await proc2.communicate()

    v.assert_interaction(p.spawn, command=["cmd1"], stdin=None)
    v.assert_interaction(p.communicate, input=None)
    v.assert_interaction(p.spawn, command=["cmd2"], stdin=None)
    v.assert_interaction(p.communicate, input=None)

    assert stdout1 == b"out1"
    assert stdout2 == b"out2"


# ---------------------------------------------------------------------------
# format_interaction / format_assert_hint / format_mock_hint
# ---------------------------------------------------------------------------


async def test_format_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"", b"", 0))
    session.expect("wait", returns=0, required=False)

    with v.sandbox():
        proc = await asyncio.create_subprocess_exec("ls", "-la")
        await proc.communicate()

    interactions = v._timeline._interactions
    spawn_fmt = p.format_interaction(interactions[0])
    assert "[AsyncSubprocessPlugin]" in spawn_fmt
    assert "spawn" in spawn_fmt
    assert "['ls', '-la']" in spawn_fmt

    comm_fmt = p.format_interaction(interactions[1])
    assert "[AsyncSubprocessPlugin]" in comm_fmt
    assert "communicate" in comm_fmt

    v.assert_interaction(p.spawn, command=["ls", "-la"], stdin=None)
    v.assert_interaction(p.communicate, input=None)


async def test_format_assert_hint() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)

    with v.sandbox():
        await asyncio.create_subprocess_exec("cmd")

    interactions = v._timeline._interactions
    hint = p.format_assert_hint(interactions[0])
    assert "async_subprocess_mock" in hint
    assert "assert_spawn" in hint

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)


async def test_format_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)

    with v.sandbox():
        await asyncio.create_subprocess_exec("cmd")

    interactions = v._timeline._interactions
    hint = p.format_mock_hint(interactions[0])
    assert "async_subprocess_mock" in hint
    assert "spawn" in hint

    v.assert_interaction(p.spawn, command=["cmd"], stdin=None)


# ---------------------------------------------------------------------------
# format_unmocked_hint
# ---------------------------------------------------------------------------


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    hint = p.format_unmocked_hint("asyncio:subprocess:spawn", (), {})
    assert "asyncio.create_subprocess" in hint
    assert "async_subprocess_mock" in hint


# ---------------------------------------------------------------------------
# format_unused_mock_hint
# ---------------------------------------------------------------------------


def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("spawn", returns=None)
    unused = p.get_unused_mocks()
    assert len(unused) == 1
    hint = p.format_unused_mock_hint(unused[0])
    assert "spawn" in hint
    assert "mocked" in hint


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.async_subprocess_mock
# ---------------------------------------------------------------------------


def test_async_subprocess_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    from bigfoot._state_machine_plugin import SessionHandle

    session = bigfoot.async_subprocess_mock.new_session()
    assert isinstance(session, SessionHandle)
    result = session.expect("spawn", returns=None, required=False)
    assert result is session


def test_async_subprocess_mock_proxy_raises_outside_context() -> None:
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.async_subprocess_mock.new_session
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# ConflictError: foreign patch
# ---------------------------------------------------------------------------


def test_conflict_error_exec_already_patched() -> None:
    from unittest.mock import MagicMock

    from bigfoot._errors import ConflictError

    v, p = _make_verifier_with_plugin()
    foreign_patch = MagicMock()
    original = asyncio.create_subprocess_exec
    try:
        asyncio.create_subprocess_exec = foreign_patch  # type: ignore[assignment]
        with pytest.raises(ConflictError) as exc_info:
            p.activate()
        assert exc_info.value.target == "asyncio.create_subprocess_exec"
    finally:
        asyncio.create_subprocess_exec = original  # type: ignore[assignment]


def test_conflict_error_shell_already_patched() -> None:
    from unittest.mock import MagicMock

    from bigfoot._errors import ConflictError

    v, p = _make_verifier_with_plugin()
    foreign_patch = MagicMock()
    original = asyncio.create_subprocess_shell
    try:
        asyncio.create_subprocess_shell = foreign_patch  # type: ignore[assignment]
        with pytest.raises(ConflictError) as exc_info:
            p.activate()
        assert exc_info.value.target == "asyncio.create_subprocess_shell"
    finally:
        asyncio.create_subprocess_shell = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Assertion helpers via proxy
# ---------------------------------------------------------------------------


async def test_assertion_helpers_via_proxy(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.async_subprocess_mock.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"output", b"", 0))

    with bigfoot.sandbox():
        proc = await asyncio.create_subprocess_exec("make", "all")
        stdout, stderr = await proc.communicate()

    bigfoot.async_subprocess_mock.assert_spawn(command=["make", "all"], stdin=None)
    bigfoot.async_subprocess_mock.assert_communicate(input=None)

    assert stdout == b"output"
    assert stderr == b""
    assert proc.returncode == 0


async def test_assertion_helpers_wait_via_proxy(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.async_subprocess_mock.new_session()
    session.expect("spawn", returns=None)
    session.expect("wait", returns=0)

    with bigfoot.sandbox():
        proc = await asyncio.create_subprocess_exec("cmd")
        await proc.wait()

    bigfoot.async_subprocess_mock.assert_spawn(command=["cmd"], stdin=None)
    bigfoot.async_subprocess_mock.assert_wait()


# ---------------------------------------------------------------------------
# Full session via sandbox
# ---------------------------------------------------------------------------


async def test_full_session_via_sandbox(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.async_subprocess_mock.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"build output", b"", 0))

    with bigfoot.sandbox():
        proc = await asyncio.create_subprocess_exec("make", "all")
        stdout, stderr = await proc.communicate()

    bigfoot.async_subprocess_mock.assert_spawn(command=["make", "all"], stdin=None)
    bigfoot.async_subprocess_mock.assert_communicate(input=None)

    assert stdout == b"build output"
    assert stderr == b""
    assert proc.returncode == 0


async def test_full_shell_session_via_sandbox(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.async_subprocess_mock.new_session()
    session.expect("spawn", returns=None)
    session.expect("communicate", returns=(b"shell output", b"", 0))

    with bigfoot.sandbox():
        proc = await asyncio.create_subprocess_shell("echo hello | tr a-z A-Z")
        stdout, stderr = await proc.communicate()

    bigfoot.async_subprocess_mock.assert_spawn(
        command="echo hello | tr a-z A-Z", stdin=None
    )
    bigfoot.async_subprocess_mock.assert_communicate(input=None)

    assert stdout == b"shell output"
    assert stderr == b""
    assert proc.returncode == 0
