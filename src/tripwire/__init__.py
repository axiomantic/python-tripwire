"""tripwire - Full-certainty test mocking.

Quick start:
    # 1. Configure in pyproject.toml:
    #    [tool.tripwire]
    #    guard = "error"  # or "warn" (default), or false
    #
    # 2. Mock, execute, assert:
    tripwire.http.mock_response("GET", "/api", json={"ok": True})
    with tripwire:
        response = requests.get("/api")
    tripwire.http.assert_request("GET", "/api", status=200)

    # 3. Every intercepted call MUST be asserted.
    #    Unasserted interactions raise UnassertedInteractionsError.
    #    This is the core guarantee. Do not bypass it.

Anti-patterns:
    - NEVER create StrictVerifier directly. Use ``with tripwire:`` context.
    - NEVER use verifier.sandbox() directly. Use ``with tripwire:``.
    - NEVER skip assert_* calls. Every mock MUST be asserted.
    - NEVER wildcard ALL fields in assert_* calls. Partial wildcards OK,
      all-wildcard verifies nothing.
    - Configure plugins via [tool.tripwire], not by code.

Plugin authoring:
    Subclass BasePlugin and register via [tool.tripwire] in pyproject.toml.
    Import authoring types from tripwire directly:
        from tripwire import BasePlugin, Interaction, Timeline
    See tripwire documentation for the plugin authoring guide.
"""

from __future__ import annotations

import sys
import threading
import types
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar, cast

from tripwire._base_plugin import BasePlugin
from tripwire._context import GuardPassThrough, _get_test_verifier_or_raise, get_verifier_or_raise
from tripwire._errors import (
    AllWildcardAssertionError,
    AssertionInsideSandboxError,
    AutoAssertError,
    ConflictError,
    GuardedCallError,
    GuardedCallWarning,
    InteractionMismatchError,
    InvalidStateError,
    MissingAssertionFieldsError,
    NoActiveVerifierError,
    PostSandboxInteractionError,
    SandboxNotActiveError,
    TripwireConfigError,
    TripwireError,
    UnassertedInteractionsError,
    UnmockedInteractionError,
    UnsafePassthroughError,
    UnusedMocksError,
    VerificationError,
)
from tripwire._firewall import Disposition
from tripwire._firewall_request import FirewallRequest
from tripwire._guard import allow, deny, restrict
from tripwire._match import M
from tripwire._mock_plugin import MockPlugin
from tripwire._registry import PluginEntry
from tripwire._timeline import Interaction, Timeline
from tripwire._verifier import InAnyOrderContext, SandboxContext, StrictVerifier

try:
    from tripwire.plugins.http import HttpPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # http extra not installed

from tripwire.plugins.async_subprocess_plugin import (
    AsyncSubprocessPlugin as _AsyncSubprocessPlugin,  # noqa: F401
)
from tripwire.plugins.database_plugin import DatabasePlugin as _DatabasePlugin  # noqa: F401
from tripwire.plugins.logging_plugin import LoggingPlugin as _LoggingPlugin  # noqa: F401
from tripwire.plugins.popen_plugin import PopenPlugin as _PopenPlugin  # noqa: F401

try:
    from tripwire.plugins.celery_plugin import CeleryPlugin as _CeleryPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # celery extra not installed

try:
    from tripwire.plugins.boto3_plugin import Boto3Plugin as _Boto3Plugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # boto3 extra not installed

try:
    from tripwire.plugins.elasticsearch_plugin import (
        ElasticsearchPlugin as _ElasticsearchPlugin,  # noqa: F401
    )
except ImportError:  # pragma: no cover
    pass  # elasticsearch extra not installed

try:
    from tripwire.plugins.jwt_plugin import JwtPlugin as _JwtPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # jwt extra not installed

try:
    from tripwire.plugins.crypto_plugin import CryptoPlugin as _CryptoPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # crypto extra not installed
