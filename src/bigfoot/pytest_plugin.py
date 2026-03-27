# src/bigfoot/pytest_plugin.py
"""pytest fixture registration for bigfoot."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from bigfoot._config import load_bigfoot_config
from bigfoot._context import (
    _current_test_verifier,
    _guard_active,
    _guard_level,
    _guard_patches_installed,
)
from bigfoot._context_propagation import (
    install_context_propagation,
    uninstall_context_propagation,
)
from bigfoot._verifier import StrictVerifier

_VALID_GUARD_LEVELS = frozenset({"warn", "error", "strict"})


def _resolve_guard_level(config: dict[str, object]) -> str:
    """Parse the guard config value into a normalized level string.

    Returns one of: "warn", "error", "off".
    Raises BigfootConfigError for invalid values.
    """
    from bigfoot._errors import BigfootConfigError  # noqa: PLC0415

    raw = config.get("guard", "warn")  # default changed from True to "warn"

    if raw is True:
        raise BigfootConfigError(
            'guard = true is ambiguous. '
            'Use guard = "warn", guard = "error", or guard = false.\n'
            'Valid values: "warn", "error", "strict", false'
        )

    if raw is False:
        return "off"

    if isinstance(raw, str):
        normalized = raw.lower()
        if normalized in ("error", "strict"):
            return "error"
        if normalized == "warn":
            return "warn"
        raise BigfootConfigError(
            f'Invalid guard value: {raw!r}. '
            f'Valid values: "warn", "error", "strict", false'
        )

    raise BigfootConfigError(
        f"guard must be a string or false, got {type(raw).__name__}: {raw!r}"
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register bigfoot pytest markers and install context propagation."""
    config.addinivalue_line(
        "markers",
        "allow(*rules): allow protocols/patterns (str or M()) to bypass guard mode",
    )
    config.addinivalue_line(
        "markers",
        "deny(*rules): deny protocols/patterns (str or M()) in guard mode",
    )
    install_context_propagation()


def pytest_unconfigure(config: pytest.Config) -> None:
    """Clean up bigfoot patches."""
    uninstall_context_propagation()


@pytest.fixture(autouse=True)
def _bigfoot_auto_verifier() -> Generator[StrictVerifier, None, None]:
    """Auto-use fixture: creates a StrictVerifier for each test, invisible to test authors.

    verify_all() is called at teardown automatically. The sandbox is NOT automatically
    activated -- the test (or module-level bigfoot.sandbox()) controls sandbox lifetime.
    """
    StrictVerifier._suppress_direct_warning = True
    try:
        verifier = StrictVerifier()
    finally:
        StrictVerifier._suppress_direct_warning = False
    token = _current_test_verifier.set(verifier)
    yield verifier
    _current_test_verifier.reset(token)
    verifier.verify_all()


@pytest.fixture
def bigfoot_verifier(_bigfoot_auto_verifier: StrictVerifier) -> StrictVerifier:
    """Explicit fixture for tests that need direct access to the verifier.

    Usage:
        def test_something(bigfoot_verifier):
            http = HttpPlugin(bigfoot_verifier)
            http.mock_response("GET", "https://api.example.com/data", json={})
            with bigfoot_verifier.sandbox():
                response = httpx.get("https://api.example.com/data")
                bigfoot_verifier.assert_interaction(http.request, method="GET")
    """
    return _bigfoot_auto_verifier


