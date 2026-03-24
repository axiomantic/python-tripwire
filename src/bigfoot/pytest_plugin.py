# src/bigfoot/pytest_plugin.py
"""pytest fixture registration for bigfoot."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from bigfoot._config import load_bigfoot_config
from bigfoot._context import (
    _current_test_verifier,
    _guard_active,
    _guard_allowlist,
    _guard_level,
    _guard_patches_installed,
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
    """Register bigfoot pytest markers."""
    config.addinivalue_line(
        "markers",
        "allow(*plugin_names): allow plugins to make real calls"
        " (bypasses guard and sandbox)",
    )
    config.addinivalue_line(
        "markers",
        'deny(*plugin_names): remove plugins from the allowlist (narrows an outer allow)',
    )


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

    # Process @pytest.mark.allow and @pytest.mark.deny
    marker_allowlist: frozenset[str] = frozenset()
    for mark in item.iter_markers("allow"):
        marker_allowlist = marker_allowlist | frozenset(mark.args)

    denylist: frozenset[str] = frozenset()
    for mark in item.iter_markers("deny"):
        denylist = denylist | frozenset(mark.args)

    # Validate names
    if marker_allowlist or denylist:
        from bigfoot._errors import BigfootConfigError  # noqa: PLC0415
        from bigfoot._registry import GUARD_ELIGIBLE_PREFIXES, VALID_PLUGIN_NAMES  # noqa: PLC0415

        valid = VALID_PLUGIN_NAMES | GUARD_ELIGIBLE_PREFIXES
        unknown = (marker_allowlist | denylist) - valid
        if unknown:
            raise BigfootConfigError(
                f"Unknown plugin name(s) in @pytest.mark.allow/deny: "
                f"{sorted(unknown)}. "
                f"Valid names: {sorted(valid)}"
            )

    # Merge existing fixture-set allowlist with marker allowlist, then subtract deny
    existing = _guard_allowlist.get()
    allowlist = (existing | marker_allowlist) - denylist

    level_token = _guard_level.set(guard_level)
    allowlist_token = _guard_allowlist.set(allowlist)
    guard_token = _guard_active.set(True)
    try:
        yield
    finally:
        _guard_active.reset(guard_token)
        _guard_allowlist.reset(allowlist_token)
        _guard_level.reset(level_token)