from tripwire.plugins.dns_plugin import DnsPlugin as _DnsPlugin  # noqa: F401
from tripwire.plugins.file_io_plugin import FileIoPlugin as _FileIoPlugin  # noqa: F401
from tripwire.plugins.memcache_plugin import MemcachePlugin as _MemcachePlugin  # noqa: F401
from tripwire.plugins.native_plugin import NativePlugin as _NativePlugin  # noqa: F401
from tripwire.plugins.redis_plugin import RedisPlugin as _RedisPlugin  # noqa: F401

try:
    from tripwire.plugins.mongo_plugin import MongoPlugin as _MongoPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # pymongo extra not installed
from tripwire.plugins.smtp_plugin import SmtpPlugin as _SmtpPlugin  # noqa: F401

try:
    from tripwire.plugins.pika_plugin import PikaPlugin as _PikaPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # pika extra not installed

try:
    from tripwire.plugins.ssh_plugin import SshPlugin as _SshPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # paramiko extra not installed

try:
    from tripwire.plugins.grpc_plugin import GrpcPlugin as _GrpcPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # grpc extra not installed

try:
    from tripwire.plugins.mcp_plugin import McpPlugin as _McpPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # mcp extra not installed

try:
    from tripwire.plugins.psycopg2_plugin import Psycopg2Plugin as _Psycopg2Plugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # psycopg2 extra not installed

try:
    from tripwire.plugins.asyncpg_plugin import AsyncpgPlugin as _AsyncpgPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # asyncpg extra not installed
from tripwire.plugins.socket_plugin import SocketPlugin as _SocketPlugin  # noqa: F401
from tripwire.plugins.subprocess import SubprocessPlugin as _SubprocessPlugin  # noqa: F401
from tripwire.plugins.websocket_plugin import (
    AsyncWebSocketPlugin as _AsyncWebSocketPlugin,
)
from tripwire.plugins.websocket_plugin import (
    SyncWebSocketPlugin as _SyncWebSocketPlugin,
)

AsyncSubprocessPlugin = _AsyncSubprocessPlugin
DatabasePlugin = _DatabasePlugin
LoggingPlugin = _LoggingPlugin
PopenPlugin = _PopenPlugin
SmtpPlugin = _SmtpPlugin
SocketPlugin = _SocketPlugin
AsyncWebSocketPlugin = _AsyncWebSocketPlugin
SyncWebSocketPlugin = _SyncWebSocketPlugin
try:
    CeleryPlugin = _CeleryPlugin
except NameError:  # pragma: no cover
    pass
DnsPlugin = _DnsPlugin
MemcachePlugin = _MemcachePlugin
RedisPlugin = _RedisPlugin
FileIoPlugin = _FileIoPlugin
NativePlugin = _NativePlugin

try:
    PikaPlugin = _PikaPlugin
except NameError:  # pragma: no cover
    pass

try:
    SshPlugin = _SshPlugin
except NameError:  # pragma: no cover
    pass

try:
    GrpcPlugin = _GrpcPlugin
except NameError:  # pragma: no cover
    pass

try:
    McpPlugin = _McpPlugin
except NameError:  # pragma: no cover
    pass

try:
    MongoPlugin = _MongoPlugin
except NameError:  # pragma: no cover
    pass

try:
    Boto3Plugin = _Boto3Plugin
except NameError:  # pragma: no cover
    pass

try:
    ElasticsearchPlugin = _ElasticsearchPlugin
except NameError:  # pragma: no cover
    pass

try:
    JwtPlugin = _JwtPlugin
except NameError:  # pragma: no cover
    pass

try:
    CryptoPlugin = _CryptoPlugin
except NameError:  # pragma: no cover
    pass

try:
    Psycopg2Plugin = _Psycopg2Plugin
except NameError:  # pragma: no cover
    pass

try:
    AsyncpgPlugin = _AsyncpgPlugin
except NameError:  # pragma: no cover
    pass

