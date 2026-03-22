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
    default_enabled: bool = True  # False for opt-in plugins (e.g., file I/O, ctypes/cffi)


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
    return getattr(module, flag_name, False)


def _is_available(entry: PluginEntry) -> bool:
    """Determine if a plugin's optional dependencies are satisfied.

    Convention-based availability check using the availability_check field:
    - "always": No optional deps; always available
    - "<module_name>": Single module; try to import it
    - "<mod1>+<mod2>": Multiple modules; all must be importable
    - "flag:<import_path>:<flag_name>": Read a boolean flag from a plugin module
    """
    check = entry.availability_check

    if check == "always":
        return True

    if check.startswith("flag:"):
        _, import_path, flag_name = check.split(":", 2)
        return _check_plugin_flag(import_path, flag_name)

    if "+" in check:
        return all(_check_dep_available(m) for m in check.split("+"))

    # Default: single module check
    return _check_dep_available(check)


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
        "flag:bigfoot.plugins.websocket_plugin:_WEBSOCKET_CLIENT_AVAILABLE",
    ),
    PluginEntry("redis", "bigfoot.plugins.redis_plugin", "RedisPlugin", "redis"),
    PluginEntry("psycopg2", "bigfoot.plugins.psycopg2_plugin", "Psycopg2Plugin", "psycopg2"),
    PluginEntry("asyncpg", "bigfoot.plugins.asyncpg_plugin", "AsyncpgPlugin", "asyncpg"),
    PluginEntry("logging", "bigfoot.plugins.logging_plugin", "LoggingPlugin", "always"),
    PluginEntry(
        "async_subprocess",
        "bigfoot.plugins.async_subprocess_plugin",
        "AsyncSubprocessPlugin",
        "always",
    ),
    PluginEntry("dns", "bigfoot.plugins.dns_plugin", "DnsPlugin", "always"),
    PluginEntry("memcache", "bigfoot.plugins.memcache_plugin", "MemcachePlugin", "pymemcache"),
    PluginEntry("celery", "bigfoot.plugins.celery_plugin", "CeleryPlugin", "celery"),
    PluginEntry("boto3", "bigfoot.plugins.boto3_plugin", "Boto3Plugin", "boto3"),
    PluginEntry(
        "elasticsearch",
        "bigfoot.plugins.elasticsearch_plugin",
        "ElasticsearchPlugin",
        "elasticsearch",
    ),
    PluginEntry("jwt", "bigfoot.plugins.jwt_plugin", "JwtPlugin", "jwt"),
    PluginEntry("crypto", "bigfoot.plugins.crypto_plugin", "CryptoPlugin", "cryptography"),
    PluginEntry("mongo", "bigfoot.plugins.mongo_plugin", "MongoPlugin", "pymongo"),
    PluginEntry(
        "file_io", "bigfoot.plugins.file_io_plugin", "FileIoPlugin",
        "always", default_enabled=False,
    ),
    PluginEntry("pika", "bigfoot.plugins.pika_plugin", "PikaPlugin", "pika"),
    PluginEntry("ssh", "bigfoot.plugins.ssh_plugin", "SshPlugin", "paramiko"),
    PluginEntry("grpc", "bigfoot.plugins.grpc_plugin", "GrpcPlugin", "grpc"),
    PluginEntry("mcp", "bigfoot.plugins.mcp_plugin", "McpPlugin", "mcp"),
    PluginEntry(
        "native", "bigfoot.plugins.native_plugin", "NativePlugin",
        "always", default_enabled=False,
    ),
)

VALID_PLUGIN_NAMES: frozenset[str] = frozenset(e.name for e in PLUGIN_REGISTRY)

# Source-ID prefixes used by guard-eligible plugins (supports_guard=True,
# default_enabled=True). Guard mode blocks calls whose source_id starts with
# one of these prefixes. Prefixes that don't match registry names (e.g., "db"
# for the "database" plugin) are included explicitly.
#
# Non-guard plugins (logging, jwt, crypto, celery) and opt-in plugins
# (file_io, native) are NOT included. MockPlugin source_ids start with
# "mock:" which is also not included.
GUARD_ELIGIBLE_PREFIXES: frozenset[str] = frozenset({
    "http",          # HttpPlugin
    "subprocess",    # SubprocessPlugin, PopenPlugin
    "smtp",          # SmtpPlugin
    "socket",        # SocketPlugin
    "db",            # DatabasePlugin (source_id: "db:connect", "db:execute", ...)
    "database",      # DatabasePlugin (registry name, for allow() compatibility)
    "websocket",     # AsyncWebSocketPlugin, SyncWebSocketPlugin
    "async_websocket",  # registry name
    "sync_websocket",   # registry name
    "redis",         # RedisPlugin
    "psycopg2",      # Psycopg2Plugin
    "asyncpg",       # AsyncpgPlugin
    "asyncio",       # AsyncSubprocessPlugin (source_id: "asyncio:subprocess:spawn")
    "async_subprocess",  # registry name
    "dns",           # DnsPlugin
    "memcache",      # MemcachePlugin
    "boto3",         # Boto3Plugin
    "elasticsearch", # ElasticsearchPlugin
    "mongo",         # MongoPlugin
    "pika",          # PikaPlugin
    "ssh",           # SshPlugin
    "grpc",          # GrpcPlugin
    "mcp",           # McpPlugin
    "popen",         # PopenPlugin (registry name)
})


def get_plugin_class(entry: PluginEntry) -> type[BasePlugin]:
    """Import and return the plugin class for a registry entry."""
    import importlib

    module = importlib.import_module(entry.import_path)
    cls: type[BasePlugin] = getattr(module, entry.class_name)
    return cls


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
        result = []
        for e in PLUGIN_REGISTRY:
            if e.name in enabled:
                if not _is_available(e):
                    raise BigfootConfigError(
                        f"Plugin '{e.name}' is in enabled_plugins but its "
                        f"dependency '{e.availability_check}' is not installed. "
                        f"Install with: pip install bigfoot[{e.name}]"
                    )
                result.append(e)
        return result

    if disabled is not None:
        unknown = set(disabled) - VALID_PLUGIN_NAMES
        if unknown:
            raise BigfootConfigError(
                f"Unknown plugin name(s) in disabled_plugins: {sorted(unknown)}. "
                f"Valid names: {sorted(VALID_PLUGIN_NAMES)}"
            )
        return [
            e for e in PLUGIN_REGISTRY
            if e.name not in disabled and e.default_enabled and _is_available(e)
        ]

    # Default: all available plugins that are default-enabled
    return [e for e in PLUGIN_REGISTRY if e.default_enabled and _is_available(e)]
