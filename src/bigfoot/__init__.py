"""bigfoot: a pluggable interaction auditor for Python tests."""

from __future__ import annotations

import sys
import threading
import types
from typing import TYPE_CHECKING, TypeVar

from bigfoot._context import _get_test_verifier_or_raise
from bigfoot._errors import (
    AssertionInsideSandboxError,
    AutoAssertError,
    BigfootConfigError,
    BigfootError,
    ConflictError,
    InteractionMismatchError,
    InvalidStateError,
    MissingAssertionFieldsError,
    NoActiveVerifierError,
    SandboxNotActiveError,
    UnassertedInteractionsError,
    UnmockedInteractionError,
    UnusedMocksError,
    VerificationError,
)
from bigfoot._mock_plugin import MockPlugin
from bigfoot._verifier import InAnyOrderContext, SandboxContext, StrictVerifier

try:
    from bigfoot.plugins.http import HttpPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # http extra not installed

from bigfoot.plugins.async_subprocess_plugin import (
    AsyncSubprocessPlugin as _AsyncSubprocessPlugin,  # noqa: F401
)
from bigfoot.plugins.database_plugin import DatabasePlugin as _DatabasePlugin  # noqa: F401
from bigfoot.plugins.logging_plugin import LoggingPlugin as _LoggingPlugin  # noqa: F401
from bigfoot.plugins.popen_plugin import PopenPlugin as _PopenPlugin  # noqa: F401
from bigfoot.plugins.celery_plugin import CeleryPlugin as _CeleryPlugin  # noqa: F401
from bigfoot.plugins.dns_plugin import DnsPlugin as _DnsPlugin  # noqa: F401
from bigfoot.plugins.memcache_plugin import MemcachePlugin as _MemcachePlugin  # noqa: F401
from bigfoot.plugins.redis_plugin import RedisPlugin as _RedisPlugin  # noqa: F401
from bigfoot.plugins.smtp_plugin import SmtpPlugin as _SmtpPlugin  # noqa: F401

try:
    from bigfoot.plugins.psycopg2_plugin import Psycopg2Plugin as _Psycopg2Plugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # psycopg2 extra not installed

try:
    from bigfoot.plugins.asyncpg_plugin import AsyncpgPlugin as _AsyncpgPlugin  # noqa: F401
except ImportError:  # pragma: no cover
    pass  # asyncpg extra not installed