if TYPE_CHECKING:
    from tripwire._mock_plugin import ImportSiteMock, MethodProxy, ObjectMock
    from tripwire.plugins.http import HttpRequestSentinel
    from tripwire.plugins.subprocess import SubprocessRunSentinel, SubprocessWhichSentinel

__all__ = [
    # Plugin authoring API
    "BasePlugin",
    "Interaction",
    "Timeline",
    "GuardPassThrough",
    "get_verifier_or_raise",
    "PluginEntry",
    # Classes
    "StrictVerifier",
    "SandboxContext",
    "InAnyOrderContext",
    "MockPlugin",
    "DatabasePlugin",
    "LoggingPlugin",
    "PopenPlugin",
    "SmtpPlugin",
    "SocketPlugin",
    "AsyncSubprocessPlugin",
    "AsyncWebSocketPlugin",
    "SyncWebSocketPlugin",
    "RedisPlugin",
    "MongoPlugin",
    "CeleryPlugin",
    "DnsPlugin",
    "MemcachePlugin",
    "Psycopg2Plugin",
    "AsyncpgPlugin",
    "Boto3Plugin",
    "ElasticsearchPlugin",
    "JwtPlugin",
    "CryptoPlugin",
    # Guard mode
    "allow",
    "deny",
    "restrict",
    "M",
    "Disposition",
    "FirewallRequest",
    "GuardedCallError",
    "GuardedCallWarning",
    # Errors
    "AllWildcardAssertionError",
    "TripwireConfigError",
    "TripwireError",
    "AssertionInsideSandboxError",
    "AutoAssertError",
    "InvalidStateError",
    "NoActiveVerifierError",
    "PostSandboxInteractionError",
    "UnmockedInteractionError",
    "UnsafePassthroughError",
    "UnassertedInteractionsError",
    "UnusedMocksError",
    "VerificationError",
    "InteractionMismatchError",
    "SandboxNotActiveError",
    "ConflictError",
    "MissingAssertionFieldsError",
    # Module-level API
    "mock",
    "sandbox",
    "assert_interaction",
    "in_any_order",
    "verify_all",
    "current_verifier",
    "spy",
    "http",
    "subprocess",
    "popen",
    "smtp",
    "socket",
    "db",
    "async_websocket",
    "sync_websocket",
    "redis",
    "mongo",
    "dns",
    "memcache",
    "celery",
    "log",
    "async_subprocess",
    "psycopg2",
    "asyncpg",
    "boto3",
    "elasticsearch",
    "jwt",
    "crypto",
    "FileIoPlugin",
    "file_io",
    "PikaPlugin",
    "pika",
    "SshPlugin",
    "ssh",
    "GrpcPlugin",
    "grpc",
    "McpPlugin",
    "mcp",
    "NativePlugin",
    "native",
]


# ---------------------------------------------------------------------------
# Plugin lookup helper
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


_MISSING = object()


def _get_or_create_plugin(verifier: StrictVerifier, plugin_type: type[_T]) -> _T:
    """Return the first plugin of plugin_type on verifier, creating it if absent."""
    existing = next(
        (p for p in verifier._plugins if isinstance(p, plugin_type)),
        _MISSING,
    )
    if existing is not _MISSING:
        return cast(_T, existing)
    constructor: Callable[..., _T] = plugin_type
    return constructor(verifier)


# ---------------------------------------------------------------------------
# Module-level implicit API
# ---------------------------------------------------------------------------


class _MockFactory:
    """Callable object: tripwire.mock("mod:attr") and tripwire.mock.object(target, "attr")."""

    def __call__(self, path: str) -> ImportSiteMock:
        from tripwire._mock_plugin import MockPlugin as _MP  # noqa: PLC0415, N814

        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _MP)
        return plugin.create_import_site_mock(path, spy=False)

    def object(self, target: object, attr: str) -> ObjectMock:
        from tripwire._mock_plugin import MockPlugin as _MP  # noqa: PLC0415, N814

        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _MP)
        return plugin.create_object_mock(target, attr, spy=False)


