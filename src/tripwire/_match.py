"""M() pattern object for defining firewall rules."""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tripwire._firewall_request import FirewallRequest

# Fields where glob auto-detection applies (strings containing * are globs).
# All other string fields use exact match by default.
GLOB_FIELDS: frozenset[str] = frozenset({
    "host", "hostname", "path", "uri", "database_path",
})


class M:
    """Match pattern for firewall rules.

    Usage:
        M(protocol="http")                          # match all HTTP
        M(protocol="http", host="*.example.com")    # glob on host
        M(protocol="http", method="GET")             # exact on method
        M(protocol="redis", host__cidr="10.0.0.0/8") # CIDR match
        M(protocol="http", path__regex=r"/api/v\d+/.*") # regex match
        M(protocol="subprocess", binary=lambda b: b in {"git", "curl"})

    Composition:
        M(protocol="http") | M(protocol="dns")      # match either
        M(protocol="http") & M(host="*.example.com") # match both
        ~M(protocol="redis", command="FLUSHALL")     # negate

    Duck-typed value matching protocol:
    1. str + field in GLOB_FIELDS + contains '*': glob match
    2. str + field NOT in GLOB_FIELDS: exact match
    3. __cidr suffix: CIDR network containment
    4. __regex suffix: re.fullmatch
    5. __glob suffix: explicit glob (for fields not in GLOB_FIELDS)
    6. callable(value): call with actual, check truthiness
    7. Otherwise: pattern == actual (supports dirty-equals, custom __eq__)
    """

    __slots__ = ("_protocol", "_field_matchers")

    # Fields that get host normalization at construction time
    _HOST_FIELDS: frozenset[str] = frozenset({"host", "hostname"})
    # Fields that get path normalization at construction time
    _PATH_FIELDS: frozenset[str] = frozenset({"path", "database_path", "uri"})

    def __init__(self, protocol: str | None = None, **kwargs: Any) -> None:  # noqa: ANN401
        self._protocol = protocol
        self._field_matchers: dict[str, _FieldMatcher] = {}

        # Normalize pattern values at construction time so patterns and
        # requests are always compared in normalized form.
        normalized_kwargs: dict[str, Any] = {}
        for key, value in kwargs.items():
            # Extract the base field name (strip __cidr, __regex, __glob suffixes)
            base_key = key
            for suffix in ("__cidr", "__regex", "__glob"):
                if key.endswith(suffix):
                    base_key = key[: -len(suffix)]
                    break

            # Only normalize plain string values (not callables, not suffix-modified)
            if isinstance(value, str) and base_key == key:
                if base_key in self._HOST_FIELDS:
                    from tripwire._normalize import normalize_host  # noqa: PLC0415
                    value = normalize_host(value)
                elif base_key in self._PATH_FIELDS:
                    from tripwire._normalize import normalize_path  # noqa: PLC0415
                    # Only normalize non-glob paths (globs contain * which should not be resolved)
                    if "*" not in value:
                        value = normalize_path(value)
            normalized_kwargs[key] = value

        for key, value in normalized_kwargs.items():
            if key.endswith("__cidr"):
                field_name = key[: -len("__cidr")]
                self._field_matchers[field_name] = _CidrMatcher(value)
            elif key.endswith("__regex"):
                field_name = key[: -len("__regex")]
                self._field_matchers[field_name] = _RegexMatcher(value)
            elif key.endswith("__glob"):
                field_name = key[: -len("__glob")]
                self._field_matchers[field_name] = _GlobMatcher(value)
            elif callable(value) and not isinstance(value, str):
                self._field_matchers[key] = _CallableMatcher(value)
            elif isinstance(value, str) and key in GLOB_FIELDS and "*" in value:
                self._field_matchers[key] = _GlobMatcher(value)
            elif isinstance(value, str):
                self._field_matchers[key] = _ExactMatcher(value)
            else:
                # Duck-typed: dirty-equals, int, etc.
                self._field_matchers[key] = _EqualityMatcher(value)

    def matches(self, request: FirewallRequest) -> bool:
        """Return True if this pattern matches the given request."""
        # Protocol filter: if set, request.protocol must match
        if self._protocol is not None and request.protocol != self._protocol:
            return False

        # Every field matcher must match its corresponding request field
        for field_name, matcher in self._field_matchers.items():
            actual = getattr(request, field_name, _MISSING)
            if actual is _MISSING:
                return False
            if not matcher.matches(actual):
                return False

        return True

    def __and__(self, other: M) -> M:
        return _AndPattern(self, other)

    def __or__(self, other: M) -> M:
        return _OrPattern(self, other)

    def __invert__(self) -> M:
        return _NotPattern(self)

    def __repr__(self) -> str:
        parts = []
        if self._protocol is not None:
            parts.append(f"protocol={self._protocol!r}")
        for field_name, matcher in self._field_matchers.items():
            parts.append(f"{field_name}={matcher!r}")
        return f"M({', '.join(parts)})"


