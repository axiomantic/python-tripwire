"""C10 integration tests: contextvar propagation across asyncio + threadpool boundaries.

These tests verify that `with tripwire:` (sandbox) state — carried via
the public ContextVars `_active_verifier` and `_current_sandbox_id` — is
propagated through the standard concurrency primitives so that a worker
thread / task can dispatch through `get_verifier_or_raise(...)` and
reach the active verifier instead of falling through to
`SandboxNotActiveError`.

Boundaries covered:

- T1: ``asyncio.to_thread``
- T2: ``asyncio.create_task``
- T3: ``loop.run_in_executor``
- T4: ``asyncio.gather``
- T5: ``concurrent.futures.ThreadPoolExecutor.submit``
- T6: ``concurrent.futures.ProcessPoolExecutor`` (negative test:
       documented separate-process boundary)

Strategy: each positive test enters `verifier.sandbox()`, schedules a
worker that calls `get_verifier_or_raise(source_id="test:c10")`, and
asserts the worker received the same `StrictVerifier` instance the
parent set. If the ContextVars did not propagate, the worker call
would raise `SandboxNotActiveError` (or `PostSandboxInteractionError`
if the boundary captured a stale id). Either failure mode fails the
test.

For T6 (ProcessPoolExecutor) the assertion is the *opposite*: each
worker is a separate Python process so contextvars cannot cross. The
test asserts one of the two documented boundary outcomes:

(a) the worker raised because tripwire state was not propagated
    (no `_active_verifier`, so the dispatch sees no sandbox), OR
(b) `concurrent.futures.process` raised `PicklingError` (a subclass
    of `pickle.PickleError`) at submit time because some part of the
    payload — for example a closure capturing the parent's verifier
    or a non-picklable intercept hook — could not be marshalled
    across the process boundary.

Either outcome confirms the documented boundary; any other outcome
(e.g. the worker silently succeeded as if the sandbox had crossed)
fails the test.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import multiprocessing
import pickle
import sys
import sysconfig
from typing import Any

import pytest

from tripwire import StrictVerifier
from tripwire._context import _active_verifier, get_verifier_or_raise

pytestmark = pytest.mark.integration

_WIN_FREETHREADED = sys.platform == "win32" and bool(
    sysconfig.get_config_var("Py_GIL_DISABLED")
)


# A neutral source_id that no real plugin owns. Reaches Branch 5
# (SandboxNotActiveError) when no verifier is in scope; otherwise the
# active verifier is returned.
_C10_SOURCE_ID = "test:c10_propagation"


def _check_active_verifier_in_worker() -> StrictVerifier | None:
    """Return the verifier that the dispatch resolves for this context.

    Runs `get_verifier_or_raise(...)` and returns the resolved verifier
    (or re-raises if dispatch concludes there is no active sandbox).
    The worker context is whatever the boundary primitive provides.
    """
    return get_verifier_or_raise(_C10_SOURCE_ID)


# ---------------------------------------------------------------------------
# C10-T1: asyncio.to_thread propagates `with tripwire:` state.
# ---------------------------------------------------------------------------


def test_asyncio_to_thread_propagates() -> None:
    """`asyncio.to_thread(f)` runs `f` in the default executor's thread
    pool, but the active verifier ContextVar must propagate so the
    worker resolves the same verifier the parent set."""

    async def main() -> tuple[StrictVerifier, StrictVerifier]:
        v = StrictVerifier()
        async with v.sandbox():
            parent_seen = _active_verifier.get()
            worker_seen = await asyncio.to_thread(
                _check_active_verifier_in_worker
            )
        assert parent_seen is v
        assert worker_seen is not None
        return v, worker_seen

    v, worker_seen = asyncio.run(main())
    assert worker_seen is v


# ---------------------------------------------------------------------------
# C10-T2: asyncio.create_task propagates `with tripwire:` state.
# ---------------------------------------------------------------------------


def test_asyncio_create_task_propagates() -> None:
    """A task spawned with `asyncio.create_task` inside the sandbox
    captures the active ContextVar set; the dispatch inside the
    coroutine resolves the parent verifier."""

    async def main() -> tuple[StrictVerifier, StrictVerifier]:
        v = StrictVerifier()
        async with v.sandbox():

            async def worker() -> StrictVerifier:
                return _check_active_verifier_in_worker()  # type: ignore[return-value]

            task = asyncio.create_task(worker())
            worker_seen = await task
        return v, worker_seen

    v, worker_seen = asyncio.run(main())
    assert worker_seen is v


# ---------------------------------------------------------------------------
# C10-T3: loop.run_in_executor propagates `with tripwire:` state.
# ---------------------------------------------------------------------------


def test_run_in_executor_propagates() -> None:
    """`loop.run_in_executor(None, f)` schedules `f` on the default
    executor; the active verifier ContextVar must propagate."""

    async def main() -> tuple[StrictVerifier, StrictVerifier]:
        v = StrictVerifier()
        async with v.sandbox():
            loop = asyncio.get_running_loop()
            worker_seen = await loop.run_in_executor(
                None, _check_active_verifier_in_worker
            )
        assert worker_seen is not None
        return v, worker_seen

    v, worker_seen = asyncio.run(main())
    assert worker_seen is v


# ---------------------------------------------------------------------------
# C10-T4: asyncio.gather propagates `with tripwire:` state to each child.
# ---------------------------------------------------------------------------


def test_asyncio_gather_propagates() -> None:
    """`asyncio.gather(...)` schedules each coroutine as a task; each
    child task must inherit the ContextVar set so both dispatch calls
    resolve the parent verifier."""

    async def main() -> tuple[StrictVerifier, StrictVerifier, StrictVerifier]:
        v = StrictVerifier()
        async with v.sandbox():

            async def call_a() -> StrictVerifier:
                return _check_active_verifier_in_worker()  # type: ignore[return-value]

            async def call_b() -> StrictVerifier:
                return _check_active_verifier_in_worker()  # type: ignore[return-value]

            results = await asyncio.gather(call_a(), call_b())
        a, b = results
        return v, a, b

    v, a, b = asyncio.run(main())
    assert a is v
    assert b is v


# ---------------------------------------------------------------------------
# C10-T5: concurrent.futures.ThreadPoolExecutor.submit propagates state.
# ---------------------------------------------------------------------------


def test_threadpool_submit_propagates() -> None:
    """A bare-thread-pool `submit(f).result()` from inside the sandbox
    must propagate the ContextVar set so the worker thread resolves the
    parent verifier."""
    v = StrictVerifier()
    with v.sandbox():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_check_active_verifier_in_worker)
            worker_seen = future.result(timeout=5)

    assert worker_seen is v


# ---------------------------------------------------------------------------
# C10-T6: ProcessPoolExecutor does NOT propagate sandbox state.
#
# The documented boundary: each worker is a separate Python process
# with its own (empty) ContextVar set. Acceptable outcomes:
#
#   (a) submit() succeeds, the worker runs, and dispatch raises
#       because no verifier is in its context, OR
#   (b) submit() raises pickle.PickleError because some part of the
#       payload (the verifier object, an intercept hook, or anything
#       else captured) cannot be pickled across the process boundary.
#
# Anything else — in particular, the parent verifier somehow appearing
# in the child process — would mean the documented boundary has
# shifted, and the test fails.
# ---------------------------------------------------------------------------


def _processpool_worker(_payload: str) -> Any:
    """Top-level picklable worker for ProcessPoolExecutor.

    Runs in a fresh Python interpreter where no `with tripwire:` state
    exists. Calling `get_verifier_or_raise(...)` here must raise the
    standard "no sandbox" error. Returns the resolved verifier on the
    *unexpected* success path so the test can detect a boundary shift.
    """
    return get_verifier_or_raise("test:c10_processpool")


@pytest.mark.skipif(
    _WIN_FREETHREADED,
    reason=(
        "ProcessPoolExecutor spawn deadlocks on Windows free-threaded 3.14 "
        "(upstream CPython multiprocessing/free-threading interaction). The "
        "documented boundary is still validated on every other platform, "
        "including Linux 3.14t."
    ),
)
def test_processpool_does_NOT_propagate() -> None:  # noqa: N802
    """ProcessPoolExecutor submission either fails to pickle the payload
    or executes the worker in a fresh process where no `with tripwire:`
    state exists. Either is the documented boundary."""
    v = StrictVerifier()

    submit_error: BaseException | None = None
    worker_error: BaseException | None = None
    worker_result: Any = None

    # Force the "spawn" start method. On Linux the multiprocessing default
    # is "forkserver" (and on Python 3.14 the forkserver setup creates a
    # Unix-domain socket *in the parent* via socket.socket(AF_UNIX), which
    # tripwire's socket plugin intercepts on close()). Spawning bypasses
    # that parent-side socket dance and makes the boundary explicit: every
    # worker is a fresh interpreter that inherits no tripwire state.
    spawn_ctx = multiprocessing.get_context("spawn")
    with v.sandbox():
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=1, mp_context=spawn_ctx
        ) as pool:
            try:
                future = pool.submit(_processpool_worker, "payload")
            except (pickle.PickleError, TypeError, AttributeError) as exc:
                # `concurrent.futures.process` raises PicklingError (a
                # pickle.PickleError) at submit() if the payload is
                # unpicklable. TypeError / AttributeError are the other
                # observed shapes when pickling intercept hooks.
                submit_error = exc
            else:
                try:
                    worker_result = future.result(timeout=30)
                except BaseException as exc:  # noqa: BLE001
                    worker_error = exc

    if submit_error is not None:
        # Outcome (b): submit failed to pickle. Documented boundary.
        assert isinstance(
            submit_error, (pickle.PickleError, TypeError, AttributeError)
        )
        assert worker_result is None
        assert worker_error is None
        return

    # Outcome (a): submit succeeded. The worker must have raised
    # because no sandbox state was inherited. The exception comes back
    # from .result() either as the original class or wrapped.
    assert worker_error is not None, (
        "ProcessPoolExecutor unexpectedly inherited `with tripwire:` "
        f"state: worker returned {worker_result!r}. The documented "
        "separate-process boundary has shifted; update README and "
        "CHANGELOG."
    )
    assert worker_result is None