class _SpyFactory:
    """Callable object: tripwire.spy("mod:attr") and tripwire.spy.object(target, "attr")."""

    def __call__(self, path: str) -> ImportSiteMock:
        from tripwire._mock_plugin import MockPlugin as _MP  # noqa: PLC0415, N814

        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _MP)
        return plugin.create_import_site_mock(path, spy=True)

    def object(self, target: object, attr: str) -> ObjectMock:
        from tripwire._mock_plugin import MockPlugin as _MP  # noqa: PLC0415, N814

        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _MP)
        return plugin.create_object_mock(target, attr, spy=True)


mock = _MockFactory()
spy = _SpyFactory()


def sandbox() -> SandboxContext:
    """Enter a sandbox on the current test verifier."""
    return _get_test_verifier_or_raise().sandbox()


def assert_interaction(
    source: MethodProxy | HttpRequestSentinel | SubprocessRunSentinel | SubprocessWhichSentinel,
    **expected: object,
) -> None:
    """Assert the next unasserted interaction on the current test verifier."""
    _get_test_verifier_or_raise().assert_interaction(source, **expected)


def in_any_order() -> InAnyOrderContext:
    """Enter an in-any-order assertion block on the current test verifier."""
    return _get_test_verifier_or_raise().in_any_order()


def verify_all() -> None:
    """Manually trigger verification on the current test verifier."""
    _get_test_verifier_or_raise().verify_all()


def current_verifier() -> StrictVerifier:
    """Return the active test verifier. Power-user escape hatch."""
    return _get_test_verifier_or_raise()


# ---------------------------------------------------------------------------
# HTTP proxy singleton
# ---------------------------------------------------------------------------


class _HttpProxy:
    """Proxy to the HttpPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        try:
            from tripwire.plugins.http import HttpPlugin as _HttpPlugin
        except ImportError:
            raise ImportError(
                "python-tripwire[http] is required to use tripwire.http. "
                "Install it with: pip install python-tripwire[http]"
            ) from None
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _HttpPlugin)
        return getattr(plugin, name)


http = _HttpProxy()


# ---------------------------------------------------------------------------
# Subprocess proxy singleton
# ---------------------------------------------------------------------------


class _SubprocessProxy:
    """Proxy to the SubprocessPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _SubprocessPlugin)
        return getattr(plugin, name)


subprocess = _SubprocessProxy()


# ---------------------------------------------------------------------------
# Popen proxy singleton
# ---------------------------------------------------------------------------


class _PopenProxy:
    """Proxy to the PopenPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _PopenPlugin)
        return getattr(plugin, name)


popen = _PopenProxy()


# ---------------------------------------------------------------------------
# SMTP proxy singleton
# ---------------------------------------------------------------------------


class _SmtpProxy:
    """Proxy to the SmtpPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _SmtpPlugin)
        return getattr(plugin, name)


smtp = _SmtpProxy()


# ---------------------------------------------------------------------------
# Socket proxy singleton
# ---------------------------------------------------------------------------


class _SocketProxy:
    """Proxy to the SocketPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _SocketPlugin)
        return getattr(plugin, name)


socket = _SocketProxy()


# ---------------------------------------------------------------------------
# Database proxy singleton
# ---------------------------------------------------------------------------


class _DatabaseProxy:
    """Proxy to the DatabasePlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _DatabasePlugin)
        return getattr(plugin, name)


db = _DatabaseProxy()


# ---------------------------------------------------------------------------
# AsyncWebSocket proxy singleton
# ---------------------------------------------------------------------------


