"""C1-T3: default guard level is 'error' (Proposal 1 default flip)."""

from __future__ import annotations

from tripwire.pytest_plugin import _resolve_guard_level


def test_default_guard_is_error() -> None:
    """With NO `[tool.tripwire]` config, the resolved guard level is 'error'.

    Replaces the prior default of 'warn'. New projects fail loud on unmocked
    I/O outside a sandbox; legacy projects must opt back into warn explicitly.
    """
    assert _resolve_guard_level({}) == "error"
