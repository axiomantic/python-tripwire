"""Guard mode allow-list context manager."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from bigfoot._context import _guard_allowlist


@contextmanager
def allow(*plugin_names: str) -> Generator[None, None, None]:
    """Permit specific plugin categories to make real calls during guard mode.

    Usage::

        with bigfoot.allow("dns", "socket"):
            boto3.client("s3")  # DNS + socket calls pass through

    Nestable: inner allow() adds to the outer allowlist.
    No-op when guard mode is not active (calls pass through anyway).
    No timeline recording: allowed calls are invisible to bigfoot.
    """
    from bigfoot._errors import BigfootConfigError  # noqa: PLC0415
    from bigfoot._registry import GUARD_ELIGIBLE_PREFIXES, VALID_PLUGIN_NAMES  # noqa: PLC0415

    valid = VALID_PLUGIN_NAMES | GUARD_ELIGIBLE_PREFIXES
    unknown = set(plugin_names) - valid
    if unknown:
        raise BigfootConfigError(
            f"Unknown plugin name(s) in allow(): {sorted(unknown)}. "
            f"Valid names: {sorted(valid)}"
        )

    current = _guard_allowlist.get()
    merged = current | frozenset(plugin_names)
    token = _guard_allowlist.set(merged)
    try:
        yield
    finally:
        _guard_allowlist.reset(token)
