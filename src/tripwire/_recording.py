"""Shared ContextVar for the auto-assert runtime guard.

Imported by both _base_plugin and _timeline to avoid a circular import:
_base_plugin imports _timeline (for Interaction type), so _timeline
cannot import from _base_plugin. Both import from this module instead.
"""

from contextvars import ContextVar

_recording_in_progress: ContextVar[bool] = ContextVar(
    "_recording_in_progress", default=False
)
