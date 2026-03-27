"""Guard mode allow/deny/restrict context managers."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from bigfoot._firewall import (
    Disposition,
    FirewallRule,
    RestrictFrame,
    _firewall_stack,
)
from bigfoot._match import M


def _coerce_to_m(rule: str | M) -> M:
    """Convert a bare string to M(protocol=name), pass M through."""
    if isinstance(rule, str):
        return M(protocol=rule)
    return rule


@contextmanager
def allow(*rules: str | M) -> Generator[None, None, None]:
    """Push ALLOW rules onto the firewall stack.

    Usage:
        # Coarse: allow entire protocol
        with bigfoot.allow("dns", "socket"):
            ...

        # Granular: allow specific host pattern
        with bigfoot.allow(M(protocol="http", host="*.example.com")):
            ...

        # Mixed:
        with bigfoot.allow("dns", M(protocol="http", host="*.example.com")):
            ...
    """
    if not rules:
        raise ValueError("allow() requires at least one rule")

    frames = tuple(
        FirewallRule(pattern=_coerce_to_m(r), disposition=Disposition.ALLOW)
        for r in rules
    )

    current = _firewall_stack.get()
    new_stack = current.push(*frames)
    token = _firewall_stack.set(new_stack)
    try:
        yield
    finally:
        _firewall_stack.reset(token)


@contextmanager
def deny(*rules: str | M) -> Generator[None, None, None]:
    """Push DENY rules onto the firewall stack.

    Usage:
        with bigfoot.allow("redis"):
            with bigfoot.deny(M(protocol="redis", command="FLUSHALL")):
                # Redis allowed except FLUSHALL
                ...
    """
    if not rules:
        raise ValueError("deny() requires at least one rule")

    frames = tuple(
        FirewallRule(pattern=_coerce_to_m(r), disposition=Disposition.DENY)
        for r in rules
    )

    current = _firewall_stack.get()
    new_stack = current.push(*frames)
    token = _firewall_stack.set(new_stack)
    try:
        yield
    finally:
        _firewall_stack.reset(token)


@contextmanager
def restrict(*rules: str | M) -> Generator[None, None, None]:
    """Push a restriction ceiling onto the firewall stack.

    Only requests matching the restriction pattern can proceed past this frame.
    Inner allow() calls work within the restriction's scope but cannot widen it.

    Usage:
        # Only HTTP allowed; inner allow("redis") is silently blocked
        with bigfoot.restrict(M(protocol="http")):
            with bigfoot.allow(M(protocol="http", host="*.example.com")):
                # Only *.example.com HTTP passes
                ...

    Multiple rules are OR'd together into a single restriction pattern:
        with bigfoot.restrict("http", "dns"):
            # Only HTTP and DNS can pass this ceiling
            ...
    """
    if not rules:
        raise ValueError("restrict() requires at least one rule")

    if len(rules) == 1:
        pattern = _coerce_to_m(rules[0])
    else:
        # OR them together: restrict("http", "dns") means either HTTP or DNS
        combined = _coerce_to_m(rules[0])
        for r in rules[1:]:
            combined = combined | _coerce_to_m(r)
        pattern = combined

    frame = RestrictFrame(pattern=pattern)
    current = _firewall_stack.get()
    new_stack = current.push(frame)
    token = _firewall_stack.set(new_stack)
    try:
        yield
    finally:
        _firewall_stack.reset(token)
