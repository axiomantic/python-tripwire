# Threading and ContextVar Propagation

## Overview

tripwire uses `ContextVar` instances to track sandbox state, guard mode, and test verifiers. By default, Python threads do not inherit `ContextVar` values from their parent (per [PEP 567](https://peps.python.org/pep-0567/)). This means a child thread would not see the active sandbox or guard state, and intercepted calls in that thread would raise `SandboxNotActiveError` or bypass guard mode entirely.

tripwire solves this automatically. At test session startup, it installs context propagation patches that copy all `ContextVar` values to child threads. No configuration is required.

## How it works

The `_context_propagation` module patches two thread-creation mechanisms:

1. **`threading.Thread.start`** -- patched directly (following the [OpenTelemetry pattern](https://pypi.org/project/opentelemetry-instrumentation-threading/)). This is a stable public API across all Python versions.

2. **`_thread.start_new_thread`** -- the low-level C function. Patching at this level catches code that bypasses `threading.Thread` and calls `_thread` directly.

3. **`ThreadPoolExecutor.submit`** -- the standard library executor. Submitted callables are wrapped to run inside the copied context.

When either path creates a thread, tripwire calls `contextvars.copy_context()` at creation time. This captures a snapshot of all active `ContextVar` values. The child thread's callable then runs inside that copied context via `Context.run()`.

The patches are installed at `pytest_configure` and removed at `pytest_unconfigure`. They are idempotent and thread-safe (guarded by a module-level lock).

On Python 3.14+ free-threaded builds where `sys.flags.thread_inherit_context` is `True`, the `threading.Thread.start` patch is skipped because the runtime handles context inheritance natively. The `_thread.start_new_thread` and `ThreadPoolExecutor.submit` patches are still installed because the runtime flag only affects `threading.Thread`, not lower-level thread creation or executors.

## What gets propagated

tripwire defines nine `ContextVar` instances. All of them are captured by `copy_context()`:

| ContextVar | Module | Purpose |
|---|---|---|
| `_active_verifier` | `_context` | Points interceptors to the current sandbox verifier |
| `_current_test_verifier` | `_context` | Points module-level API functions to the per-test verifier |
| `_any_order_depth` | `_context` | Tracks nesting depth of `in_any_order()` blocks |
| `_guard_active` | `_context` | Whether guard mode is active for the current test |
| `_guard_allowlist` | `_context` | Firewall rules (allow/deny/restrict) for the current scope |
| `_guard_level` | `_context` | Guard level: `"warn"` or `"error"` |
| `_guard_patches_installed` | `_context` | Whether session-scoped guard patches are installed |
| `_recording_in_progress` | `_recording` | Auto-assert guard (prevents `mark_asserted` during `record`) |
| `_file_io_bypass` | `plugins.file_io_plugin` | Reentrancy guard for FileIoPlugin interceptors |

## Common scenarios

Context propagation matters whenever code under test (or a test utility) creates threads. Common cases:

**Starlette/FastAPI TestClient.** The `TestClient` spawns a background thread via anyio to run the ASGI app. Without context propagation, HTTP calls intercepted inside that background thread would not find the active sandbox.

**ThreadPoolExecutor in production code.** If your application dispatches work to a thread pool, those worker threads need the sandbox context to route intercepted calls correctly.

**Custom threading.Thread usage.** Any code that creates `threading.Thread` instances benefits from propagation. tripwire patches both `threading.Thread.start()` and the lower-level `_thread.start_new_thread()` to cover all thread-creation paths.

**Libraries that create threads internally.** Some libraries spawn threads for connection pools, background polling, or heartbeats. The low-level `_thread.start_new_thread` patch catches these without needing per-library workarounds.

## Thread isolation

Context propagation uses **copy semantics**, not shared state. `copy_context()` takes a snapshot at thread creation time. Changes to `ContextVar` values in the child thread do not propagate back to the parent, and changes in the parent after the child starts are not visible to the child.

This means:

- A child thread that enters a nested sandbox does not affect the parent.
- A parent that exits a sandbox after spawning a child does not invalidate the child's copy.
- Multiple child threads each get independent copies and cannot interfere with each other.

## Limitations

The only blind spot is C code that calls `PyThread_start_new_thread` directly from C, bypassing the Python-level `_thread` module entirely. This is vanishingly rare in practice. Virtually all thread creation in the Python ecosystem goes through `_thread.start_new_thread` or `ThreadPoolExecutor.submit`, both of which are patched.

## Free-threaded Python (3.14t)

tripwire supports free-threaded Python (the `t` suffix builds with `Py_GIL_DISABLED`). On these builds, `sys.flags.thread_inherit_context` is `True` and threads natively inherit `ContextVar` values, so tripwire skips the `Thread.start` patch.

When developing or testing tripwire on free-threaded Python, use the `dev-ft` extra instead of `dev`:

```bash
pip install -e ".[dev-ft]"
```

The `dev-ft` extra excludes `psycopg2-binary`, which does not ship prebuilt wheels for free-threaded Python and fails to build from source without `libpq` development headers. All other tripwire plugins and test dependencies are included. Tests for `Psycopg2Plugin` will be skipped due to the missing import.

## Interaction with guard mode

Firewall state (`_guard_active`, `_guard_allowlist`, `_guard_level`, `_guard_patches_installed`) propagates to child threads through the same mechanism. This means:

- If a test is running with guard mode active, calls in child threads are guarded.
- If a test uses `@pytest.mark.allow("http")` or `tripwire.allow("http")`, the allowlist propagates to child threads.
- Guard warnings and errors fire correctly in child threads, with the same level and allowlist as the parent.

For full details on guard mode, see [Guard Mode](guard-mode.md).
