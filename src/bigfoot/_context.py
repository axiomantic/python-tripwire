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


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def get_active_verifier() -> StrictVerifier | None:
    """Return the currently active verifier, or None if no sandbox is active."""
    return _active_verifier.get()


def _get_verifier_or_raise(source_id: str) -> StrictVerifier:
    """Return the active verifier, or raise SandboxNotActiveError.

    Called by interceptors when they fire. If no sandbox is active, raises
    SandboxNotActiveError with the given source_id so the user knows which
    interceptor fired outside a sandbox.
    """
    from bigfoot._errors import SandboxNotActiveError

    verifier = _active_verifier.get()
    if verifier is None:
        raise SandboxNotActiveError(source_id=source_id)
    return verifier


def is_in_any_order() -> bool:
    """Return True if the current context is inside an in_any_order() block."""
    return _any_order_depth.get() > 0
