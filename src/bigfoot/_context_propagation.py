"""Cross-thread ContextVar propagation for bigfoot interceptors.

Monkey-patches ``_thread.start_new_thread()`` and
``concurrent.futures.ThreadPoolExecutor.submit()`` to copy the current
context to child threads via ``contextvars.copy_context()``.

Patching at the ``_thread`` level (rather than ``threading.Thread.start``)
catches ALL thread creation that goes through the Python-level ``_thread``
module, including C extension threads. The only blind spot is C code
calling ``PyThread_start_new_thread`` directly from C, which is vanishingly
rare and unaddressable from Python.

Activate via ``install_context_propagation()`` (called from pytest_configure)
and deactivate via ``uninstall_context_propagation()`` (called from
pytest_unconfigure).
"""

from __future__ import annotations

import _thread
import contextvars
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_installed = False
_lock = threading.Lock()

# Capture whatever _thread.start_new_thread and TPE.submit are at install
# time, NOT at import time. This respects other libraries (e.g., OTel) that
# may have already patched these before bigfoot installs.
_saved_start_new_thread: Callable[..., Any] | None = None
_saved_threading_start_new_thread: Callable[..., Any] | None = None
_saved_tpe_submit: Callable[..., Any] | None = None


def install_context_propagation() -> None:
    """Monkey-patch _thread.start_new_thread and TPE.submit to propagate contextvars.

    Idempotent: calling twice is a no-op.

    On Python 3.14+ free-threaded builds where
    ``sys.flags.thread_inherit_context`` is True, _thread.start_new_thread
    patching is skipped (the runtime handles it natively). TPE.submit is
    still patched because the runtime flag only affects Thread, not executors.
    """
    global _installed, _saved_start_new_thread, _saved_threading_start_new_thread, _saved_tpe_submit

    with _lock:
        if _installed:
            return

        skip_thread_patch = getattr(getattr(sys, "flags", None), "thread_inherit_context", False)

        if not skip_thread_patch:
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

            # threading caches _thread.start_new_thread as a module-level
            # _start_new_thread at import time. We must also patch that cached
            # reference so threading.Thread.start() uses our wrapper.
            _saved_threading_start_new_thread = threading._start_new_thread  # type: ignore[attr-defined]
            threading._start_new_thread = _patched_start_new_thread  # type: ignore[attr-defined]

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
    """Restore original _thread.start_new_thread and TPE.submit.

    Idempotent: calling when not installed is a no-op.
    """
    global _installed, _saved_start_new_thread, _saved_threading_start_new_thread, _saved_tpe_submit

    with _lock:
        if not _installed:
            return

        if _saved_start_new_thread is not None:
            _thread.start_new_thread = _saved_start_new_thread
            _saved_start_new_thread = None

        if _saved_threading_start_new_thread is not None:
            threading._start_new_thread = _saved_threading_start_new_thread  # type: ignore[attr-defined]
            _saved_threading_start_new_thread = None

        if _saved_tpe_submit is not None:
            ThreadPoolExecutor.submit = _saved_tpe_submit  # type: ignore[method-assign]
            _saved_tpe_submit = None

        _installed = False
