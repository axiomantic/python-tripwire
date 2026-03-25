"""Tests for cross-thread ContextVar propagation."""

from __future__ import annotations

import _thread
import concurrent.futures
import contextvars
import sys
import threading
from unittest.mock import patch

import pytest

from bigfoot._context_propagation import (
    install_context_propagation,
    uninstall_context_propagation,
)

# Use a fresh ContextVar for isolation from bigfoot's own vars
_test_var: contextvars.ContextVar[str] = contextvars.ContextVar("_test_var", default="unset")


@pytest.fixture(autouse=True)
def _ensure_uninstalled() -> None:
    """Ensure context propagation is uninstalled for each test, then restore prior state."""
    import bigfoot._context_propagation as cp
    was_installed = cp._installed
    uninstall_context_propagation()
    yield  # type: ignore[misc]
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
        """threading.Thread uses _thread.start_new_thread under the hood, so it gets context."""
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

        Since we patch at the _thread level, the subclass's run() is called
        inside the propagated context automatically.
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
    _guard_active,
    _guard_allowlist,
    _guard_level,
    _guard_patches_installed,
)
from bigfoot._recording import _recording_in_progress
from bigfoot.plugins.file_io_plugin import _file_io_bypass


class TestBigfootContextVarsPropagation:
    """Verify all 9 bigfoot ContextVars propagate to child threads."""

    @pytest.mark.parametrize(
        "var,value",
        [
            (_active_verifier, object()),
            (_guard_active, True),
            (_guard_allowlist, frozenset({"http", "socket"})),
            (_guard_level, "error"),
            (_guard_patches_installed, True),
            (_recording_in_progress, True),
            (_file_io_bypass, True),
        ],
        ids=[
            "active_verifier",
            "guard_active",
            "guard_allowlist",
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

from bigfoot._context import get_verifier_or_raise, GuardPassThrough
from bigfoot._errors import GuardedCallError


class TestGuardModePropagation:
    """Guard mode state propagates correctly to child threads."""

    def test_guard_error_propagates_to_child_thread(self) -> None:
        """When guard is active with level=error, child thread sees it."""
        install_context_propagation()

        guard_token = _guard_active.set(True)
        level_token = _guard_level.set("error")
        patches_token = _guard_patches_installed.set(True)
        allowlist_token = _guard_allowlist.set(frozenset())
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                get_verifier_or_raise("http:request")
            except (GuardedCallError, GuardPassThrough) as exc:
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        _guard_active.reset(guard_token)
        _guard_level.reset(level_token)
        _guard_patches_installed.reset(patches_token)
        _guard_allowlist.reset(allowlist_token)

        assert len(errors) == 1
        assert isinstance(errors[0], GuardedCallError)

    def test_guard_allowlist_propagates_to_child_thread(self) -> None:
        """When a plugin is in the allowlist, child thread passes through."""
        install_context_propagation()

        guard_token = _guard_active.set(True)
        level_token = _guard_level.set("error")
        patches_token = _guard_patches_installed.set(True)
        allowlist_token = _guard_allowlist.set(frozenset({"http"}))
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                get_verifier_or_raise("http:request")
            except GuardPassThrough as exc:
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        _guard_active.reset(guard_token)
        _guard_level.reset(level_token)
        _guard_patches_installed.reset(patches_token)
        _guard_allowlist.reset(allowlist_token)

        assert len(errors) == 1
        assert isinstance(errors[0], GuardPassThrough)


# ---------------------------------------------------------------------------
# Python 3.14 thread_inherit_context detection
# ---------------------------------------------------------------------------


class TestPython314Detection:
    """Verify behavior when sys.flags.thread_inherit_context is True."""

    def test_start_new_thread_not_patched_when_runtime_handles_it(self) -> None:
        """When sys.flags.thread_inherit_context is True, _thread.start_new_thread is not patched."""
        import bigfoot._context_propagation as cp

        # Ensure clean state
        uninstall_context_propagation()

        original_start = _thread.start_new_thread

        # Mock sys.flags to have thread_inherit_context=True
        mock_flags = type("MockFlags", (), {"thread_inherit_context": True})()
        with patch.object(sys, "flags", mock_flags):
            install_context_propagation()

        # _thread.start_new_thread should NOT have been patched
        assert _thread.start_new_thread is original_start
        # But TPE.submit SHOULD still be patched
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
