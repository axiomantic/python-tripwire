"""Firewall engine: rule stack with push/pop/evaluate."""

from __future__ import annotations

import contextvars
import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tripwire._firewall_request import FirewallRequest
    from tripwire._match import M


class Disposition(enum.Enum):
    """The action a firewall rule takes when it matches a request."""
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class FirewallRule:
    """A single firewall rule: a pattern and what to do when it matches."""
    pattern: M
    disposition: Disposition


@dataclass(frozen=True, slots=True)
class RestrictFrame:
    """A restriction ceiling on the rule stack.

    During evaluation, scanning stops at a RestrictFrame for any request
    that does NOT match the restriction pattern. Requests that DO match
    continue scanning past this frame.

    Semantics:
    - restrict(M(protocol='http')) means only HTTP requests can proceed
      past this frame. Redis, socket, etc. requests hit this ceiling
      and get DENY.
    - Nested restrict() frames intersect: both must match for the
      request to pass through.
    """
    pattern: M


# A stack frame is either a rule or a restriction ceiling.
StackFrame = FirewallRule | RestrictFrame


class FirewallStack:
    """ContextVar-backed stack of firewall rules.

    Frames are ordered outermost (index 0) to innermost (index -1).
    Evaluation scans from innermost to outermost. First matching rule wins.
    Default disposition is DENY.
    """

    __slots__ = ("_frames",)

    def __init__(self, frames: tuple[StackFrame, ...] = ()) -> None:
        self._frames = frames

    @property
    def frames(self) -> tuple[StackFrame, ...]:
        return self._frames

    def push(self, *new_frames: StackFrame) -> FirewallStack:
        """Return a new stack with new_frames appended (innermost)."""
        return FirewallStack(self._frames + new_frames)

    def evaluate(self, request: FirewallRequest) -> Disposition:
        """Evaluate with restrict ceiling semantics (two-phase algorithm).

        Phase 1: Check all RestrictFrames. If ANY restrict frame's pattern
        does NOT match the request, return DENY immediately. Restrict frames
        form a conjunction (all must pass).

        Phase 2: Scan FirewallRules innermost to outermost. First match wins.
        Default DENY.

        See Appendix B for a detailed walkthrough of WHY a two-phase approach
        is needed -- a single-pass scan allows inner allow() rules to
        override restrict() ceilings, which defeats their purpose.
        """
        # Phase 1: Restriction check (all restrict frames must pass)
        for frame in self._frames:
            if isinstance(frame, RestrictFrame):
                if not frame.pattern.matches(request):
                    return Disposition.DENY

        # Phase 2: Rule scan (innermost first)
        for frame in reversed(self._frames):
            if isinstance(frame, FirewallRule):
                if frame.pattern.matches(request):
                    return frame.disposition

        return Disposition.DENY


# ---------------------------------------------------------------------------
# Module-level ContextVar
# ---------------------------------------------------------------------------

_firewall_stack: contextvars.ContextVar[FirewallStack] = contextvars.ContextVar(
    "tripwire_firewall_stack", default=FirewallStack()
)


def get_firewall_stack() -> FirewallStack:
    """Return the current firewall stack."""
    return _firewall_stack.get()
