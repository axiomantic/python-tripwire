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

from bigfoot.plugins.database_plugin import DatabasePlugin as _DatabasePlugin  # noqa: F401
from bigfoot.plugins.popen_plugin import PopenPlugin as _PopenPlugin  # noqa: F401
from bigfoot.plugins.redis_plugin import RedisPlugin as _RedisPlugin  # noqa: F401
from bigfoot.plugins.smtp_plugin import SmtpPlugin as _SmtpPlugin  # noqa: F401
from bigfoot.plugins.socket_plugin import SocketPlugin as _SocketPlugin  # noqa: F401
from bigfoot.plugins.subprocess import SubprocessPlugin as _SubprocessPlugin  # noqa: F401
from bigfoot.plugins.websocket_plugin import (
    AsyncWebSocketPlugin as _AsyncWebSocketPlugin,
)
from bigfoot.plugins.websocket_plugin import (
    SyncWebSocketPlugin as _SyncWebSocketPlugin,
)

DatabasePlugin = _DatabasePlugin
PopenPlugin = _PopenPlugin
SmtpPlugin = _SmtpPlugin
SocketPlugin = _SocketPlugin
AsyncWebSocketPlugin = _AsyncWebSocketPlugin
SyncWebSocketPlugin = _SyncWebSocketPlugin
RedisPlugin = _RedisPlugin

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
    "PopenPlugin",
    "SmtpPlugin",
    "SocketPlugin",
    "AsyncWebSocketPlugin",
    "SyncWebSocketPlugin",
    "RedisPlugin",
    # Errors
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
]


# ---------------------------------------------------------------------------
# Plugin lookup helper
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


def _get_or_create_plugin(verifier: StrictVerifier, plugin_type: type[_T]) -> _T:
    """Return the first plugin of plugin_type on verifier, creating it if absent."""
    for p in verifier._plugins:
        if isinstance(p, plugin_type):
            return p
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
# Module-level context manager  (``with bigfoot:`` / ``async with bigfoot:``)
# ---------------------------------------------------------------------------

_sandbox_stack: threading.local = threading.local()


class _BigfootModule(types.ModuleType):
    """ModuleType subclass that makes ``bigfoot`` usable as a context manager.

    ``with bigfoot:`` is equivalent to ``with bigfoot.sandbox():``.
    ``async with bigfoot:`` is equivalent to ``async with bigfoot.sandbox():``.
    Both forms return the active :class:`StrictVerifier` from ``__enter__``.
    """

    def __enter__(self) -> StrictVerifier:
        cm = sandbox()
        stack = _sandbox_stack.__dict__.setdefault("stack", [])
        stack.append(cm)
        return cm.__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        _sandbox_stack.stack.pop().__exit__(exc_type, exc_val, exc_tb)

    async def __aenter__(self) -> StrictVerifier:
        cm = sandbox()
        stack = _sandbox_stack.__dict__.setdefault("stack", [])
        stack.append(cm)
        return await cm.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await _sandbox_stack.stack.pop().__aexit__(exc_type, exc_val, exc_tb)


sys.modules[__name__].__class__ = _BigfootModule
