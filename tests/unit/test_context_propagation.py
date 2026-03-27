"""Tests for cross-thread ContextVar propagation."""

from __future__ import annotations

import _thread
import concurrent.futures
import contextlib
import contextvars
import sys
import threading
from collections.abc import Generator
from unittest.mock import patch

import pytest

from bigfoot._context_propagation import (
    install_context_propagation,
    uninstall_context_propagation,
)

# Use a fresh ContextVar for isolation from bigfoot's own vars
_test_var: contextvars.ContextVar[str] = contextvars.ContextVar("_test_var", default="unset")

# Python 3.14t (free-threaded) natively inherits context to child threads,
# so tests asserting "no propagation without install" must be skipped.
_NATIVE_THREAD_CONTEXT = getattr(
    getattr(sys, "flags", None), "thread_inherit_context", False
)


@pytest.fixture(autouse=True)
def _ensure_uninstalled() -> Generator[None, None, None]:
    """Ensure context propagation is uninstalled for each test, then restore prior state."""
    import bigfoot._context_propagation as cp
    was_installed = cp._installed
    uninstall_context_propagation()
    yield
    uninstall_context_propagation()
    if was_installed:
        install_context_propagation()


# ---------------------------------------------------------------------------
# _thread.start_new_thread propagation
# ---------------------------------------------------------------------------


class TestThreadPropagation:
    def test_contextvar_propagates_to_child_thread(self) -> None:
        """Child thread sees parent's ContextVar value after install."""
        install_context_propagation()
        token = _test_var.set("from_parent")
        captured: list[str] = []
        event = threading.Event()

        def worker() -> None:
            captured.append(_test_var.get())
            event.set()

        _thread.start_new_thread(worker, ())
        event.wait(timeout=5)
        _test_var.reset(token)

        assert captured == ["from_parent"]

    @pytest.mark.skipif(
        _NATIVE_THREAD_CONTEXT,
        reason="Native context inheritance on free-threaded Python",
    )
    def test_contextvar_does_not_propagate_without_install(self) -> None:
        """Without install, child thread gets default ContextVar value."""
        token = _test_var.set("from_parent")
        captured: list[str] = []
        event = threading.Event()

        def worker() -> None:
            captured.append(_test_var.get())
            event.set()

        _thread.start_new_thread(worker, ())
        event.wait(timeout=5)
        _test_var.reset(token)

        assert captured == ["unset"]

    def test_child_thread_changes_do_not_leak_to_parent(self) -> None:
        """Child thread's ContextVar mutations do not affect parent."""
        install_context_propagation()
        token = _test_var.set("parent_value")
        event = threading.Event()

        def worker() -> None:
            _test_var.set("child_override")
            event.set()

        _thread.start_new_thread(worker, ())
        event.wait(timeout=5)

        assert _test_var.get() == "parent_value"
        _test_var.reset(token)

    def test_threading_thread_propagates_via_thread_patch(self) -> None:
        """threading.Thread uses patched start(), so it gets context."""
        install_context_propagation()
        token = _test_var.set("via_threading")
        captured: list[str] = []

        t = threading.Thread(target=lambda: captured.append(_test_var.get()))
        t.start()
        t.join()
        _test_var.reset(token)

        assert captured == ["via_threading"]

    def test_thread_subclass_with_overridden_run(self) -> None:
        """Thread subclass that overrides run() still gets context.

        Since we patch Thread.start() to wrap run() in a context copy,
        the subclass's run() is called inside the propagated context.
        """
        install_context_propagation()
        token = _test_var.set("subclass_test")
        captured: list[str] = []

        class MyThread(threading.Thread):
            def run(self) -> None:
                captured.append(_test_var.get())

        t = MyThread()
        t.start()
        t.join()
        _test_var.reset(token)

        assert captured == ["subclass_test"]


# ---------------------------------------------------------------------------
# ThreadPoolExecutor propagation
# ---------------------------------------------------------------------------


