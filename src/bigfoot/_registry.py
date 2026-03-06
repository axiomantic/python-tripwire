"""Plugin registry for always-on auto-activation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bigfoot._base_plugin import BasePlugin


@dataclass(frozen=True)
class PluginEntry:
    """Registry entry for a single plugin."""

    name: str  # canonical registry name (e.g., "http")
    import_path: str  # e.g., "bigfoot.plugins.http"
    class_name: str  # e.g., "HttpPlugin"
    availability_check: str  # module path or flag path to check availability


def _check_dep_available(module_name: str) -> bool:
    """Return True if a third-party dependency module is importable."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def _check_plugin_flag(import_path: str, flag_name: str) -> bool:
    """Import a plugin module and read its availability flag."""
    import importlib

    module = importlib.import_module(import_path)
    return getattr(module, flag_name, False)  # type: ignore[no-any-return]


def _is_available(entry: PluginEntry) -> bool:
    """Determine if a plugin's optional dependencies are satisfied.

    Reuses the existing _*_AVAILABLE flags from plugin modules where
    possible, avoiding redundant import attempts.
    """
    # Plugins with no optional deps are always available
    if entry.availability_check == "always":
        return True

    # Plugins whose modules set _*_AVAILABLE flags at import time.
    # These modules are already imported by bigfoot/__init__.py at
    # package init, so this is a cheap attribute read, not a new import.
    _flag_map: dict[str, tuple[str, str]] = {
        "websockets": ("bigfoot.plugins.websocket_plugin", "_WEBSOCKETS_AVAILABLE"),
        "websocket-client": ("bigfoot.plugins.websocket_plugin", "_WEBSOCKET_CLIENT_AVAILABLE"),
        "redis": ("bigfoot.plugins.redis_plugin", "_REDIS_AVAILABLE"),
    }
    if entry.availability_check in _flag_map:
        mod_path, flag = _flag_map[entry.availability_check]
        return _check_plugin_flag(mod_path, flag)

    # HttpPlugin: its module raises ImportError at import time if httpx/requests
    # are missing. Check the actual dependencies directly.
    if entry.availability_check == "httpx+requests":
        return _check_dep_available("httpx") and _check_dep_available("requests")

    return False


# Registry of all interceptor plugins (excludes MockPlugin).
PLUGIN_REGISTRY: tuple[PluginEntry, ...] = (
    PluginEntry("http", "bigfoot.plugins.http", "HttpPlugin", "httpx+requests"),
    PluginEntry("subprocess", "bigfoot.plugins.subprocess", "SubprocessPlugin", "always"),
    PluginEntry("popen", "bigfoot.plugins.popen_plugin", "PopenPlugin", "always"),
    PluginEntry("smtp", "bigfoot.plugins.smtp_plugin", "SmtpPlugin", "always"),
    PluginEntry("socket", "bigfoot.plugins.socket_plugin", "SocketPlugin", "always"),
    PluginEntry("database", "bigfoot.plugins.database_plugin", "DatabasePlugin", "always"),
    PluginEntry(
        "async_websocket",
        "bigfoot.plugins.websocket_plugin",
        "AsyncWebSocketPlugin",
        "websockets",
    ),
    PluginEntry(
        "sync_websocket",
        "bigfoot.plugins.websocket_plugin",
        "SyncWebSocketPlugin",
        "websocket-client",
    ),
    PluginEntry("redis", "bigfoot.plugins.redis_plugin", "RedisPlugin", "redis"),
)

VALID_PLUGIN_NAMES: frozenset[str] = frozenset(e.name for e in PLUGIN_REGISTRY)


def get_plugin_class(entry: PluginEntry) -> type[BasePlugin]:
    """Import and return the plugin class for a registry entry."""
    import importlib

    module = importlib.import_module(entry.import_path)
    return getattr(module, entry.class_name)  # type: ignore[no-any-return]


def resolve_enabled_plugins(
    config: dict[str, object],
) -> list[PluginEntry]:
    """Determine which plugins to auto-instantiate based on config.

    Config keys (mutually exclusive):
    - enabled_plugins: list[str] - allowlist (only these)
    - disabled_plugins: list[str] - blocklist (all except these)
    - neither: all available plugins

    Raises BigfootConfigError for:
    - Both keys present
    - Unknown plugin names
    - Invalid types (not a list)
    """
    from bigfoot._errors import BigfootConfigError

    enabled = config.get("enabled_plugins")
    disabled = config.get("disabled_plugins")

    if enabled is not None and disabled is not None:
        raise BigfootConfigError(
            "enabled_plugins and disabled_plugins are mutually exclusive. "
            "Use one or the other, not both."
        )

    # Type validation: must be lists if present
    if enabled is not None and not isinstance(enabled, list):
        raise BigfootConfigError(
            f"enabled_plugins must be a list of strings, got {type(enabled).__name__}"
        )
    if disabled is not None and not isinstance(disabled, list):
        raise BigfootConfigError(
            f"disabled_plugins must be a list of strings, got {type(disabled).__name__}"
        )

    if enabled is not None:
        unknown = set(enabled) - VALID_PLUGIN_NAMES
        if unknown:
            raise BigfootConfigError(
                f"Unknown plugin name(s) in enabled_plugins: {sorted(unknown)}. "
                f"Valid names: {sorted(VALID_PLUGIN_NAMES)}"
            )
        return [e for e in PLUGIN_REGISTRY if e.name in enabled and _is_available(e)]

    if disabled is not None:
        unknown = set(disabled) - VALID_PLUGIN_NAMES
        if unknown:
            raise BigfootConfigError(
                f"Unknown plugin name(s) in disabled_plugins: {sorted(unknown)}. "
                f"Valid names: {sorted(VALID_PLUGIN_NAMES)}"
            )
        return [e for e in PLUGIN_REGISTRY if e.name not in disabled and _is_available(e)]

    # Default: all available plugins
    return [e for e in PLUGIN_REGISTRY if _is_available(e)]