_MISSING = object()


# ---------------------------------------------------------------------------
# Field matcher implementations
# ---------------------------------------------------------------------------

class _FieldMatcher:
    """Base protocol for field-level matching."""
    __slots__ = ()

    def matches(self, actual: Any) -> bool:  # noqa: ANN401
        raise NotImplementedError


class _ExactMatcher(_FieldMatcher):
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def matches(self, actual: Any) -> bool:  # noqa: ANN401
        return bool(self._value == actual)

    def __repr__(self) -> str:
        return repr(self._value)


class _GlobMatcher(_FieldMatcher):
    __slots__ = ("_pattern",)

    def __init__(self, pattern: str) -> None:
        self._pattern = pattern

    def matches(self, actual: Any) -> bool:  # noqa: ANN401
        from tripwire._glob import tripwire_match  # noqa: PLC0415
        return tripwire_match(self._pattern, str(actual))

    def __repr__(self) -> str:
        return f"glob({self._pattern!r})"


class _CidrMatcher(_FieldMatcher):
    __slots__ = ("_network",)

    def __init__(self, cidr: str) -> None:
        self._network = ipaddress.ip_network(cidr, strict=False)

    def matches(self, actual: Any) -> bool:  # noqa: ANN401
        try:
            return ipaddress.ip_address(actual) in self._network
        except ValueError:
            return False

    def __repr__(self) -> str:
        return f"cidr({self._network!r})"


class _RegexMatcher(_FieldMatcher):
    __slots__ = ("_pattern", "_compiled")

    def __init__(self, pattern: str) -> None:
        self._pattern = pattern
        self._compiled = re.compile(pattern)

    def matches(self, actual: Any) -> bool:  # noqa: ANN401
        return self._compiled.fullmatch(str(actual)) is not None

    def __repr__(self) -> str:
        return f"regex({self._pattern!r})"


class _CallableMatcher(_FieldMatcher):
    __slots__ = ("_func",)

    def __init__(self, func: Any) -> None:  # noqa: ANN401
        self._func = func

    def matches(self, actual: Any) -> bool:  # noqa: ANN401
        return bool(self._func(actual))

    def __repr__(self) -> str:
        return f"callable({self._func!r})"


class _EqualityMatcher(_FieldMatcher):
    __slots__ = ("_value",)

    def __init__(self, value: Any) -> None:  # noqa: ANN401
        self._value = value

    def matches(self, actual: Any) -> bool:  # noqa: ANN401
        return bool(self._value == actual)

    def __repr__(self) -> str:
        return repr(self._value)


# ---------------------------------------------------------------------------
# Composite patterns
# ---------------------------------------------------------------------------

class _AndPattern(M):
    """Intersection: both patterns must match."""
    __slots__ = ("_left", "_right")

    def __init__(self, left: M, right: M) -> None:
        self._left = left
        self._right = right

    def matches(self, request: FirewallRequest) -> bool:
        return self._left.matches(request) and self._right.matches(request)

    def __repr__(self) -> str:
        return f"({self._left!r} & {self._right!r})"


class _OrPattern(M):
    """Union: either pattern may match."""
    __slots__ = ("_left", "_right")

    def __init__(self, left: M, right: M) -> None:
        self._left = left
        self._right = right

    def matches(self, request: FirewallRequest) -> bool:
        return self._left.matches(request) or self._right.matches(request)

    def __repr__(self) -> str:
        return f"({self._left!r} | {self._right!r})"


class _NotPattern(M):
    """Negation: pattern must NOT match."""
    __slots__ = ("_inner",)

    def __init__(self, inner: M) -> None:
        self._inner = inner

    def matches(self, request: FirewallRequest) -> bool:
        return not self._inner.matches(request)

    def __repr__(self) -> str:
        return f"(~{self._inner!r})"