class TestThreadPoolExecutorPropagation:
    def test_contextvar_propagates_via_executor_submit(self) -> None:
        """ThreadPoolExecutor.submit() propagates ContextVars after install."""
        install_context_propagation()
        token = _test_var.set("pool_parent")

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_test_var.get)
            result = future.result(timeout=5)

        _test_var.reset(token)
        assert result == "pool_parent"

    @pytest.mark.skipif(
        _NATIVE_THREAD_CONTEXT,
        reason="Native context inheritance on free-threaded Python",
    )
    def test_executor_submit_does_not_propagate_without_install(self) -> None:
        """Without install, executor workers get default ContextVar value."""
        token = _test_var.set("pool_parent")

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_test_var.get)
            result = future.result(timeout=5)

        _test_var.reset(token)
        assert result == "unset"

    def test_executor_worker_changes_do_not_leak(self) -> None:
        """Worker ContextVar mutations do not affect parent context."""
        install_context_propagation()
        token = _test_var.set("pool_original")

        def worker() -> str:
            _test_var.set("worker_override")
            return _test_var.get()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            worker_saw = future.result(timeout=5)

        assert worker_saw == "worker_override"
        assert _test_var.get() == "pool_original"
        _test_var.reset(token)

    def test_worker_reuse_gets_independent_snapshots(self) -> None:
        """TPE with max_workers=1: two sequential submits with different
        ContextVar values get independent context snapshots per submit."""
        install_context_propagation()

        token1 = _test_var.set("first_submit")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future1 = pool.submit(_test_var.get)
            result1 = future1.result(timeout=5)

            _test_var.reset(token1)
            token2 = _test_var.set("second_submit")
            future2 = pool.submit(_test_var.get)
            result2 = future2.result(timeout=5)

        _test_var.reset(token2)

        assert result1 == "first_submit"
        assert result2 == "second_submit"


# ---------------------------------------------------------------------------
# Install / uninstall idempotency
# ---------------------------------------------------------------------------


class TestInstallUninstall:
    def test_install_is_idempotent(self) -> None:
        """Calling install twice does not break anything."""
        install_context_propagation()
        install_context_propagation()  # second call is no-op

        token = _test_var.set("idempotent_test")
        captured: list[str] = []

        t = threading.Thread(target=lambda: captured.append(_test_var.get()))
        t.start()
        t.join()
        _test_var.reset(token)

        assert captured == ["idempotent_test"]

    @pytest.mark.skipif(
        _NATIVE_THREAD_CONTEXT,
        reason="Native context inheritance on free-threaded Python",
    )
    def test_uninstall_restores_original_behavior(self) -> None:
        """After uninstall, threads no longer get parent context."""
        install_context_propagation()
        uninstall_context_propagation()

        token = _test_var.set("after_uninstall")
        captured: list[str] = []

        t = threading.Thread(target=lambda: captured.append(_test_var.get()))
        t.start()
        t.join()
        _test_var.reset(token)

        assert captured == ["unset"]

    def test_uninstall_is_idempotent(self) -> None:
        """Calling uninstall when not installed does not raise."""
        uninstall_context_propagation()
        uninstall_context_propagation()  # no-op, no error


# ---------------------------------------------------------------------------
# Bigfoot-specific ContextVar propagation
# ---------------------------------------------------------------------------

from bigfoot._context import (
    _active_verifier,
    _any_order_depth,
    _current_test_verifier,
    _guard_active,
    _guard_level,
    _guard_patches_installed,
)
from bigfoot._recording import _recording_in_progress
from bigfoot.plugins.file_io_plugin import _file_io_bypass


class TestBigfootContextVarsPropagation:
    """Verify all bigfoot ContextVars propagate to child threads."""

    @pytest.mark.parametrize(
        "var,value",
        [
            (_active_verifier, object()),
            (_any_order_depth, 3),
            (_current_test_verifier, object()),
            (_guard_active, True),
            (_guard_level, "error"),
            (_guard_patches_installed, True),
            (_recording_in_progress, True),
            (_file_io_bypass, True),
        ],
        ids=[
            "active_verifier",
            "any_order_depth",
            "current_test_verifier",
            "guard_active",
            "guard_level",
            "guard_patches_installed",
            "recording_in_progress",
            "file_io_bypass",
        ],
    )
    def test_bigfoot_contextvar_propagates_to_thread(
        self,
        var: contextvars.ContextVar[object],
        value: object,
    ) -> None:
        """Each bigfoot ContextVar value is visible in a child thread after install."""
        install_context_propagation()
        token = var.set(value)
        captured: list[object] = []

        def worker() -> None:
            captured.append(var.get())

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        var.reset(token)

        assert captured == [value]


# ---------------------------------------------------------------------------
# Guard mode propagation
# ---------------------------------------------------------------------------

from bigfoot._context import GuardPassThrough, get_verifier_or_raise
from bigfoot._errors import GuardedCallError


