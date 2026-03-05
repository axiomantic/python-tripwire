"""WebSocket plugins: AsyncWebSocketPlugin and SyncWebSocketPlugin."""

from __future__ import annotations

import threading
from typing import Any, ClassVar

from bigfoot._context import _get_verifier_or_raise
from bigfoot._errors import UnmockedInteractionError
from bigfoot._state_machine_plugin import SessionHandle, StateMachinePlugin
from bigfoot._timeline import Interaction

# ---------------------------------------------------------------------------
# Optional dependency guards
# ---------------------------------------------------------------------------

try:
    import websockets
    import websockets.asyncio.client  # noqa: F401 -- import confirms correct sub-package

    _WEBSOCKETS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WEBSOCKETS_AVAILABLE = False

try:
    import websocket  # noqa: F401 -- websocket-client package; import used for flag only

    _WEBSOCKET_CLIENT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WEBSOCKET_CLIENT_AVAILABLE = False


# ---------------------------------------------------------------------------
# Module-level helpers: locate the active plugin from the current verifier
# ---------------------------------------------------------------------------


def _get_async_websocket_plugin() -> AsyncWebSocketPlugin:
    verifier = _get_verifier_or_raise("websocket:async:connect")
    for plugin in verifier._plugins:
        if isinstance(plugin, AsyncWebSocketPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot AsyncWebSocketPlugin interceptor is active but no "
        "AsyncWebSocketPlugin is registered on the current verifier."
    )


def _get_sync_websocket_plugin() -> SyncWebSocketPlugin:
    verifier = _get_verifier_or_raise("websocket:sync:connect")
    for plugin in verifier._plugins:
        if isinstance(plugin, SyncWebSocketPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot SyncWebSocketPlugin interceptor is active but no "
        "SyncWebSocketPlugin is registered on the current verifier."
    )


# ===========================================================================
# AsyncWebSocketPlugin
# ===========================================================================


class _FakeAsyncWebSocket:
    """Fake async WebSocket connection object returned from __aenter__."""

    def __init__(self, handle: SessionHandle, plugin: AsyncWebSocketPlugin) -> None:
        self._handle = handle
        self._plugin = plugin

    async def send(self, data: Any) -> Any:  # noqa: ANN401
        return self._plugin._execute_step(self._handle, "send", (data,), {}, "websocket:async:send")

    async def recv(self) -> Any:  # noqa: ANN401
        return self._plugin._execute_step(self._handle, "recv", (), {}, "websocket:async:recv")

    async def close(self) -> Any:  # noqa: ANN401
        result = self._plugin._execute_step(self._handle, "close", (), {}, "websocket:async:close")
        self._plugin._release_session(self)
        return result


class _FakeAsyncWebSocketCM:
    """Async context manager returned by the patched websockets.connect().

    The FIFO pop from the session queue happens at construction time (i.e.,
    at websockets.connect() call time), NOT in __aenter__. This matches user
    expectations: the queue slot is consumed when the connection is "dialed",
    not when the async with block is entered.
    """

    def __init__(self, handle: SessionHandle, plugin: AsyncWebSocketPlugin) -> None:
        self._handle = handle
        self._plugin = plugin
        self._fake_ws: _FakeAsyncWebSocket | None = None

    async def __aenter__(self) -> _FakeAsyncWebSocket:
        fake_ws = _FakeAsyncWebSocket(self._handle, self._plugin)
        self._fake_ws = fake_ws
        # Register in active_sessions now that we have the fake_ws identity.
        self._plugin._register_connection(self._handle, fake_ws)
        # Execute the "connect" transition step.
        self._plugin._execute_step(self._handle, "connect", (), {}, "websocket:async:connect")
        return fake_ws

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        # Only call close if the session is still active (not already closed by caller).
        if self._fake_ws is not None and id(self._fake_ws) in self._plugin._active_sessions:
            await self._fake_ws.close()


