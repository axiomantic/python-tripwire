"""Normalization for URLs, hostnames, and paths before firewall matching.

All normalization happens when constructing FirewallRequest objects,
BEFORE they reach the firewall engine. This prevents bypass via
encoding tricks, path traversal, or hostname aliasing.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import unquote, urlparse

# Localhost equivalence: these all refer to the local machine
_LOCALHOST_ALIASES: frozenset[str] = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "[::1]",
})


def normalize_host(host: str) -> str:
    """Normalize a hostname for consistent matching.

    - Lowercase (RFC 4343)
    - Strip brackets from IPv6 (e.g., [::1] -> ::1)
    - Resolve localhost aliases to canonical "localhost"
    """
    host = host.lower().strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    if host in _LOCALHOST_ALIASES:
        return "localhost"

    # Try to parse as IP and normalize
    try:
        addr = ipaddress.ip_address(host)
        normalized = str(addr)
        if normalized in _LOCALHOST_ALIASES:
            return "localhost"
        return normalized
    except ValueError:
        pass

    return host


def normalize_path(path: str) -> str:
    """Normalize a URL path.

    - Decode percent-encoding
    - Resolve .. and . segments
    - Collapse // to /
    - Strip trailing slash (except root /)
    """
    path = unquote(path)

    # Collapse double slashes
    while "//" in path:
        path = path.replace("//", "/")

    # Resolve . and ..
    segments = path.split("/")
    resolved: list[str] = []
    for seg in segments:
        if seg == ".":
            continue
        if seg == "..":
            if resolved and resolved[-1] != "":
                resolved.pop()
        else:
            resolved.append(seg)

    result = "/".join(resolved)
    if not result.startswith("/"):
        result = "/" + result

    # Strip trailing slash (except root)
    if result != "/" and result.endswith("/"):
        result = result[:-1]

    return result


def normalize_url(url: str) -> tuple[str, str, int, str]:
    """Parse and normalize a URL into (scheme, host, port, path).

    Default ports:
        http -> 80, https -> 443, ws -> 80, wss -> 443,
        redis -> 6379, postgresql -> 5432
    """
    parsed = urlparse(url)

    scheme = (parsed.scheme or "http").lower()
    host = normalize_host(parsed.hostname or "")

    default_ports = {
        "http": 80, "https": 443,
        "ws": 80, "wss": 443,
        "redis": 6379, "rediss": 6380,
        "postgresql": 5432, "postgres": 5432,
        "smtp": 25,
        "ssh": 22,
    }
    port = parsed.port or default_ports.get(scheme, 0)
    path = normalize_path(parsed.path or "/")

    return scheme, host, port, path
