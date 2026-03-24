"""Module-level ContextVars for bigfoot.

Import this module first to avoid circular imports. It has no dependencies
on other bigfoot modules at import time (only deferred imports in functions).
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Module-level ContextVars
# ---------------------------------------------------------------------------

_active_verifier: contextvars.ContextVar[StrictVerifier | None] = contextvars.ContextVar(
    "bigfoot_active_verifier", default=None
)

_any_order_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "bigfoot_any_order_depth", default=0
)

_current_test_verifier: contextvars.ContextVar[StrictVerifier | None] = contextvars.ContextVar(
    "bigfoot_current_test_verifier", default=None
)

_guard_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "bigfoot_guard_active", default=False
)

_guard_allowlist: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "bigfoot_guard_allowlist", default=frozenset()
)

_guard_level: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bigfoot_guard_level", default="warn"
)

_guard_patches_installed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "bigfoot_guard_patches_installed", default=False
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


def get_verifier_or_raise(source_id: str) -> StrictVerifier:
    """Return the active verifier, or handle guard mode, or raise.

    Called by interceptors when they fire. Decision tree:

    1. Sandbox active (_active_verifier set): return verifier.
    2. Guard-eligible plugin (source_id prefix in GUARD_ELIGIBLE_PREFIXES):
       a. Guard active + not in allowlist: raise GuardedCallError (blocked).
       b. Guard active + in allowlist, or guard not active but patches
          installed: raise GuardPassThrough (call original).
    3. Non-guard-eligible plugin (e.g., "mock:", "logging:"):
       raise SandboxNotActiveError (existing behavior, guard is irrelevant).
    4. No sandbox, no guard: raise SandboxNotActiveError.
    """
    verifier = _active_verifier.get()
    if verifier is not None:
        return verifier

    # No sandbox active. Check if this is a guard-eligible plugin.
    from bigfoot._registry import GUARD_ELIGIBLE_PREFIXES  # noqa: PLC0415

    plugin_name = source_id.split(":")[0]
    is_guard_eligible = plugin_name in GUARD_ELIGIBLE_PREFIXES

    if is_guard_eligible:
        if _guard_active.get():
            # Guard active: check allowlist
            allowlist = _guard_allowlist.get()
            if plugin_name not in allowlist:
                level = _guard_level.get()
                if level == "warn":
                    import warnings  # noqa: PLC0415

                    from bigfoot._errors import GuardedCallWarning  # noqa: PLC0415

                    warnings.warn(
                        f"{source_id!r} called outside sandbox. "
                        f'Silence with @pytest.mark.allow("{plugin_name}") or '
                        f'set guard = "error" in [tool.bigfoot] to make this an error.',
                        GuardedCallWarning,
                        stacklevel=4,
                    )
                    raise GuardPassThrough()
                # level == "error"
                from bigfoot._errors import GuardedCallError  # noqa: PLC0415

                raise GuardedCallError(
                    source_id=source_id, plugin_name=plugin_name,
                )
            # In allowlist: pass through to original
            raise GuardPassThrough()
        if _guard_patches_installed.get():
            # Patches installed but guard not active (fixture teardown).
            # Pass through to original.
            raise GuardPassThrough()

    # Non-guard-eligible plugin, or no guard infrastructure active.
    from bigfoot._errors import SandboxNotActiveError  # noqa: PLC0415

    raise SandboxNotActiveError(source_id=source_id)



def _get_test_verifier_or_raise() -> StrictVerifier:
    """Return the current test verifier, or raise NoActiveVerifierError.

    Called by module-level API functions (mock, sandbox, assert_interaction, etc.)
    when no test verifier is active.
    """
    from bigfoot._errors import NoActiveVerifierError

    verifier = _current_test_verifier.get()
    if verifier is None:
        raise NoActiveVerifierError()
    return verifier


def is_in_any_order() -> bool:
    """Return True if the current context is inside an in_any_order() block."""
    return _any_order_depth.get() > 0