class AsyncWebSocketPlugin(StateMachinePlugin):
    """Async WebSocket interception plugin.

    Patches websockets.connect at the module level.
    Uses reference counting so nested sandboxes work correctly.

    States: connecting -> open -> closed
    """

    # Class-level reference counting -- shared across all instances/verifiers.
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    # Saved original, restored when count reaches 0.
    _original_connect: ClassVar[Any] = None

    # ------------------------------------------------------------------
    # StateMachinePlugin abstract methods
    # ------------------------------------------------------------------

    def _initial_state(self) -> str:
        return "connecting"

    def _transitions(self) -> dict[str, dict[str, str]]:
        return {
            "connect": {"connecting": "open"},
            "send": {"open": "open"},
            "recv": {"open": "open"},
            "close": {"open": "closed"},
        }

    def _unmocked_source_id(self) -> str:
        return "websocket:async:connect"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        if not _WEBSOCKETS_AVAILABLE:
            raise ImportError(
                "Install bigfoot[websockets] to use AsyncWebSocketPlugin: "
                "pip install bigfoot[websockets]"
            )
        with AsyncWebSocketPlugin._install_lock:
            if AsyncWebSocketPlugin._install_count == 0:
                self._install_patches()
            AsyncWebSocketPlugin._install_count += 1

    def deactivate(self) -> None:
        with AsyncWebSocketPlugin._install_lock:
            AsyncWebSocketPlugin._install_count = max(0, AsyncWebSocketPlugin._install_count - 1)
            if AsyncWebSocketPlugin._install_count == 0:
                self._restore_patches()

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        import websockets as _ws

        AsyncWebSocketPlugin._original_connect = _ws.connect

        def _patched_websockets_connect(*args: Any, **kwargs: Any) -> _FakeAsyncWebSocketCM:  # noqa: ANN401
            plugin = _get_async_websocket_plugin()
            # Pop from queue at websockets.connect() call time (FIFO).
            with plugin._registry_lock:
                if not plugin._session_queue:
                    source_id = plugin._unmocked_source_id()
                    hint = plugin.format_unmocked_hint(source_id, args, kwargs)
                    raise UnmockedInteractionError(
                        source_id=source_id,
                        args=args,
                        kwargs=kwargs,
                        hint=hint,
                    )
                handle = plugin._session_queue.popleft()
            return _FakeAsyncWebSocketCM(handle, plugin)

        _ws.connect = _patched_websockets_connect  # type: ignore[misc,assignment]

    def _restore_patches(self) -> None:
        if AsyncWebSocketPlugin._original_connect is not None:
            import websockets as _ws

            _ws.connect = AsyncWebSocketPlugin._original_connect  # type: ignore[misc]
            AsyncWebSocketPlugin._original_connect = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        args = interaction.details.get("args", ())
        kwargs = interaction.details.get("kwargs", {})
        parts = [repr(a) for a in args]
        parts += [f"{k}={v!r}" for k, v in kwargs.items()]
        return f"[AsyncWebSocketPlugin] websockets.{method}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"    bigfoot.async_websocket_mock.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":")[-1] if ":" in source_id else source_id
        return (
            f"websockets.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.async_websocket_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.async_websocket_mock"
        method = interaction.details.get("method", "?")
        return f"    # {sm}: session step '{method}' recorded (state-machine, auto-asserted)"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config
        method = getattr(step, "method", "?")
        return (
            f"websockets.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )


# ===========================================================================
# SyncWebSocketPlugin
# ===========================================================================


class _FakeSyncWebSocket:
    """Fake sync WebSocket connection object returned by the patched create_connection()."""

    def __init__(self, handle: SessionHandle, plugin: SyncWebSocketPlugin) -> None:
        self._handle = handle
        self._plugin = plugin

    def send(self, data: Any) -> Any:  # noqa: ANN401
        return self._plugin._execute_step(self._handle, "send", (data,), {}, "websocket:sync:send")

    def recv(self) -> Any:  # noqa: ANN401
        return self._plugin._execute_step(self._handle, "recv", (), {}, "websocket:sync:recv")

    def close(self) -> Any:  # noqa: ANN401
        result = self._plugin._execute_step(self._handle, "close", (), {}, "websocket:sync:close")
        self._plugin._release_session(self)
        return result


class SyncWebSocketPlugin(StateMachinePlugin):
    """Sync WebSocket interception plugin (websocket-client library).

    Patches websocket.create_connection at the module level.
    Uses reference counting so nested sandboxes work correctly.

    States: connecting -> open -> closed
    """

    # Class-level reference counting -- shared across all instances/verifiers.
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    # Saved original, restored when count reaches 0.
    _original_create_connection: ClassVar[Any] = None

    # ------------------------------------------------------------------
    # StateMachinePlugin abstract methods
    # ------------------------------------------------------------------

    def _initial_state(self) -> str:
        return "connecting"

    def _transitions(self) -> dict[str, dict[str, str]]:
        return {
            "connect": {"connecting": "open"},
            "send": {"open": "open"},
            "recv": {"open": "open"},
            "close": {"open": "closed"},
        }

    def _unmocked_source_id(self) -> str:
        return "websocket:sync:connect"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        if not _WEBSOCKET_CLIENT_AVAILABLE:
            raise ImportError(
                "Install bigfoot[websocket-client] to use SyncWebSocketPlugin: "
                "pip install bigfoot[websocket-client]"
            )
        with SyncWebSocketPlugin._install_lock:
            if SyncWebSocketPlugin._install_count == 0:
                self._install_patches()
            SyncWebSocketPlugin._install_count += 1

    def deactivate(self) -> None:
        with SyncWebSocketPlugin._install_lock:
            SyncWebSocketPlugin._install_count = max(0, SyncWebSocketPlugin._install_count - 1)
            if SyncWebSocketPlugin._install_count == 0:
                self._restore_patches()

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        import websocket as _wsc

        SyncWebSocketPlugin._original_create_connection = _wsc.create_connection

        def _patched_create_connection(*args: Any, **kwargs: Any) -> _FakeSyncWebSocket:  # noqa: ANN401
            plugin = _get_sync_websocket_plugin()
            # Pop from queue immediately at create_connection() call time (FIFO).
            with plugin._registry_lock:
                if not plugin._session_queue:
                    source_id = plugin._unmocked_source_id()
                    hint = plugin.format_unmocked_hint(source_id, args, kwargs)
                    raise UnmockedInteractionError(
                        source_id=source_id,
                        args=args,
                        kwargs=kwargs,
                        hint=hint,
                    )
                handle = plugin._session_queue.popleft()
            # Create the fake object and register it.
            fake_ws = _FakeSyncWebSocket(handle, plugin)
            plugin._register_connection(handle, fake_ws)
            # Execute the "connect" transition step.
            plugin._execute_step(handle, "connect", (), {}, "websocket:sync:connect")
            return fake_ws

        _wsc.create_connection = _patched_create_connection

    def _restore_patches(self) -> None:
        if SyncWebSocketPlugin._original_create_connection is not None:
            import websocket as _wsc

            _wsc.create_connection = SyncWebSocketPlugin._original_create_connection
            SyncWebSocketPlugin._original_create_connection = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        args = interaction.details.get("args", ())
        kwargs = interaction.details.get("kwargs", {})
        parts = [repr(a) for a in args]
        parts += [f"{k}={v!r}" for k, v in kwargs.items()]
        return f"[SyncWebSocketPlugin] websocket.{method}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"    bigfoot.sync_websocket_mock.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":")[-1] if ":" in source_id else source_id
        return (
            f"websocket.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.sync_websocket_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.sync_websocket_mock"
        method = interaction.details.get("method", "?")
        return f"    # {sm}: session step '{method}' recorded (state-machine, auto-asserted)"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config
        method = getattr(step, "method", "?")
        return (
            f"websocket.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