class TestGuardModePropagation:
    """Guard mode state propagates correctly to child threads."""

    def test_guard_error_propagates_to_child_thread(self) -> None:
        """When guard is active with level=error, child thread sees it."""
        install_context_propagation()

        with contextlib.ExitStack() as stack:
            stack.callback(_guard_active.reset, _guard_active.set(True))
            stack.callback(_guard_level.reset, _guard_level.set("error"))
            stack.callback(_guard_patches_installed.reset, _guard_patches_installed.set(True))
            errors: list[BaseException] = []

            def worker() -> None:
                try:
                    get_verifier_or_raise("http:request")
                except (GuardedCallError, GuardPassThrough) as exc:
                    errors.append(exc)

            t = threading.Thread(target=worker)
            t.start()
            t.join()

        assert len(errors) == 1
        assert isinstance(errors[0], GuardedCallError)

    def test_guard_firewall_allow_propagates_to_child_thread(self) -> None:
        """When a firewall allow rule matches, child thread passes through."""
        from bigfoot._firewall import (
            Disposition,
            FirewallRule,
            FirewallStack,
            _firewall_stack,
        )
        from bigfoot._firewall_request import HttpFirewallRequest
        from bigfoot._match import M

        install_context_propagation()

        allow_stack = FirewallStack((
            FirewallRule(pattern=M(protocol="http"), disposition=Disposition.ALLOW),
        ))

        with contextlib.ExitStack() as stack:
            stack.callback(_guard_active.reset, _guard_active.set(True))
            stack.callback(_guard_level.reset, _guard_level.set("error"))
            stack.callback(_guard_patches_installed.reset, _guard_patches_installed.set(True))
            stack.callback(_firewall_stack.reset, _firewall_stack.set(allow_stack))
            errors: list[BaseException] = []

            request = HttpFirewallRequest(host="example.com", port=80)

            def worker() -> None:
                try:
                    get_verifier_or_raise("http:request", firewall_request=request)
                except GuardPassThrough as exc:
                    errors.append(exc)

            t = threading.Thread(target=worker)
            t.start()
            t.join()

        assert len(errors) == 1
        assert isinstance(errors[0], GuardPassThrough)


# ---------------------------------------------------------------------------
# Python 3.14 thread_inherit_context detection
# ---------------------------------------------------------------------------


class TestPython314Detection:
    """Verify behavior when sys.flags.thread_inherit_context is True."""

    def test_thread_start_not_patched_when_runtime_handles_it(self) -> None:
        """When sys.flags.thread_inherit_context is True, Thread.start is not
        patched but _thread.start_new_thread IS (it doesn't natively inherit)."""
        import bigfoot._context_propagation as cp

        # Ensure clean state
        uninstall_context_propagation()

        original_start = _thread.start_new_thread
        original_thread_start = threading.Thread.start

        # Mock sys.flags to have thread_inherit_context=True
        mock_flags = type("MockFlags", (), {"thread_inherit_context": True})()
        with patch.object(sys, "flags", mock_flags):
            install_context_propagation()

        # _thread.start_new_thread SHOULD still be patched (no native inheritance)
        assert _thread.start_new_thread is not original_start
        # threading.Thread.start should NOT have been patched (runtime handles it)
        assert threading.Thread.start is original_thread_start
        # TPE.submit SHOULD still be patched
        assert cp._saved_tpe_submit is not None

    def test_tpe_submit_still_patched_when_runtime_handles_threads(self) -> None:
        """TPE.submit is always patched, even when sys.flags.thread_inherit_context is True."""
        uninstall_context_propagation()

        mock_flags = type("MockFlags", (), {"thread_inherit_context": True})()
        with patch.object(sys, "flags", mock_flags):
            install_context_propagation()

        token = _test_var.set("tpe_with_314")

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_test_var.get)
            result = future.result(timeout=5)

        _test_var.reset(token)
        assert result == "tpe_with_314"


# ---------------------------------------------------------------------------
# threading.Thread.start patch verification
# ---------------------------------------------------------------------------


class TestThreadStartPatch:
    """Verify threading.Thread.start is patched and restored correctly."""

    @pytest.fixture(autouse=True)
    def _save_threading_state(self) -> Generator[None, None, None]:
        """Save and restore threading.Thread.start around each test."""
        saved_thread_start = threading.Thread.start
        saved_raw_start = _thread.start_new_thread

        yield

        uninstall_context_propagation()
        threading.Thread.start = saved_thread_start  # type: ignore[method-assign]
        _thread.start_new_thread = saved_raw_start

    @pytest.mark.skipif(
        _NATIVE_THREAD_CONTEXT,
        reason="Thread.start not patched on free-threaded Python (runtime handles it)",
    )
    def test_thread_start_is_patched_and_restored(self) -> None:
        """After install, threading.Thread.start is patched;
        after uninstall it is restored to the original."""
        import bigfoot._context_propagation as cp

        original = threading.Thread.start

        install_context_propagation()

        assert threading.Thread.start is not original
        assert cp._saved_thread_start is original

        uninstall_context_propagation()

        assert threading.Thread.start is original

    def test_context_propagates_via_patched_thread_start(self) -> None:
        """Context propagation works via the patched Thread.start()."""
        install_context_propagation()
        token = _test_var.set("compat_check")
        captured: list[str] = []

        t = threading.Thread(target=lambda: captured.append(_test_var.get()))
        t.start()
        t.join()
        _test_var.reset(token)

        assert captured == ["compat_check"]
