"""Custom glob matching for tripwire firewall patterns.

fnmatch is insufficient because:
- It treats * and ** identically
- It has no concept of subdomain boundary anchoring
- Case sensitivity is platform-dependent

This module provides:
- tripwire_match(): general-purpose matching
- Proper anchoring: *.example.com must NOT match evil-example.com
- Case-insensitive host matching (RFC 4343)
- * matches within a segment; ** matches across segments (for paths)
"""

from __future__ import annotations

from fnmatch import fnmatchcase


def _match_host_glob(pattern: str, value: str) -> bool:
    """Match a hostname glob pattern with proper subdomain anchoring.

    *.example.com matches:
        sub.example.com         YES
        deep.sub.example.com    YES
        example.com             NO
        evil-example.com        NO
    """
    pattern = pattern.lower()
    value = value.lower()

    if pattern.startswith("*."):
        suffix = pattern[2:]  # "example.com"
        if value == suffix:
            return False  # *.example.com does NOT match example.com
        return value.endswith("." + suffix)

    return fnmatchcase(value.lower(), pattern.lower())


def tripwire_match(
    pattern: str,
    value: str,
    *,
    case_sensitive: bool = True,
) -> bool:
    """Match a glob pattern against a value with proper anchoring.

    Args:
        pattern: Glob pattern. Supports * (one segment) and ** (across segments).
        value: The actual string to match against.
        case_sensitive: If False, lowercase both sides before matching.
            Hostnames should always be case-insensitive (RFC 4343).

    Rules:
        - "*.example.com" matches "sub.example.com" but NOT "example.com"
          and NOT "evil-example.com"
        - "/api/**" matches "/api/v1/users" and "/api/v2/items/123"
        - "/api/*" matches "/api/v1" but NOT "/api/v1/users"
        - Exact strings (no wildcards) use equality
    """
    if not case_sensitive:
        pattern = pattern.lower()
        value = value.lower()

    # No wildcards: exact match
    if "*" not in pattern and "?" not in pattern and "[" not in pattern:
        return pattern == value

    # Host-style patterns: *.example.com must be anchored at subdomain boundary
    if pattern.startswith("*.") and "/" not in pattern:
        return _match_host_glob(pattern, value)

    # Path-style patterns with **: match across path segments
    if "**" in pattern:
        # Convert ** to match-anything, * to match-within-segment
        # Split on ** first, then handle * within each part
        # IMPORTANT: Use re.escape() on non-wildcard portions before constructing
        # the regex. Split pattern on *, escape each literal part, rejoin with
        # regex wildcards. This prevents regex injection from literal characters
        # like dots, brackets, etc.
        import re  # noqa: PLC0415

        parts = pattern.split("**")
        regex_parts = []
        for part in parts:
            # Within each part, * matches anything except /
            # Escape literal portions, then replace escaped \* back with [^/]*
            sub_parts = part.split("*")
            escaped = [re.escape(sp) for sp in sub_parts]
            converted = "[^/]*".join(escaped)
            regex_parts.append(converted)
        regex = ".*".join(regex_parts)
        return re.fullmatch(regex, value) is not None

    # Path-style pattern with single * only: * must not cross /
    if "/" in pattern:
        import re  # noqa: PLC0415

        sub_parts = pattern.split("*")
        escaped = [re.escape(sp) for sp in sub_parts]
        regex = "[^/]*".join(escaped)
        return re.fullmatch(regex, value) is not None

    # Non-path glob: standard fnmatchcase
    return fnmatchcase(value, pattern)
