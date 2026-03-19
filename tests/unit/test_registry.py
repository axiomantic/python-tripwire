"""Unit tests for bigfoot._registry: plugin registry and config resolution."""

from unittest.mock import patch

import pytest

from bigfoot._errors import BigfootConfigError
from bigfoot._registry import (
    PLUGIN_REGISTRY,
    VALID_PLUGIN_NAMES,
    PluginEntry,
    _is_available,
    get_plugin_class,
    resolve_enabled_plugins,
)

# ---------------------------------------------------------------------------
# Registry contents
# ---------------------------------------------------------------------------


def test_plugin_registry_contains_all_plugins() -> None:
    """PLUGIN_REGISTRY must contain exactly 27 entries (all interceptor plugins)."""
    assert len(PLUGIN_REGISTRY) == 27


def test_valid_plugin_names_matches_registry() -> None:
    """VALID_PLUGIN_NAMES must contain exactly the names from PLUGIN_REGISTRY."""
    expected = {
        "http",
        "subprocess",
        "popen",
        "smtp",
        "socket",
        "database",
        "async_websocket",
        "sync_websocket",
        "redis",
        "psycopg2",
        "asyncpg",
        "logging",
        "async_subprocess",
        "dns",
        "memcache",
        "celery",
        "boto3",
        "elasticsearch",
        "jwt",
        "crypto",
        "mongo",
        "file_io",
        "pika",
        "ssh",
        "grpc",
        "native",
        "mcp",
    }
    assert VALID_PLUGIN_NAMES == expected


def test_plugin_registry_entries_are_frozen() -> None:
    """Each entry in PLUGIN_REGISTRY must be a frozen dataclass (immutable)."""
    for entry in PLUGIN_REGISTRY:
        assert isinstance(entry, PluginEntry)
        with pytest.raises(AttributeError):
            entry.name = "changed"  # type: ignore[misc]


def test_plugin_registry_names_are_unique() -> None:
    """No two entries in PLUGIN_REGISTRY may share the same name."""
    names = [e.name for e in PLUGIN_REGISTRY]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------


def test_is_available_always_returns_true() -> None:
    """Plugins with availability_check='always' are always available."""
    entry = PluginEntry("test", "bigfoot.plugins.subprocess", "SubprocessPlugin", "always")
    assert _is_available(entry) is True


def test_is_available_httpx_requests_when_installed() -> None:
    """HttpPlugin availability check returns True when httpx and requests are installed."""
    # httpx and requests are in dev deps, so they should be available
    entry = PluginEntry("http", "bigfoot.plugins.http", "HttpPlugin", "httpx+requests")
    assert _is_available(entry) is True


def test_is_available_websockets_uses_flag() -> None:
    """Websockets availability check reads _WEBSOCKETS_AVAILABLE flag."""
    entry = PluginEntry(
        "async_websocket",
        "bigfoot.plugins.websocket_plugin",
        "AsyncWebSocketPlugin",
        "websockets",
    )
    # websockets is in dev deps, should be available
    assert _is_available(entry) is True


def test_is_available_redis_uses_flag() -> None:
    """Redis availability check reads _REDIS_AVAILABLE flag."""
    entry = PluginEntry(
        "redis", "bigfoot.plugins.redis_plugin", "RedisPlugin", "redis"
    )
    # redis is in dev deps, should be available
    assert _is_available(entry) is True


def test_is_available_unknown_check_returns_false() -> None:
    """Unknown availability_check values return False."""
    entry = PluginEntry("fake", "bigfoot.plugins.fake", "FakePlugin", "nonexistent_dep")
    assert _is_available(entry) is False


class TestIsAvailableConventions:
    """_is_available handles all four convention formats."""

    def test_always_available(self) -> None:
        entry = PluginEntry("test", "x.y", "X", "always")
        assert _is_available(entry) is True

    def test_single_module_available(self) -> None:
        entry = PluginEntry("test", "x.y", "X", "json")  # stdlib, always importable
        assert _is_available(entry) is True

    def test_single_module_unavailable(self) -> None:
        entry = PluginEntry("test", "x.y", "X", "nonexistent_module_xyz_abc")
        assert _is_available(entry) is False

    def test_multi_module_all_available(self) -> None:
        entry = PluginEntry("test", "x.y", "X", "json+os")
        assert _is_available(entry) is True

    def test_multi_module_one_missing(self) -> None:
        entry = PluginEntry("test", "x.y", "X", "json+nonexistent_module_xyz_abc")
        assert _is_available(entry) is False

    def test_flag_based_check(self) -> None:
        entry = PluginEntry("test", "x.y", "X", "flag:bigfoot.plugins.redis_plugin:_REDIS_AVAILABLE")
        result = _is_available(entry)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# get_plugin_class
# ---------------------------------------------------------------------------


def test_get_plugin_class_returns_correct_class() -> None:
    """get_plugin_class returns the actual class for a valid entry."""
    from bigfoot.plugins.subprocess import SubprocessPlugin

    entry = PluginEntry("subprocess", "bigfoot.plugins.subprocess", "SubprocessPlugin", "always")
    cls = get_plugin_class(entry)
    assert cls is SubprocessPlugin


