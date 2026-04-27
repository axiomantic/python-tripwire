"""Module-level ContextVars for tripwire.

Import this module first to avoid circular imports. It has no dependencies
on other tripwire modules at import time (only deferred imports in functions).
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

from tripwire._config import GuardLevels

if TYPE_CHECKING:
    from tripwire._firewall_request import FirewallRequest
    from tripwire._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Module-level ContextVars
# ---------------------------------------------------------------------------

_active_verifier: contextvars.ContextVar[StrictVerifier | None] = contextvars.ContextVar(
    "tripwire_active_verifier", default=None
)

_any_order_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "tripwire_any_order_depth", default=0
)

_current_test_verifier: contextvars.ContextVar[StrictVerifier | None] = contextvars.ContextVar(
    "tripwire_current_test_verifier", default=None
)

_guard_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tripwire_guard_active", default=False
)

_guard_levels: contextvars.ContextVar[GuardLevels] = contextvars.ContextVar(
    "tripwire_guard_levels", default=GuardLevels(default="warn", overrides={})
)

_guard_patches_installed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tripwire_guard_patches_installed", default=False
)


class GuardPassThrough(BaseException):
    """Internal sentinel: interceptor should call the original function.

    Inherits from BaseException (not Exception) so generic except clauses
    in user code do not accidentally swallow it. Only interceptors should
    catch this.
    """


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def get_active_verifier() -> StrictVerifier | None:
    """Return the currently active verifier, or None if no sandbox is active."""
    return _active_verifier.get()


def _detect_post_sandbox() -> int | None:
    """Return the sandbox_id of a since-exited sandbox if the current
    execution context still carries it; otherwise None.

    In normal control flow, Branch 1 of `get_verifier_or_raise` returns
    the verifier when a sandbox is active in the current context. This
    helper triggers only when Branch 1 fell through (no active verifier)
    but the ContextVar still carries a sandbox_id from a since-exited
    sandbox. This is the case Proposal 4 catches: an asyncio task /
    thread / future survived the `with tripwire:` exit.
    """
    from tripwire._verifier import (  # noqa: PLC0415
        SandboxContext,
        _current_sandbox_id,
    )

    sid = _current_sandbox_id.get()
    if sid is None:
        return None
    # Hold the lock for the membership check: under PEP 703 free-threaded
    # CPython, an unsynchronized `in` check against a `set` being mutated
    # on another thread can corrupt the hash table and hang. The lock is
    # uncontended on the GIL build, so this is effectively a no-op there.
    with SandboxContext._active_sandbox_ids_lock:
        active = sid in SandboxContext._active_sandbox_ids
    if active:
        # Sandbox is still active in the process; Branch 1 should have
        # caught this. Defensive: do not fire post-sandbox here.
        return None
    return sid


def get_verifier_or_raise(
    source_id: str,
    firewall_request: FirewallRequest | None = None,
) -> StrictVerifier:
    """Return the active verifier, or handle guard mode, or raise.

    Decision tree:

    1. Sandbox active: return verifier.
    2. Guard active + firewall_request:
       a. ALLOW: raise GuardPassThrough.
       b. DENY + level "warn":
          - plugin.passthrough_safe is False: raise UnsafePassthroughError
            (real I/O would otherwise leak past 'warn').
          - otherwise: emit GuardedCallWarning and raise GuardPassThrough.
       c. DENY + level "error": raise GuardedCallError.
    3. Guard active + no firewall_request: raise GuardedCallError (fail-closed).
    4. Guard not active but patches installed: raise GuardPassThrough.
    5. Otherwise: raise SandboxNotActiveError.
    """
    plugin_name = source_id.split(":")[0]

    # === Branch 2: post-sandbox detection (C4) ===
    # MUST run BEFORE Branch 1: an asyncio task / thread captures the
    # parent's ContextVars at creation time, including `_active_verifier`.
    # After the parent's `with tripwire:` exits, `_active_verifier.reset()`
    # in the parent does NOT propagate into the task's snapshot, so the
    # task would otherwise hit Branch 1 with a stale verifier reference.
    # `_active_sandbox_ids` is a process-wide ClassVar set, which the
    # parent's `_exit()` discards in real time. Detecting a captured
    # sandbox_id that is NOT in the active set is the authoritative test
    # for "the sandbox this task came from has exited."
    closed_sandbox_id = _detect_post_sandbox()
    if closed_sandbox_id is not None:
        from tripwire._errors import PostSandboxInteractionError  # noqa: PLC0415

        raise PostSandboxInteractionError(
            source_id=source_id,
            plugin_name=plugin_name,
            sandbox_id=closed_sandbox_id,
        )

    # === Branch 1: sandbox active ===
    verifier = _active_verifier.get()
    if verifier is not None:
        return verifier

    # Resolve the plugin class once. ``plugin_cls is None`` means the
    # source_id does not belong to a registered plugin (e.g., a test
    # exercising get_verifier_or_raise with an arbitrary name). Unknown
    # plugins skip every guard branch and fall through to
    # SandboxNotActiveError so they preserve the pre-C2 contract.
    from tripwire._registry import (  # noqa: PLC0415
        lookup_plugin_class_by_name,
    )
    plugin_cls = lookup_plugin_class_by_name(plugin_name)
    plugin_is_unsafe_passthrough = (
        plugin_cls is not None and plugin_cls.passthrough_safe is False
    )

    # === Branch 3: guard active ===
    if plugin_cls is not None and _guard_active.get():
        if firewall_request is not None:
            from tripwire._firewall import Disposition, get_firewall_stack  # noqa: PLC0415

            disposition = get_firewall_stack().evaluate(firewall_request)

            # === Branch 3a: ALLOW ===
            if disposition is Disposition.ALLOW:
                raise GuardPassThrough()

            # === Branch 3b: DENY ===
            # Per-protocol or default guard level (C3).
            guard_levels = _guard_levels.get()
            level = guard_levels.overrides.get(plugin_name, guard_levels.default)

            # === Branch 3b-off (C3) ===
            # Per-protocol "off" disables the firewall entirely for this
            # plugin: no warn, no error, no UnsafePassthroughError.
            # MUST run BEFORE the warn-unsafe check.
            if level == "off":
                raise GuardPassThrough()

            if level == "warn":
                # === Branch 3b-warn-unsafe ===
                # If the plugin's passthrough is NOT safe, raise rather
                # than warn-and-pass-through, so real I/O does not leak.
                if plugin_is_unsafe_passthrough:
                    from tripwire._errors import UnsafePassthroughError  # noqa: PLC0415

                    raise UnsafePassthroughError(
                        source_id=source_id,
                        plugin_name=plugin_name,
                    )

                # === Branch 3b-warn-safe ===
                import warnings  # noqa: PLC0415

                from tripwire._errors import GuardedCallWarning  # noqa: PLC0415

                warnings.warn(
                    f"{source_id!r} blocked by firewall. "
                    f"See GuardedCallError docs for fix options.",
                    GuardedCallWarning,
                    stacklevel=4,
                )
                raise GuardPassThrough()

            # === Branch 3b-error (C5: enrich with user call site) ===
            from tripwire._errors import GuardedCallError  # noqa: PLC0415
            from tripwire._frames import walk_to_user_frame  # noqa: PLC0415

            user_frame = walk_to_user_frame()
            raise GuardedCallError(
                source_id=source_id,
                plugin_name=plugin_name,
                firewall_request=firewall_request,
                user_frame=user_frame,
            )

        # === Branch 3c: guard active, no firewall_request ===
        # Fail-closed only for plugins whose passthrough is unsafe (real
        # I/O). Unknown plugins and passthrough_safe=True plugins fall
        # through to SandboxNotActiveError so their interceptor-level
        # error paths can run as before.
        if plugin_is_unsafe_passthrough:
            from tripwire._errors import GuardedCallError  # noqa: PLC0415
            from tripwire._frames import walk_to_user_frame  # noqa: PLC0415

            user_frame = walk_to_user_frame()
            raise GuardedCallError(
                source_id=source_id,
                plugin_name=plugin_name,
                firewall_request=None,
                user_frame=user_frame,
            )

    # === Branch 4: guard not active but patches installed ===
    # Only fires for known plugins; unknown source_ids fall through to
    # SandboxNotActiveError below.
    if plugin_cls is not None and _guard_patches_installed.get():
        raise GuardPassThrough()

    # === Branch 5: nothing active ===
    from tripwire._errors import SandboxNotActiveError  # noqa: PLC0415
    raise SandboxNotActiveError(source_id=source_id)



def _get_test_verifier_or_raise() -> StrictVerifier:
    """Return the current test verifier, or raise NoActiveVerifierError.

    Called by module-level API functions (mock, sandbox, assert_interaction, etc.)
    when no test verifier is active.
    """
    from tripwire._errors import NoActiveVerifierError

    verifier = _current_test_verifier.get()
    if verifier is None:
        raise NoActiveVerifierError()
    return verifier


def is_in_any_order() -> bool:
    """Return True if the current context is inside an in_any_order() block."""
    return _any_order_depth.get() > 0