@pytest.fixture(autouse=True, scope="session")
def _bigfoot_guard_patches() -> Generator[None, None, None]:
    """Install I/O plugin patches at session start for guard mode.

    Only installs patches for plugins that:
    - Have their dependencies available
    - Have supports_guard = True
    - Are default_enabled (not opt-in plugins like file_io, native)

    Uses the existing reference-counting activate/deactivate mechanism.
    At session teardown, all activated plugins are deactivated.

    The ``_guard_patches_installed`` ContextVar is set so interceptors pass
    through to originals when neither sandbox nor guard is active (e.g.,
    during fixture setup/teardown).
    """
    config = load_bigfoot_config()
    guard_level = _resolve_guard_level(config)
    if guard_level == "off":
        yield
        return

    from bigfoot._base_plugin import BasePlugin
    from bigfoot._registry import PLUGIN_REGISTRY, _is_available, get_plugin_class

    activated: list[BasePlugin] = []

    for entry in PLUGIN_REGISTRY:
        if not entry.default_enabled:
            continue
        if not _is_available(entry):
            continue
        try:
            plugin_cls = get_plugin_class(entry)
            if not getattr(plugin_cls, "supports_guard", True):
                continue
            # Create minimal plugin instance just for activate/deactivate.
            # __new__ skips __init__; activate() uses ClassVars for patch
            # installation via reference counting, so no verifier is needed.
            plugin = plugin_cls.__new__(plugin_cls)
            plugin.activate()
            activated.append(plugin)
        except Exception:
            import warnings

            warnings.warn(
                f"bigfoot: guard mode failed to activate plugin {entry.name!r}",
                stacklevel=1,
            )

    patches_token = _guard_patches_installed.set(True)

    yield

    _guard_patches_installed.reset(patches_token)

    for plugin in reversed(activated):
        try:
            plugin.deactivate()
        except Exception:
            pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """Activate guard mode during the test call only.

    This hook wraps the actual test function call (not setup or teardown),
    ensuring guard mode is precisely scoped to the test body. Using a hook
    instead of a fixture prevents guard from interfering with fixture
    setup/teardown (e.g., pytest-asyncio's event loop cleanup).

    The ``@pytest.mark.allow("dns", "socket")`` mark pre-populates the
    allowlist for the test. Multiple marks combine via union.

    Note: This hook only activates the guard ContextVars. Patch installation
    is handled by ``_bigfoot_guard_patches`` (session-scoped). Per-test
    plugin cleanup fixtures may reset install counts to 0, which removes
    the session fixture's patches for that test. In that case, guard mode
    is still active but only effective for plugins whose interceptors are
    installed (e.g., via sandbox activation within the test).
    """
    config = load_bigfoot_config()
    guard_level = _resolve_guard_level(config)
    if guard_level == "off":
        yield
        return

    # Reject legacy guard_allow config key
    if "guard_allow" in config:
        from bigfoot._errors import BigfootConfigError  # noqa: PLC0415

        raise BigfootConfigError(
            "The guard_allow config key has been replaced by [tool.bigfoot.firewall]. "
            "Migration: guard_allow = [\"http\", \"dns\"] becomes "
            "[tool.bigfoot.firewall]\\nallow = [\"http:*\", \"dns:*\"]"
        )

    from bigfoot._firewall import (  # noqa: PLC0415
        Disposition,
        FirewallRule,
        _firewall_stack,
    )
    from bigfoot._match import M  # noqa: PLC0415

    frames: list[FirewallRule] = []

    # ---------------------------------------------------------------
    # Layer 0: Global TOML rules from [tool.bigfoot.firewall]
    # ---------------------------------------------------------------
    firewall_config = config.get("firewall", {})
    if not isinstance(firewall_config, dict):
        from bigfoot._errors import BigfootConfigError  # noqa: PLC0415

        raise BigfootConfigError(
            f"firewall config must be a table/dict, got {type(firewall_config).__name__}"
        )

    # Global allow rules
    for rule_str in firewall_config.get("allow", []):
        frames.append(
            FirewallRule(pattern=_parse_toml_rule(rule_str), disposition=Disposition.ALLOW)
        )

    # Global deny rules
    for rule_str in firewall_config.get("deny", []):
        frames.append(
            FirewallRule(pattern=_parse_toml_rule(rule_str), disposition=Disposition.DENY)
        )

    # Structured protocol sections (e.g., [tool.bigfoot.firewall.http])
    structured_protocols = ("http", "redis", "subprocess", "boto3", "socket", "file_io")
    for proto in structured_protocols:
        proto_section = firewall_config.get(proto, {})
        if not isinstance(proto_section, dict):
            continue
        for rule_str in proto_section.get("allow", []):
            frames.append(
                FirewallRule(
                    pattern=_parse_toml_rule(f"{proto}:{rule_str}"),
                    disposition=Disposition.ALLOW,
                )
            )
        for rule_str in proto_section.get("deny", []):
            frames.append(
                FirewallRule(
                    pattern=_parse_toml_rule(f"{proto}:{rule_str}"),
                    disposition=Disposition.DENY,
                )
            )

    # ---------------------------------------------------------------
    # Layer 1: Per-file TOML overrides
    # ---------------------------------------------------------------
    per_file = firewall_config.get("per-file-allow", {})
    if isinstance(per_file, dict) and per_file:
        test_path = str(item.fspath)
        for glob_pattern, rules in per_file.items():
            if _path_matches_glob(test_path, glob_pattern):
                if isinstance(rules, list):
                    for rule_str in rules:
                        frames.append(
                            FirewallRule(
                                pattern=_parse_toml_rule(rule_str),
                                disposition=Disposition.ALLOW,
                            )
                        )

    # ---------------------------------------------------------------
    # Layer 2: @pytest.mark.allow / @pytest.mark.deny
    # ---------------------------------------------------------------
    for mark in item.iter_markers("deny"):
        for arg in mark.args:
            if isinstance(arg, M):
                frames.append(FirewallRule(pattern=arg, disposition=Disposition.DENY))
            else:
                frames.append(
                    FirewallRule(pattern=M(protocol=str(arg)), disposition=Disposition.DENY)
                )

    for mark in item.iter_markers("allow"):
        for arg in mark.args:
            if isinstance(arg, M):
                frames.append(FirewallRule(pattern=arg, disposition=Disposition.ALLOW))
            else:
                frames.append(
                    FirewallRule(pattern=M(protocol=str(arg)), disposition=Disposition.ALLOW)
                )

    current_stack = _firewall_stack.get()
    new_stack = current_stack.push(*frames) if frames else current_stack
    firewall_token = _firewall_stack.set(new_stack)

    level_token = _guard_level.set(guard_level)
    guard_token = _guard_active.set(True)
    try:
        yield
    finally:
        _guard_active.reset(guard_token)
        _guard_level.reset(level_token)
        _firewall_stack.reset(firewall_token)


