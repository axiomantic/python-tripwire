"""Guard mode allow/deny context managers."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from bigfoot._context import _guard_allowlist


@contextmanager
def allow(*plugin_names: str) -> Generator[None, None, None]:
    """Permit specific plugin categories to bypass both guard mode and sandbox mode.

    Usage::

        with bigfoot.allow("dns", "socket"):
            boto3.client("s3")  # DNS + socket calls pass through

    When a plugin is in the allowlist, its interceptor calls the original
    function immediately, regardless of whether guard mode or a sandbox is
    active. No timeline recording: allowed calls are invisible to bigfoot.

    Nestable: inner allow() adds to the outer allowlist.
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


@contextmanager
def deny(*plugin_names: str) -> Generator[None, None, None]:
    """Remove specific plugins from the allowlist within a nested context.

    Usage::

        with bigfoot.allow("dns", "socket"):
            # dns and socket pass through
            with bigfoot.deny("socket"):
                # only dns passes through; socket is guarded again
                socket.connect(...)  # raises GuardedCallError
            # socket is allowed again here

    Nestable: inner deny() narrows the outer allowlist. On exit, the
    previous allowlist is restored.
    """
    from bigfoot._errors import BigfootConfigError  # noqa: PLC0415
    from bigfoot._registry import GUARD_ELIGIBLE_PREFIXES, VALID_PLUGIN_NAMES  # noqa: PLC0415

    valid = VALID_PLUGIN_NAMES | GUARD_ELIGIBLE_PREFIXES
    unknown = set(plugin_names) - valid
    if unknown:
        raise BigfootConfigError(
            f"Unknown plugin name(s) in deny(): {sorted(unknown)}. "
            f"Valid names: {sorted(valid)}"
        )

    current = _guard_allowlist.get()
    narrowed = current - frozenset(plugin_names)
    token = _guard_allowlist.set(narrowed)
    try:
        yield
    finally:
        _guard_allowlist.reset(token)
