"""Module-level ContextVars for bigfoot.

Import this module first to avoid circular imports. It has no dependencies
on other bigfoot modules at import time (only deferred imports in functions).
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bigfoot._firewall_request import FirewallRequest
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


def get_verifier_or_raise(
    source_id: str,
    firewall_request: FirewallRequest | None = None,
) -> StrictVerifier:
    """Return the active verifier, or handle guard mode, or raise.

    Decision tree:

    1. Guard-eligible plugin + guard active + firewall ALLOW:
       raise GuardPassThrough (bypasses sandbox -- allowed calls are invisible).
    2. Sandbox active: return verifier.
    3. Guard-eligible plugin (determined by supports_guard ClassVar):
       a. Guard active + firewall_request provided:
          - DENY + level "warn": warn and raise GuardPassThrough.
          - DENY + level "error": raise GuardedCallError.
       b. Guard active + no firewall_request (should not happen post-migration,
          but safe fallback): raise GuardedCallError.
       c. Guard not active but patches installed: raise GuardPassThrough.
    4. Non-guard-eligible plugin: raise SandboxNotActiveError.
    """
    # Check for active sandbox FIRST: when a sandbox is active, all calls
    # should go through the sandbox's mock/intercept pipeline.  The firewall
    # is only relevant in guard mode (outside a sandbox).
    verifier = _active_verifier.get()
    if verifier is not None:
        return verifier

    # No sandbox active -- check firewall for guard mode.
    if firewall_request is not None and _guard_active.get():
        plugin_name = source_id.split(":")[0]
        from bigfoot._registry import is_guard_eligible  # noqa: PLC0415

        if is_guard_eligible(plugin_name):
            from bigfoot._firewall import Disposition, get_firewall_stack  # noqa: PLC0415

            disposition = get_firewall_stack().evaluate(firewall_request)
            if disposition == Disposition.ALLOW:
                raise GuardPassThrough()

    # Determine guard eligibility from plugin ClassVar, not GUARD_ELIGIBLE_PREFIXES
    plugin_name = source_id.split(":")[0]

    # Use the new unified eligibility check
    from bigfoot._registry import is_guard_eligible  # noqa: PLC0415

    if is_guard_eligible(plugin_name):
        if _guard_active.get():
            if firewall_request is not None:
                from bigfoot._firewall import Disposition, get_firewall_stack  # noqa: PLC0415

                disposition = get_firewall_stack().evaluate(firewall_request)
                # ALLOW was already handled above, so this is DENY
                level = _guard_level.get()
                if level == "warn":
                    import warnings  # noqa: PLC0415

                    from bigfoot._errors import GuardedCallWarning  # noqa: PLC0415

                    warnings.warn(
                        f"{source_id!r} blocked by firewall. "
                        f"See GuardedCallError docs for fix options.",
                        GuardedCallWarning,
                        stacklevel=4,
                    )
                    raise GuardPassThrough()

                # level == "error"
                from bigfoot._errors import GuardedCallError  # noqa: PLC0415

                raise GuardedCallError(
                    source_id=source_id,
                    plugin_name=plugin_name,
                    firewall_request=firewall_request,
                )
            else:
                # No firewall_request: fail closed
                from bigfoot._errors import GuardedCallError  # noqa: PLC0415

                raise GuardedCallError(
                    source_id=source_id,
                    plugin_name=plugin_name,
                    firewall_request=None,
                )

        if _guard_patches_installed.get():
            raise GuardPassThrough()

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