def test_get_plugin_class_import_error_for_bad_path() -> None:
    """get_plugin_class raises ImportError for a nonexistent module."""
    entry = PluginEntry("fake", "bigfoot.plugins.nonexistent", "FakePlugin", "always")
    with pytest.raises(ImportError):
        get_plugin_class(entry)


# ---------------------------------------------------------------------------
# resolve_enabled_plugins
# ---------------------------------------------------------------------------


def test_resolve_enabled_plugins_default_returns_all_available() -> None:
    """With empty config, all available plugins are returned."""
    result = resolve_enabled_plugins({})
    names = {e.name for e in result}
    # All plugins with no optional deps should be present
    assert "subprocess" in names
    assert "popen" in names
    assert "smtp" in names
    assert "socket" in names
    assert "database" in names


def test_resolve_enabled_plugins_allowlist() -> None:
    """enabled_plugins returns only the listed plugins."""
    result = resolve_enabled_plugins({"enabled_plugins": ["subprocess", "popen"]})
    names = {e.name for e in result}
    assert names == {"subprocess", "popen"}


def test_resolve_enabled_plugins_blocklist() -> None:
    """disabled_plugins excludes the listed plugins."""
    result = resolve_enabled_plugins({"disabled_plugins": ["subprocess"]})
    names = {e.name for e in result}
    assert "subprocess" not in names
    # Other always-available plugins should be present
    assert "popen" in names
    assert "smtp" in names


def test_resolve_enabled_plugins_mutual_exclusion() -> None:
    """Both keys present raises BigfootConfigError."""
    with pytest.raises(BigfootConfigError, match="mutually exclusive"):
        resolve_enabled_plugins({
            "enabled_plugins": ["http"],
            "disabled_plugins": ["subprocess"],
        })


def test_resolve_enabled_plugins_unknown_name_in_enabled() -> None:
    """Unknown name in enabled_plugins raises BigfootConfigError."""
    with pytest.raises(BigfootConfigError, match="Unknown plugin name"):
        resolve_enabled_plugins({"enabled_plugins": ["nonexistent"]})


def test_resolve_enabled_plugins_unknown_name_in_disabled() -> None:
    """Unknown name in disabled_plugins raises BigfootConfigError."""
    with pytest.raises(BigfootConfigError, match="Unknown plugin name"):
        resolve_enabled_plugins({"disabled_plugins": ["nonexistent"]})


def test_resolve_enabled_plugins_invalid_type_string() -> None:
    """enabled_plugins as string (not list) raises BigfootConfigError."""
    with pytest.raises(BigfootConfigError, match="must be a list of strings"):
        resolve_enabled_plugins({"enabled_plugins": "http"})


def test_resolve_enabled_plugins_invalid_type_disabled_string() -> None:
    """disabled_plugins as string (not list) raises BigfootConfigError."""
    with pytest.raises(BigfootConfigError, match="must be a list of strings"):
        resolve_enabled_plugins({"disabled_plugins": "http"})


def test_resolve_enabled_plugins_skips_unavailable() -> None:
    """Enabled plugin list filters by availability."""
    result = resolve_enabled_plugins({"enabled_plugins": ["subprocess"]})
    names = {e.name for e in result}
    assert names == {"subprocess"}


class TestDefaultEnabled:
    """PluginEntry.default_enabled controls default inclusion."""

    def test_default_enabled_false_excluded_from_default(self) -> None:
        entry = PluginEntry("test_opt", "x.y", "X", "always", default_enabled=False)
        always_entry = PluginEntry("test_always", "x.y", "Y", "always", default_enabled=True)
        with patch("bigfoot._registry.PLUGIN_REGISTRY", (entry, always_entry)):
            with patch("bigfoot._registry.VALID_PLUGIN_NAMES", frozenset({"test_opt", "test_always"})):
                result = resolve_enabled_plugins({})
                names = [e.name for e in result]
                assert "test_opt" not in names
                assert "test_always" in names

    def test_default_enabled_false_included_when_explicit(self) -> None:
        entry = PluginEntry("test_opt", "x.y", "X", "always", default_enabled=False)
        with patch("bigfoot._registry.PLUGIN_REGISTRY", (entry,)):
            with patch("bigfoot._registry.VALID_PLUGIN_NAMES", frozenset({"test_opt"})):
                result = resolve_enabled_plugins({"enabled_plugins": ["test_opt"]})
                assert any(e.name == "test_opt" for e in result)


def test_resolve_enabled_plugins_error_lists_valid_names() -> None:
    """Error message for unknown names includes the list of valid names."""
    with pytest.raises(BigfootConfigError) as exc_info:
        resolve_enabled_plugins({"enabled_plugins": ["bogus"]})
    error_msg = str(exc_info.value)
    assert "subprocess" in error_msg
    assert "http" in error_msg