class _AsyncWebSocketProxy:
    """Proxy to the AsyncWebSocketPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if the
    websockets extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.websocket_plugin import _WEBSOCKETS_AVAILABLE

        if not _WEBSOCKETS_AVAILABLE:
            raise ImportError(
                "python-tripwire[websockets] is required to use tripwire.async_websocket. "
                "Install it with: pip install python-tripwire[websockets]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _AsyncWebSocketPlugin)
        return getattr(plugin, name)


async_websocket = _AsyncWebSocketProxy()


# ---------------------------------------------------------------------------
# SyncWebSocket proxy singleton
# ---------------------------------------------------------------------------


class _SyncWebSocketProxy:
    """Proxy to the SyncWebSocketPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if the
    websocket-client extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.websocket_plugin import _WEBSOCKET_CLIENT_AVAILABLE

        if not _WEBSOCKET_CLIENT_AVAILABLE:
            raise ImportError(
                "python-tripwire[websocket-client] is required to use tripwire.sync_websocket. "
                "Install it with: pip install python-tripwire[websocket-client]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _SyncWebSocketPlugin)
        return getattr(plugin, name)


sync_websocket = _SyncWebSocketProxy()


# ---------------------------------------------------------------------------
# Redis proxy singleton
# ---------------------------------------------------------------------------


class _RedisProxy:
    """Proxy to the RedisPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the redis extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.redis_plugin import _REDIS_AVAILABLE

        if not _REDIS_AVAILABLE:
            raise ImportError(
                "python-tripwire[redis] is required to use tripwire.redis. "
                "Install it with: pip install python-tripwire[redis]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _RedisPlugin)
        return getattr(plugin, name)


redis = _RedisProxy()


# ---------------------------------------------------------------------------
# File I/O proxy singleton
# ---------------------------------------------------------------------------


class _FileIoProxy:
    """Proxy to the FileIoPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. FileIoPlugin is always
    available (no optional dependencies), but is NOT default enabled.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _FileIoPlugin)
        return getattr(plugin, name)


file_io = _FileIoProxy()


# ---------------------------------------------------------------------------
# Native proxy singleton
# ---------------------------------------------------------------------------


