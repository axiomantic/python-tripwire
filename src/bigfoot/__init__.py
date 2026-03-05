"""bigfoot: a pluggable interaction auditor for Python tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bigfoot._context import _get_test_verifier_or_raise
from bigfoot._errors import (
    AssertionInsideSandboxError,
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
SocketPlugin = _SocketPlugin
AsyncWebSocketPlugin = _AsyncWebSocketPlugin
SyncWebSocketPlugin = _SyncWebSocketPlugin

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
    "SocketPlugin",
    "AsyncWebSocketPlugin",
    "SyncWebSocketPlugin",
    # Errors
    "BigfootError",
    "AssertionInsideSandboxError",
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
    "socket_mock",
    "db_mock",
    "async_websocket_mock",
    "sync_websocket_mock",
]


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
        plugin: _HttpPlugin | None = None
        for p in verifier._plugins:
            if isinstance(p, _HttpPlugin):
                plugin = p
                break
        if plugin is None:
            plugin = _HttpPlugin(verifier)
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
        plugin: _SubprocessPlugin | None = None
        for p in verifier._plugins:
            if isinstance(p, _SubprocessPlugin):
                plugin = p
                break
        if plugin is None:
            plugin = _SubprocessPlugin(verifier)
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
        plugin: _PopenPlugin | None = None
        for p in verifier._plugins:
            if isinstance(p, _PopenPlugin):
                plugin = p
                break
        if plugin is None:
            plugin = _PopenPlugin(verifier)
        return getattr(plugin, name)


popen_mock = _PopenProxy()


# ---------------------------------------------------------------------------
# Socket proxy singleton
# ---------------------------------------------------------------------------


class _SocketProxy:
    """Proxy to the SocketPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin: _SocketPlugin | None = None
        for p in verifier._plugins:
            if isinstance(p, _SocketPlugin):
                plugin = p
                break
        if plugin is None:
            plugin = _SocketPlugin(verifier)
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
        plugin: _DatabasePlugin | None = None
        for p in verifier._plugins:
            if isinstance(p, _DatabasePlugin):
                plugin = p
                break
        if plugin is None:
            plugin = _DatabasePlugin(verifier)
        return getattr(plugin, name)


db_mock = _DatabaseProxy()


# ---------------------------------------------------------------------------
# AsyncWebSocket proxy singleton
# ---------------------------------------------------------------------------


class _AsyncWebSocketProxy:
    """Proxy to the AsyncWebSocketPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin: _AsyncWebSocketPlugin | None = None
        for p in verifier._plugins:
            if isinstance(p, _AsyncWebSocketPlugin):
                plugin = p
                break
        if plugin is None:
            plugin = _AsyncWebSocketPlugin(verifier)
        return getattr(plugin, name)


async_websocket_mock = _AsyncWebSocketProxy()


# ---------------------------------------------------------------------------
# SyncWebSocket proxy singleton
# ---------------------------------------------------------------------------


class _SyncWebSocketProxy:
    """Proxy to the SyncWebSocketPlugin registered on the current test verifier.

    Auto-creates the plugin on first access per test.
    """

    def __getattr__(self, name: str) -> object:
        verifier = _get_test_verifier_or_raise()
        plugin: _SyncWebSocketPlugin | None = None
        for p in verifier._plugins:
            if isinstance(p, _SyncWebSocketPlugin):
                plugin = p
                break
        if plugin is None:
            plugin = _SyncWebSocketPlugin(verifier)
        return getattr(plugin, name)


sync_websocket_mock = _SyncWebSocketProxy()
