"""Plugin registry for always-on auto-activation."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from tripwire._base_plugin import BasePlugin


@dataclass(frozen=True)
class PluginEntry:
    """Registry entry for a single plugin."""

    name: str  # canonical registry name (e.g., "http")
    import_path: str  # e.g., "tripwire.plugins.http"
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
    PluginEntry("http", "tripwire.plugins.http", "HttpPlugin", "httpx+requests"),
    PluginEntry("subprocess", "tripwire.plugins.subprocess", "SubprocessPlugin", "always"),
    PluginEntry("popen", "tripwire.plugins.popen_plugin", "PopenPlugin", "always"),
    PluginEntry("smtp", "tripwire.plugins.smtp_plugin", "SmtpPlugin", "always"),
    PluginEntry("socket", "tripwire.plugins.socket_plugin", "SocketPlugin", "always"),
    PluginEntry("database", "tripwire.plugins.database_plugin", "DatabasePlugin", "always"),
    PluginEntry(
        "async_websocket",
        "tripwire.plugins.websocket_plugin",
        "AsyncWebSocketPlugin",
        "websockets",
    ),
    PluginEntry(
        "sync_websocket",
        "tripwire.plugins.websocket_plugin",
        "SyncWebSocketPlugin",
        "flag:tripwire.plugins.websocket_plugin:_WEBSOCKET_CLIENT_AVAILABLE",
    ),
    PluginEntry("redis", "tripwire.plugins.redis_plugin", "RedisPlugin", "redis"),
    PluginEntry("psycopg2", "tripwire.plugins.psycopg2_plugin", "Psycopg2Plugin", "psycopg2"),
    PluginEntry("asyncpg", "tripwire.plugins.asyncpg_plugin", "AsyncpgPlugin", "asyncpg"),
    PluginEntry("logging", "tripwire.plugins.logging_plugin", "LoggingPlugin", "always"),
    PluginEntry(
        "async_subprocess",
        "tripwire.plugins.async_subprocess_plugin",
        "AsyncSubprocessPlugin",
        "always",
    ),
    PluginEntry("dns", "tripwire.plugins.dns_plugin", "DnsPlugin", "always"),
    PluginEntry("memcache", "tripwire.plugins.memcache_plugin", "MemcachePlugin", "pymemcache"),
    PluginEntry("celery", "tripwire.plugins.celery_plugin", "CeleryPlugin", "celery"),
    PluginEntry("boto3", "tripwire.plugins.boto3_plugin", "Boto3Plugin", "boto3"),
    PluginEntry(
        "elasticsearch",
        "tripwire.plugins.elasticsearch_plugin",
        "ElasticsearchPlugin",
        "elasticsearch",
    ),
    PluginEntry("jwt", "tripwire.plugins.jwt_plugin", "JwtPlugin", "jwt"),
    PluginEntry("crypto", "tripwire.plugins.crypto_plugin", "CryptoPlugin", "cryptography"),
    PluginEntry("mongo", "tripwire.plugins.mongo_plugin", "MongoPlugin", "pymongo"),
    PluginEntry(
        "file_io", "tripwire.plugins.file_io_plugin", "FileIoPlugin",
        "always", default_enabled=False,
    ),
    PluginEntry("pika", "tripwire.plugins.pika_plugin", "PikaPlugin", "pika"),
    PluginEntry("ssh", "tripwire.plugins.ssh_plugin", "SshPlugin", "paramiko"),
    PluginEntry("grpc", "tripwire.plugins.grpc_plugin", "GrpcPlugin", "grpc"),
    PluginEntry("mcp", "tripwire.plugins.mcp_plugin", "McpPlugin", "mcp"),
    PluginEntry(
        "native", "tripwire.plugins.native_plugin", "NativePlugin",
        "always", default_enabled=False,
    ),
)

VALID_PLUGIN_NAMES: frozenset[str] = frozenset(e.name for e in PLUGIN_REGISTRY)

def get_plugin_class(entry: PluginEntry) -> type[BasePlugin]:
    """Import and return the plugin class for a registry entry."""
    import importlib

    module = importlib.import_module(entry.import_path)
    cls: type[BasePlugin] = getattr(module, entry.class_name)
    return cls


# ---------------------------------------------------------------------------
# Hot-path lookup cache
#
# ``lookup_plugin_class_by_name`` runs on EVERY intercepted call from a
# sandbox (every subprocess.run, socket.send, logging.debug, etc.) via
# ``get_verifier_or_raise``. The uncached implementation iterates the
# registry, runs availability checks (which import modules), and imports
# the plugin class on every call: O(N) work over ~27 plugins per dispatch.
#
# Built-in plugins from ``PLUGIN_REGISTRY`` are eagerly seeded at module
# import time (see ``_populate_lookup_cache()`` below) so canonical names
# and every declared ``guard_prefix`` resolve via a lock-free
# ``dict.get`` on the read path. Third-party plugins registered via the
# ``tripwire.plugins`` entry-point group are NOT eagerly seeded — doing
# so would cost an ``importlib.metadata.entry_points`` call on every
# import, and the entry-point list cannot be enumerated to extract
# canonical names without loading every plugin class. They are
# discovered lazily on the first cache miss for an unknown name.
#
# Thread-safety contract: after import-time population, the built-in
# read path is LOCK-FREE. Only never-before-seen names take
# ``_lookup_cache_lock``, which serves two purposes: (a) probing
# entry-point plugins and seeding their canonical name + guard_prefixes
# on first observation, and (b) negatively caching a None result so
# concurrent callers don't all repeat the discovery walk. Stock
# CPython's GIL serializes dict reads; the free-threaded build (PEP
# 703) makes ``dict.get`` of a stable key atomic, and the cache is
# effectively immutable for known names after first observation.
#
# Tests that monkeypatch ``PLUGIN_REGISTRY`` or stub
# ``importlib.metadata.entry_points`` MUST clear the cache via
# ``_clear_lookup_cache()`` for their patch to take effect.
# ---------------------------------------------------------------------------

_UNSET: Final[object] = object()
_lookup_cache: dict[str, tuple[type[BasePlugin], str] | None] = {}
_lookup_cache_lock: Final[threading.Lock] = threading.Lock()


def _populate_lookup_cache() -> None:
    """Eagerly seed ``_lookup_cache`` from ``PLUGIN_REGISTRY``.

    For every available entry, this imports the plugin class and inserts
    cache entries keyed by both the canonical registry name and every
    declared ``guard_prefixes`` value. After this runs, all known plugin
    names and prefixes resolve via a lock-free ``dict.get`` on the read
    path. Unknown names still fall through to the locked negative-cache
    slow path in ``lookup_plugin_class_by_name``.

    Called exactly once at module import time. Also called by
    ``_clear_lookup_cache`` to restore the eager state after a test
    invalidates the cache.

    Failures to import a single plugin class are swallowed: the entry is
    simply not seeded, and a subsequent lookup will go through the slow
    path (and likely return None for that name). This preserves the
    original lazy behavior for broken/partially-installed plugins.
    """
    for entry in PLUGIN_REGISTRY:
        if not _is_available(entry):
            continue
        try:
            cls = get_plugin_class(entry)
        except Exception:
            continue
        payload: tuple[type[BasePlugin], str] = (cls, entry.name)
        _lookup_cache[entry.name] = payload
        for prefix in getattr(cls, "guard_prefixes", ()):
            # Canonical name takes precedence if a prefix collides with
            # another plugin's canonical name; do not overwrite.
            _lookup_cache.setdefault(prefix, payload)


def _clear_lookup_cache() -> None:
    """Drop all cached ``lookup_plugin_class_by_name`` results, then
    re-seed the eager entries.

    Call this from tests that monkeypatch ``PLUGIN_REGISTRY`` so their
    substitute registry is consulted instead of stale cache entries.
    The re-population step ensures that the lock-free read path remains
    valid for normal (unpatched) registry entries after the test
    teardown clears the cache. Tests that monkeypatch ``PLUGIN_REGISTRY``
    will pick up the patched registry on the next ``_populate_lookup_cache``
    invocation, since this function reads ``PLUGIN_REGISTRY`` at call
    time rather than at import time.
    """
    with _lookup_cache_lock:
        _lookup_cache.clear()
        _populate_lookup_cache()


def _discover_entrypoint_plugin(
    plugin_name: str,
) -> tuple[type[BasePlugin], str] | None:
    """Probe ``tripwire.plugins`` entry points for ``plugin_name``.

    Iterates ``importlib.metadata.entry_points(group="tripwire.plugins")``
    and, for each entry point, loads the plugin class and checks whether
    its canonical name (``entry_point.name``) or any ``guard_prefixes``
    on the class match ``plugin_name``. Returns the first match's
    ``(plugin_class, canonical_name)`` tuple, or None if no entry point
    matches.

    On match, the caller is responsible for seeding ``_lookup_cache``
    under both the canonical name and every declared ``guard_prefix`` so
    subsequent lookups hit the cache without re-walking entry points.

    Failures to load an individual entry point (ImportError, broken
    plugin) are swallowed: the entry point is simply skipped. This
    matches the behavior of ``StrictVerifier._load_entrypoint_plugins``
    so a broken third-party plugin does not poison built-in lookups.
    """
    from importlib.metadata import entry_points  # noqa: PLC0415

    for ep in entry_points(group="tripwire.plugins"):
        try:
            plugin_cls = ep.load()
        except Exception:
            continue
        canonical = ep.name
        if plugin_name == canonical:
            return (plugin_cls, canonical)
        if plugin_name in getattr(plugin_cls, "guard_prefixes", ()):
            return (plugin_cls, canonical)
    return None


def _seed_entrypoint_match(
    payload: tuple[type[BasePlugin], str],
) -> None:
    """Insert ``payload`` under its canonical name and every declared
    ``guard_prefix`` in ``_lookup_cache``.

    MUST be called with ``_lookup_cache_lock`` held. Canonical name
    takes precedence: a ``guard_prefix`` that collides with another
    plugin's canonical name does NOT overwrite the existing entry, to
    preserve the same precedence rule used for built-in plugins in
    ``_populate_lookup_cache``.
    """
    cls, canonical = payload
    _lookup_cache[canonical] = payload
    for prefix in getattr(cls, "guard_prefixes", ()):
        _lookup_cache.setdefault(prefix, payload)


def lookup_plugin_class_by_name(
    plugin_name: str,
) -> tuple[type[BasePlugin], str] | None:
    """Return ``(plugin_class, canonical_registry_name)`` registered under
    ``plugin_name``, or None.

    Looks up by canonical registry name first, then by any ``guard_prefixes``
    declared on a registered plugin class. Returns None when no plugin
    matches or when its optional dependency is missing. Callers use this
    from outside any active sandbox to ask "what plugin would receive a
    call from this source_id?".

    The canonical name is ``entry.name`` (the registry name, e.g.
    ``"database"``). It may differ from ``plugin_name`` when ``plugin_name``
    matches a ``guard_prefix`` instead (e.g., ``plugin_name="db"`` resolves
    to ``("DatabasePlugin", "database")``). Callers MUST use the canonical
    name when looking up per-protocol guard overrides and when populating
    ``plugin_name`` on errors so the user sees the registry name.

    Resolution order:

    1. Built-in plugins seeded from ``PLUGIN_REGISTRY`` at import time.
       Hit on a lock-free ``dict.get``.
    2. Third-party plugins registered via the ``tripwire.plugins`` entry
       point group. Discovered lazily on the first cache miss for a
       given name; once observed, the canonical name and every declared
       ``guard_prefix`` are seeded into ``_lookup_cache`` so subsequent
       lookups also hit the lock-free fast path.
    3. Negative cache: if neither built-in nor entry-point discovery
       finds a match, the name is seeded with ``None`` to avoid
       re-walking entry points on every miss.

    Tests that mutate ``PLUGIN_REGISTRY`` or stub
    ``importlib.metadata.entry_points`` must call
    ``_clear_lookup_cache()`` for changes to be observed.
    """
    # Lock-free fast path. After import-time population, every built-in
    # canonical name and guard_prefix is a hit; only unseen names miss.
    cached = _lookup_cache.get(plugin_name, _UNSET)
    if cached is not _UNSET:
        # mypy cannot narrow through the sentinel object; we know the
        # cached value is the tuple-or-None payload, not the sentinel.
        return cached  # type: ignore[return-value]

    # Slow path: name not in cache. Take the lock so concurrent callers
    # don't all repeat the entry-point walk for the same name.
    with _lookup_cache_lock:
        # Re-check: another thread may have raced us through the lock
        # and populated the entry already.
        cached = _lookup_cache.get(plugin_name, _UNSET)
        if cached is not _UNSET:
            return cached  # type: ignore[return-value]

        # Discover third-party plugins via entry points. This is the
        # path that fixes "third-party plugins are invisible to
        # ``get_verifier_or_raise`` outside a sandbox" — without it,
        # guard mode and per-protocol overrides for entry-point plugins
        # would silently break.
        match = _discover_entrypoint_plugin(plugin_name)
        if match is not None:
            _seed_entrypoint_match(match)
            return match

        # No built-in, no entry-point match: negatively cache so
        # subsequent unknown-name lookups stay lock-free.
        _lookup_cache[plugin_name] = None
        return None


def resolve_enabled_plugins(
    config: dict[str, object],
) -> list[PluginEntry]:
    """Determine which plugins to auto-instantiate based on config.

    Config keys (mutually exclusive):
    - enabled_plugins: list[str] - allowlist (only these)
    - disabled_plugins: list[str] - blocklist (all except these)
    - neither: all available plugins

    Raises TripwireConfigError for:
    - Both keys present
    - Unknown plugin names
    - Invalid types (not a list)
    """
    from tripwire._errors import TripwireConfigError

    enabled = config.get("enabled_plugins")
    disabled = config.get("disabled_plugins")

    if enabled is not None and disabled is not None:
        raise TripwireConfigError(
            "enabled_plugins and disabled_plugins are mutually exclusive. "
            "Use one or the other, not both."
        )

    # Type validation: must be lists if present
    if enabled is not None and not isinstance(enabled, list):
        raise TripwireConfigError(
            f"enabled_plugins must be a list of strings, got {type(enabled).__name__}"
        )
    if disabled is not None and not isinstance(disabled, list):
        raise TripwireConfigError(
            f"disabled_plugins must be a list of strings, got {type(disabled).__name__}"
        )

    if enabled is not None:
        unknown = set(enabled) - VALID_PLUGIN_NAMES
        if unknown:
            raise TripwireConfigError(
                f"Unknown plugin name(s) in enabled_plugins: {sorted(unknown)}. "
                f"Valid names: {sorted(VALID_PLUGIN_NAMES)}"
            )
        result = []
        for e in PLUGIN_REGISTRY:
            if e.name in enabled:
                if not _is_available(e):
                    raise TripwireConfigError(
                        f"Plugin '{e.name}' is in enabled_plugins but its "
                        f"dependency '{e.availability_check}' is not installed. "
                        f"Install with: pip install pytest-tripwire[{e.name}]"
                    )
                result.append(e)
        return result

    if disabled is not None:
        unknown = set(disabled) - VALID_PLUGIN_NAMES
        if unknown:
            raise TripwireConfigError(
                f"Unknown plugin name(s) in disabled_plugins: {sorted(unknown)}. "
                f"Valid names: {sorted(VALID_PLUGIN_NAMES)}"
            )
        return [
            e for e in PLUGIN_REGISTRY
            if e.name not in disabled and e.default_enabled and _is_available(e)
        ]

    # Default: all available plugins that are default-enabled
    return [e for e in PLUGIN_REGISTRY if e.default_enabled and _is_available(e)]


# Eagerly seed ``_lookup_cache`` exactly once at module import time. After
# this runs, every known canonical name and registered ``guard_prefix``
# is a lock-free dict hit on the hot path. See the module-level cache
# comment block above for the full thread-safety contract.
_populate_lookup_cache()
