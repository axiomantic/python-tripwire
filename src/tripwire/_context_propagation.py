"""Cross-thread ContextVar propagation for tripwire interceptors.

Monkey-patches ``threading.Thread.start()``, ``_thread.start_new_thread()``,
and ``concurrent.futures.ThreadPoolExecutor.submit()`` to copy the current
context to child threads via ``contextvars.copy_context()``.

``threading.Thread.start()`` is patched directly (like OpenTelemetry does)
because it is a stable public API across all Python versions. Previous
approaches that patched internal threading module attributes
(``threading._start_new_thread``, ``threading._start_joinable_thread``)
broke across CPython versions as the internal plumbing changed.

``_thread.start_new_thread`` is still patched to catch raw ``_thread`` usage
that bypasses ``threading.Thread``.

Activate via ``install_context_propagation()`` (called from pytest_configure)
and deactivate via ``uninstall_context_propagation()`` (called from
pytest_unconfigure).
"""

from __future__ import annotations

import _thread
import contextvars
import functools
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_installed = False
_lock = threading.Lock()

# Capture originals at install time, NOT at import time. This respects
# other libraries (e.g., OTel) that may have already patched these
# before tripwire installs.
_saved_start_new_thread: Callable[..., Any] | None = None
_saved_thread_start: Callable[..., None] | None = None
_saved_tpe_submit: Callable[..., Any] | None = None


def install_context_propagation() -> None:
    """Monkey-patch threading.Thread.start, _thread.start_new_thread, and TPE.submit.

    Idempotent: calling twice is a no-op.

    On Python 3.14+ free-threaded builds where
    ``sys.flags.thread_inherit_context`` is True, thread patching is
    skipped (the runtime handles it natively). TPE.submit is still patched
    because the runtime flag only affects Thread, not executors.
    """
    global _installed, _saved_start_new_thread, _saved_thread_start
    global _saved_tpe_submit

    with _lock:
        if _installed:
            return

        skip_thread_patch = getattr(getattr(sys, "flags", None), "thread_inherit_context", False)

        # Always patch _thread.start_new_thread for raw _thread usage.
        # Unlike threading.Thread, _thread.start_new_thread does NOT natively
        # inherit context even on free-threaded Python 3.14t.
        _saved_start_new_thread = _thread.start_new_thread

        _original_start = _saved_start_new_thread

        def _patched_start_new_thread(
            function: Callable[..., Any],
            args: tuple[Any, ...],
            kwargs: dict[str, Any] | None = None,
        ) -> int:
            ctx = contextvars.copy_context()

            def _context_wrapper(*a: Any, **kw: Any) -> None:  # noqa: ANN401
                ctx.run(function, *a, **kw)

            return _original_start(_context_wrapper, args, kwargs or {})

        _thread.start_new_thread = _patched_start_new_thread

        if not skip_thread_patch:
            # Patch threading.Thread.start() directly for Thread usage.
            # Skipped on free-threaded 3.14t where the runtime handles it.
            _saved_thread_start = threading.Thread.start

            _original_thread_start = _saved_thread_start

            def _patched_thread_start(self: threading.Thread) -> None:
                ctx = contextvars.copy_context()
                original_run = self.run

                @functools.wraps(original_run)
                def _context_run() -> None:
                    ctx.run(original_run)

                self.run = _context_run  # type: ignore[method-assign]
                _original_thread_start(self)

            threading.Thread.start = _patched_thread_start  # type: ignore[method-assign]

        _saved_tpe_submit = ThreadPoolExecutor.submit

        _original_submit = _saved_tpe_submit

        def _patched_submit(
            self: ThreadPoolExecutor,
            fn: Callable[..., Any],
            /,
            *args: Any,  # noqa: ANN401
            **kwargs: Any,  # noqa: ANN401
        ) -> Any:  # noqa: ANN401
            ctx = contextvars.copy_context()
            return _original_submit(self, ctx.run, fn, *args, **kwargs)

        ThreadPoolExecutor.submit = _patched_submit  # type: ignore[assignment]

        _installed = True


def uninstall_context_propagation() -> None:
    """Restore original threading.Thread.start, _thread.start_new_thread, and TPE.submit.

    Idempotent: calling when not installed is a no-op.
    """
    global _installed, _saved_start_new_thread, _saved_thread_start
    global _saved_tpe_submit

    with _lock:
        if not _installed:
            return

        if _saved_start_new_thread is not None:
            _thread.start_new_thread = _saved_start_new_thread
            _saved_start_new_thread = None

        if _saved_thread_start is not None:
            threading.Thread.start = _saved_thread_start  # type: ignore[method-assign]
            _saved_thread_start = None

        if _saved_tpe_submit is not None:
            ThreadPoolExecutor.submit = _saved_tpe_submit  # type: ignore[method-assign]
            _saved_tpe_submit = None

        _installed = False