def _parse_toml_rule(rule_str: str) -> M:  # type: ignore[name-defined]  # noqa: F821
    """Parse a TOML rule string into an M() pattern."""
    from bigfoot._match import M  # noqa: PLC0415

    # Protocol:wildcard shorthand
    if ":" in rule_str and "//" not in rule_str:
        protocol, _, pattern = rule_str.partition(":")
        if pattern == "*":
            return M(protocol=protocol)
        if protocol == "subprocess":
            return M(protocol="subprocess", binary=pattern)
        if protocol == "memcache":
            return M(protocol="memcache", command=pattern)
        if protocol == "file_io":
            return M(protocol="file_io", path=pattern)
        if protocol == "boto3":
            parts = pattern.split(":")
            if len(parts) == 2:  # noqa: PLR2004
                return M(protocol="boto3", service=parts[0], operation=parts[1])
            return M(protocol="boto3", service=parts[0])
        return M(protocol=protocol)

    # URL-style: scheme://host[:port][/path]
    from urllib.parse import urlparse  # noqa: PLC0415

    parsed = urlparse(rule_str)
    scheme = parsed.scheme
    kwargs: dict[str, object] = {}
    protocol_map = {
        "http": "http",
        "https": "http",
        "redis": "redis",
        "rediss": "redis",
        "ws": "websocket",
        "wss": "websocket",
        "postgresql": "psycopg2",
        "postgres": "psycopg2",
        "smtp": "smtp",
        "ssh": "ssh",
    }
    protocol = protocol_map.get(scheme, scheme)
    kwargs["protocol"] = protocol
    if parsed.hostname:
        kwargs["host"] = parsed.hostname
    if parsed.port:
        kwargs["port"] = parsed.port
    if parsed.path and parsed.path != "/":
        if protocol == "redis" and parsed.path.lstrip("/").isdigit():
            kwargs["db"] = int(parsed.path.lstrip("/"))
        else:
            kwargs["path"] = parsed.path
    return M(**kwargs)  # type: ignore[arg-type]


def _path_matches_glob(test_path: str, glob_pattern: str) -> bool:
    """Check if a test path matches a glob pattern."""
    from bigfoot._glob import bigfoot_match  # noqa: PLC0415

    return bigfoot_match(glob_pattern, test_path)