from bigfoot.plugins.socket_plugin import SocketPlugin as _SocketPlugin  # noqa: F401
from bigfoot.plugins.subprocess import SubprocessPlugin as _SubprocessPlugin  # noqa: F401
from bigfoot.plugins.websocket_plugin import (
    AsyncWebSocketPlugin as _AsyncWebSocketPlugin,
)
from bigfoot.plugins.websocket_plugin import (
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
CeleryPlugin = _CeleryPlugin
DnsPlugin = _DnsPlugin
MemcachePlugin = _MemcachePlugin
RedisPlugin = _RedisPlugin

try:
    Psycopg2Plugin = _Psycopg2Plugin
except NameError:  # pragma: no cover
    pass

try:
    AsyncpgPlugin = _AsyncpgPlugin
except NameError:  # pragma: no cover
    pass

if TYPE_CHECKING:
    from bigfoot._mock_plugin import MethodProxy, MockProxy
    from bigfoot.plugins.http import HttpRequestSentinel
    from bigfoot.plugins.subprocess import SubprocessRunSentinel, SubprocessWhichSentinel

__all__ = [
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
    "CeleryPlugin",
    "DnsPlugin",
    "MemcachePlugin",
    "Psycopg2Plugin",
    "AsyncpgPlugin",
    # Errors
    "BigfootConfigError",
    "BigfootError",
    "AssertionInsideSandboxError",
    "AutoAssertError",
    "InvalidStateError",
    "NoActiveVerifierError",
    "UnmockedInteractionError",
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
    "subprocess_mock",
    "popen_mock",
    "smtp_mock",
    "socket_mock",
    "db_mock",
    "async_websocket_mock",
    "sync_websocket_mock",
    "redis_mock",
    "dns_mock",
    "memcache_mock",
    "celery_mock",
    "log_mock",
    "async_subprocess_mock",
    "psycopg2_mock",
    "asyncpg_mock",
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
        return existing  # type: ignore[return-value]
    return plugin_type(verifier)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Module-level implicit API
# ---------------------------------------------------------------------------


def mock(name: str, wraps: object = None) -> MockProxy:
    """Create or retrieve a named mock on the current test verifier.

    If wraps is provided, method calls with an empty queue are delegated to
    the wrapped object instead of raising UnmockedInteractionError.
    """
    return _get_test_verifier_or_raise().mock(name, wraps=wraps)


def spy(name: str, real: object) -> MockProxy:
    """Create a spy on the current test verifier (syntactic sugar for mock(name, wraps=real)).

    The proxy delegates all calls to real, recording every interaction on the
    timeline without requiring explicit mock configurations.
    """
    return _get_test_verifier_or_raise().spy(name, real)


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
            from bigfoot.plugins.http import HttpPlugin as _HttpPlugin
        except ImportError:
            raise ImportError(
                "bigfoot[http] is required to use bigfoot.http. "
                "Install it with: pip install bigfoot[http]"
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


subprocess_mock = _SubprocessProxy()


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


popen_mock = _PopenProxy()


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


smtp_mock = _SmtpProxy()


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


socket_mock = _SocketProxy()


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


db_mock = _DatabaseProxy()


# ---------------------------------------------------------------------------
# AsyncWebSocket proxy singleton
# ---------------------------------------------------------------------------


class _AsyncWebSocketProxy:
    """Proxy to the AsyncWebSocketPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if the
    websockets extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from bigfoot.plugins.websocket_plugin import _WEBSOCKETS_AVAILABLE

        if not _WEBSOCKETS_AVAILABLE:
            raise ImportError(
                "bigfoot[websockets] is required to use bigfoot.async_websocket_mock. "
                "Install it with: pip install bigfoot[websockets]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _AsyncWebSocketPlugin)
        return getattr(plugin, name)


async_websocket_mock = _AsyncWebSocketProxy()


# ---------------------------------------------------------------------------
# SyncWebSocket proxy singleton
# ---------------------------------------------------------------------------


class _SyncWebSocketProxy:
    """Proxy to the SyncWebSocketPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if the
    websocket-client extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from bigfoot.plugins.websocket_plugin import _WEBSOCKET_CLIENT_AVAILABLE

        if not _WEBSOCKET_CLIENT_AVAILABLE:
            raise ImportError(
                "bigfoot[websocket-client] is required to use bigfoot.sync_websocket_mock. "
                "Install it with: pip install bigfoot[websocket-client]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _SyncWebSocketPlugin)
        return getattr(plugin, name)


sync_websocket_mock = _SyncWebSocketProxy()


# ---------------------------------------------------------------------------
# Redis proxy singleton
# ---------------------------------------------------------------------------


class _RedisProxy:
    """Proxy to the RedisPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the redis extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from bigfoot.plugins.redis_plugin import _REDIS_AVAILABLE

        if not _REDIS_AVAILABLE:
            raise ImportError(
                "bigfoot[redis] is required to use bigfoot.redis_mock. "
                "Install it with: pip install bigfoot[redis]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _RedisPlugin)
        return getattr(plugin, name)


redis_mock = _RedisProxy()


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


dns_mock = _DnsProxy()


# ---------------------------------------------------------------------------
# Memcache proxy singleton
# ---------------------------------------------------------------------------


class _MemcacheProxy:
    """Proxy to the MemcachePlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the pymemcache extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from bigfoot.plugins.memcache_plugin import _PYMEMCACHE_AVAILABLE

        if not _PYMEMCACHE_AVAILABLE:
            raise ImportError(
                "bigfoot[pymemcache] is required to use bigfoot.memcache_mock. "
                "Install it with: pip install bigfoot[pymemcache]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _MemcachePlugin)
        return getattr(plugin, name)


memcache_mock = _MemcacheProxy()


# ---------------------------------------------------------------------------
# Celery proxy singleton
# ---------------------------------------------------------------------------


class _CeleryProxy:
    """Proxy to the CeleryPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the celery extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from bigfoot.plugins.celery_plugin import _CELERY_AVAILABLE

        if not _CELERY_AVAILABLE:
            raise ImportError(
                "bigfoot[celery] is required to use bigfoot.celery_mock. "
                "Install it with: pip install bigfoot[celery]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _CeleryPlugin)
        return getattr(plugin, name)


celery_mock = _CeleryProxy()


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


log_mock = _LoggingProxy()


# ---------------------------------------------------------------------------
# Psycopg2 proxy singleton
# ---------------------------------------------------------------------------


class _Psycopg2Proxy:
    """Proxy to the Psycopg2Plugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the psycopg2 extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from bigfoot.plugins.psycopg2_plugin import _PSYCOPG2_AVAILABLE

        if not _PSYCOPG2_AVAILABLE:
            raise ImportError(
                "bigfoot[psycopg2] is required to use bigfoot.psycopg2_mock. "
                "Install it with: pip install bigfoot[psycopg2]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _Psycopg2Plugin)
        return getattr(plugin, name)


psycopg2_mock = _Psycopg2Proxy()


# ---------------------------------------------------------------------------
# Asyncpg proxy singleton
# ---------------------------------------------------------------------------


class _AsyncpgProxy:
    """Proxy to the AsyncpgPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test. Raises ImportError if
    the asyncpg extra is not installed.
    """

    def __getattr__(self, name: str) -> object:
        from bigfoot.plugins.asyncpg_plugin import _ASYNCPG_AVAILABLE

        if not _ASYNCPG_AVAILABLE:
            raise ImportError(
                "bigfoot[asyncpg] is required to use bigfoot.asyncpg_mock. "
                "Install it with: pip install bigfoot[asyncpg]"
            )
        verifier = _get_test_verifier_or_raise()
        plugin = _get_or_create_plugin(verifier, _AsyncpgPlugin)
        return getattr(plugin, name)


asyncpg_mock = _AsyncpgProxy()


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


async_subprocess_mock = _AsyncSubprocessProxy()


# ---------------------------------------------------------------------------
# Module-level context manager  (``with bigfoot:`` / ``async with bigfoot:``)
# ---------------------------------------------------------------------------

_sandbox_stack: threading.local = threading.local()


class _BigfootModule(types.ModuleType):
    """ModuleType subclass that makes ``bigfoot`` usable as a context manager.

    ``with bigfoot:`` is equivalent to ``with bigfoot.sandbox():``.
    ``async with bigfoot:`` is equivalent to ``async with bigfoot.sandbox():``.
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


sys.modules[__name__].__class__ = _BigfootModule