class _NativeProxy:
    """Proxy to the NativePlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. NativePlugin is always
    available (ctypes is stdlib), but is NOT default enabled.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _NativePlugin)
        return getattr(plugin, name)


native = _NativeProxy()


# ---------------------------------------------------------------------------
# Pika proxy singleton
# ---------------------------------------------------------------------------


class _PikaProxy:
    """Proxy to the PikaPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the pika extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.pika_plugin import _PIKA_AVAILABLE

        if not _PIKA_AVAILABLE:
            raise ImportError(
                "python-tripwire[pika] is required to use tripwire.pika. "
                "Install it with: pip install python-tripwire[pika]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _PikaPlugin)
        return getattr(plugin, name)


pika = _PikaProxy()


# ---------------------------------------------------------------------------
# SSH proxy singleton
# ---------------------------------------------------------------------------


class _SshProxy:
    """Proxy to the SshPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the paramiko extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.ssh_plugin import _PARAMIKO_AVAILABLE

        if not _PARAMIKO_AVAILABLE:
            raise ImportError(
                "python-tripwire[ssh] is required to use tripwire.ssh. "
                "Install it with: pip install python-tripwire[ssh]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _SshPlugin)
        return getattr(plugin, name)


ssh = _SshProxy()


# ---------------------------------------------------------------------------
# gRPC proxy singleton
# ---------------------------------------------------------------------------


class _GrpcProxy:
    """Proxy to the GrpcPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the grpc extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.grpc_plugin import _GRPC_AVAILABLE

        if not _GRPC_AVAILABLE:
            raise ImportError(
                "python-tripwire[grpc] is required to use tripwire.grpc. "
                "Install it with: pip install python-tripwire[grpc]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _GrpcPlugin)
        return getattr(plugin, name)


grpc = _GrpcProxy()


# ---------------------------------------------------------------------------
# MCP proxy singleton
# ---------------------------------------------------------------------------


class _McpProxy:
    """Proxy to the McpPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the mcp extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.mcp_plugin import _MCP_AVAILABLE

        if not _MCP_AVAILABLE:
            raise ImportError(
                "python-tripwire[mcp] is required to use tripwire.mcp. "
                "Install it with: pip install python-tripwire[mcp]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _McpPlugin)
        return getattr(plugin, name)


mcp = _McpProxy()


# ---------------------------------------------------------------------------
# MongoDB proxy singleton
# ---------------------------------------------------------------------------


class _MongoProxy:
    """Proxy to the MongoPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the pymongo extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.mongo_plugin import _PYMONGO_AVAILABLE

        if not _PYMONGO_AVAILABLE:
            raise ImportError(
                "python-tripwire[mongo] is required to use tripwire.mongo. "
                "Install it with: pip install python-tripwire[mongo]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _MongoPlugin)
        return getattr(plugin, name)


mongo = _MongoProxy()


# ---------------------------------------------------------------------------
# DNS proxy singleton
# ---------------------------------------------------------------------------


class _DnsProxy:
    """Proxy to the DnsPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. DNS plugin is always
    available (stdlib socket), no ImportError check needed.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _DnsPlugin)
        return getattr(plugin, name)


dns = _DnsProxy()


# ---------------------------------------------------------------------------
# Memcache proxy singleton
# ---------------------------------------------------------------------------


class _MemcacheProxy:
    """Proxy to the MemcachePlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the pymemcache extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.memcache_plugin import _PYMEMCACHE_AVAILABLE

        if not _PYMEMCACHE_AVAILABLE:
            raise ImportError(
                "python-tripwire[pymemcache] is required to use tripwire.memcache. "
                "Install it with: pip install python-tripwire[pymemcache]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _MemcachePlugin)
        return getattr(plugin, name)


memcache = _MemcacheProxy()


# ---------------------------------------------------------------------------
# Celery proxy singleton
# ---------------------------------------------------------------------------


class _CeleryProxy:
    """Proxy to the CeleryPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the celery extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.celery_plugin import _CELERY_AVAILABLE

        if not _CELERY_AVAILABLE:
            raise ImportError(
                "python-tripwire[celery] is required to use tripwire.celery. "
                "Install it with: pip install python-tripwire[celery]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _CeleryPlugin)
        return getattr(plugin, name)


celery = _CeleryProxy()


# ---------------------------------------------------------------------------
# Logging proxy singleton
# ---------------------------------------------------------------------------


class _LoggingProxy:
    """Proxy to the LoggingPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _LoggingPlugin)
        return getattr(plugin, name)


log = _LoggingProxy()


# ---------------------------------------------------------------------------
# Psycopg2 proxy singleton
# ---------------------------------------------------------------------------


class _Psycopg2Proxy:
    """Proxy to the Psycopg2Plugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the psycopg2 extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.psycopg2_plugin import _PSYCOPG2_AVAILABLE

        if not _PSYCOPG2_AVAILABLE:
            raise ImportError(
                "python-tripwire[psycopg2] is required to use tripwire.psycopg2. "
                "Install it with: pip install python-tripwire[psycopg2]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _Psycopg2Plugin)
        return getattr(plugin, name)


psycopg2 = _Psycopg2Proxy()


# ---------------------------------------------------------------------------
# Asyncpg proxy singleton
# ---------------------------------------------------------------------------


class _AsyncpgProxy:
    """Proxy to the AsyncpgPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the asyncpg extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.asyncpg_plugin import _ASYNCPG_AVAILABLE

        if not _ASYNCPG_AVAILABLE:
            raise ImportError(
                "python-tripwire[asyncpg] is required to use tripwire.asyncpg. "
                "Install it with: pip install python-tripwire[asyncpg]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _AsyncpgPlugin)
        return getattr(plugin, name)


asyncpg = _AsyncpgProxy()


# ---------------------------------------------------------------------------
# boto3 proxy singleton
# ---------------------------------------------------------------------------


class _Boto3Proxy:
    """Proxy to the Boto3Plugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the boto3 extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.boto3_plugin import _BOTO3_AVAILABLE

        if not _BOTO3_AVAILABLE:
            raise ImportError(
                "python-tripwire[boto3] is required to use tripwire.boto3. "
                "Install it with: pip install python-tripwire[boto3]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _Boto3Plugin)
        return getattr(plugin, name)


boto3 = _Boto3Proxy()


# ---------------------------------------------------------------------------
# Elasticsearch proxy singleton
# ---------------------------------------------------------------------------


class _ElasticsearchProxy:
    """Proxy to the ElasticsearchPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the elasticsearch extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.elasticsearch_plugin import _ELASTICSEARCH_AVAILABLE

        if not _ELASTICSEARCH_AVAILABLE:
            raise ImportError(
                "python-tripwire[elasticsearch] is required to use tripwire.elasticsearch. "
                "Install it with: pip install python-tripwire[elasticsearch]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _ElasticsearchPlugin)
        return getattr(plugin, name)


elasticsearch = _ElasticsearchProxy()


# ---------------------------------------------------------------------------
# JWT proxy singleton
# ---------------------------------------------------------------------------


class _JwtProxy:
    """Proxy to the JwtPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the jwt extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.jwt_plugin import _JWT_AVAILABLE

        if not _JWT_AVAILABLE:
            raise ImportError(
                "python-tripwire[jwt] is required to use tripwire.jwt. "
                "Install it with: pip install python-tripwire[jwt]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _JwtPlugin)
        return getattr(plugin, name)


jwt = _JwtProxy()


# ---------------------------------------------------------------------------
# Crypto proxy singleton
# ---------------------------------------------------------------------------


class _CryptoProxy:
    """Proxy to the CryptoPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the cryptography extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from tripwire.plugins.crypto_plugin import _CRYPTOGRAPHY_AVAILABLE

        if not _CRYPTOGRAPHY_AVAILABLE:
            raise ImportError(
                "python-tripwire[crypto] is required to use tripwire.crypto. "
                "Install it with: pip install python-tripwire[crypto]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _CryptoPlugin)
        return getattr(plugin, name)


crypto = _CryptoProxy()


# ---------------------------------------------------------------------------
# AsyncSubprocess proxy singleton
# ---------------------------------------------------------------------------


class _AsyncSubprocessProxy:
    """Proxy to the AsyncSubprocessPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _AsyncSubprocessPlugin)
        return getattr(plugin, name)


async_subprocess = _AsyncSubprocessProxy()


# ---------------------------------------------------------------------------
# Module-level context manager  (``with tripwire:`` / ``async with tripwire:``)
# ---------------------------------------------------------------------------

_sandbox_stack: threading.local = threading.local()


class _TripwireModule(types.ModuleType):
    """ModuleType subclass that makes ``tripwire`` usable as a context manager.

    ``with tripwire:`` is equivalent to ``with tripwire.sandbox():``.
    ``async with tripwire:`` is equivalent to ``async with tripwire.sandbox():``.
    Both forms return the active :class:`StrictVerifier` from ``__enter__``.
    """

    def _push_cm(self) -> SandboxContext:
        """Create a sandbox context manager and push it onto the thread-local stack."""
        cm = sandbox()
        stack = _sandbox_stack.__dict__.setdefault("stack", [])
        stack.append(cm)
        return cm

    def __enter__(self) -> StrictVerifier:
        return self._push_cm().__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        _sandbox_stack.stack.pop().__exit__(exc_type, exc_val, exc_tb)

    async def __aenter__(self) -> StrictVerifier:
        return await self._push_cm().__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await _sandbox_stack.stack.pop().__aexit__(exc_type, exc_val, exc_tb)


sys.modules[__name__].__class__ = _TripwireModule
