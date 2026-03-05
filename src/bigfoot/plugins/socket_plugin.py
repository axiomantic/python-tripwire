"""SocketPlugin: intercepts socket.socket connect/send/sendall/recv/close."""

import socket
import threading
from typing import Any, ClassVar

from bigfoot._context import _get_verifier_or_raise
from bigfoot._state_machine_plugin import StateMachinePlugin
from bigfoot._timeline import Interaction

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_CONNECT = "socket:connect"
_SOURCE_SEND = "socket:send"
_SOURCE_SENDALL = "socket:sendall"
_SOURCE_RECV = "socket:recv"
_SOURCE_CLOSE = "socket:close"

# ---------------------------------------------------------------------------
# Import-time constants — captured BEFORE any patches are installed.
# ---------------------------------------------------------------------------

_SOCKET_CONNECT_ORIGINAL: Any = socket.socket.connect
_SOCKET_SEND_ORIGINAL: Any = socket.socket.send
_SOCKET_SENDALL_ORIGINAL: Any = socket.socket.sendall
_SOCKET_RECV_ORIGINAL: Any = socket.socket.recv
_SOCKET_CLOSE_ORIGINAL: Any = socket.socket.close


# ---------------------------------------------------------------------------
# Module-level helper: find the SocketPlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_socket_plugin() -> "SocketPlugin":
    verifier = _get_verifier_or_raise(_SOURCE_CONNECT)
    for plugin in verifier._plugins:
        if isinstance(plugin, SocketPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot SocketPlugin interceptor is active but no "
        "SocketPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# SocketPlugin
# ---------------------------------------------------------------------------


class SocketPlugin(StateMachinePlugin):
    """Socket interception plugin.

    Patches socket.socket.connect/send/sendall/recv/close at the class level.
    Uses reference counting so nested sandboxes work correctly.

    States: disconnected -> connected -> closed
    """

    # Class-level reference counting — shared across all instances/verifiers.
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    # Saved originals, restored when count reaches 0.
    _original_connect: ClassVar[Any] = None
    _original_send: ClassVar[Any] = None
    _original_sendall: ClassVar[Any] = None
    _original_recv: ClassVar[Any] = None
    _original_close: ClassVar[Any] = None

    # ------------------------------------------------------------------
    # StateMachinePlugin abstract methods
    # ------------------------------------------------------------------

    def _initial_state(self) -> str:
        return "disconnected"

    def _transitions(self) -> dict[str, dict[str, str]]:
        return {
            "connect": {"disconnected": "connected"},
            "send": {"connected": "connected"},
            "sendall": {"connected": "connected"},
            "recv": {"connected": "connected"},
            "close": {"connected": "closed"},
        }

    def _unmocked_source_id(self) -> str:
        return "socket:connect"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        with SocketPlugin._install_lock:
            if SocketPlugin._install_count == 0:
                self._install_patches()
            SocketPlugin._install_count += 1

    def deactivate(self) -> None:
        with SocketPlugin._install_lock:
            SocketPlugin._install_count = max(0, SocketPlugin._install_count - 1)
            if SocketPlugin._install_count == 0:
                self._restore_patches()

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        SocketPlugin._original_connect = socket.socket.connect
        SocketPlugin._original_send = socket.socket.send
        SocketPlugin._original_sendall = socket.socket.sendall
        SocketPlugin._original_recv = socket.socket.recv
        SocketPlugin._original_close = socket.socket.close

        def _patched_connect(sock_self: socket.socket, address: tuple[object, ...]) -> None:
            plugin = _get_socket_plugin()
            handle = plugin._bind_connection(sock_self)
            plugin._execute_step(handle, "connect", (address,), {}, _SOURCE_CONNECT)

        def _patched_send(
            sock_self: socket.socket,
            data: bytes | bytearray | memoryview,
            flags: int = 0,
        ) -> int:
            plugin = _get_socket_plugin()
            handle = plugin._lookup_session(sock_self)
            return int(
                plugin._execute_step(handle, "send", (data,), {"flags": flags}, _SOURCE_SEND)
            )

        def _patched_sendall(
            sock_self: socket.socket,
            data: bytes | bytearray | memoryview,
            flags: int = 0,
        ) -> None:
            plugin = _get_socket_plugin()
            handle = plugin._lookup_session(sock_self)
            plugin._execute_step(handle, "sendall", (data,), {"flags": flags}, _SOURCE_SENDALL)

        def _patched_recv(sock_self: socket.socket, bufsize: int, flags: int = 0) -> bytes:
            plugin = _get_socket_plugin()
            handle = plugin._lookup_session(sock_self)
            result = plugin._execute_step(
                handle, "recv", (bufsize,), {"flags": flags}, _SOURCE_RECV
            )
            return bytes(result)

        def _patched_close(sock_self: socket.socket) -> None:
            plugin = _get_socket_plugin()
            handle = plugin._lookup_session(sock_self)
            plugin._execute_step(handle, "close", (), {}, _SOURCE_CLOSE)
            plugin._release_session(sock_self)

        socket.socket.connect = _patched_connect  # type: ignore[method-assign, assignment]
        socket.socket.send = _patched_send  # type: ignore[method-assign, assignment]
        socket.socket.sendall = _patched_sendall  # type: ignore[method-assign, assignment]
        socket.socket.recv = _patched_recv  # type: ignore[method-assign, assignment]
        socket.socket.close = _patched_close  # type: ignore[method-assign, assignment]

    def _restore_patches(self) -> None:
        if SocketPlugin._original_connect is not None:
            socket.socket.connect = SocketPlugin._original_connect  # type: ignore[method-assign]
            SocketPlugin._original_connect = None
        if SocketPlugin._original_send is not None:
            socket.socket.send = SocketPlugin._original_send  # type: ignore[method-assign]
            SocketPlugin._original_send = None
        if SocketPlugin._original_sendall is not None:
            socket.socket.sendall = SocketPlugin._original_sendall  # type: ignore[method-assign]
            SocketPlugin._original_sendall = None
        if SocketPlugin._original_recv is not None:
            socket.socket.recv = SocketPlugin._original_recv  # type: ignore[method-assign]
            SocketPlugin._original_recv = None
        if SocketPlugin._original_close is not None:
            socket.socket.close = SocketPlugin._original_close  # type: ignore[method-assign]
            SocketPlugin._original_close = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        args = interaction.details.get("args", ())
        kwargs = interaction.details.get("kwargs", {})
        parts = [repr(a) for a in args]
        parts += [f"{k}={v!r}" for k, v in kwargs.items() if k != "flags" or v != 0]
        return f"[SocketPlugin] socket.{method}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"    bigfoot.socket_mock.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        return (
            f"socket.socket.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.socket_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.socket_mock"
        method = interaction.details.get("method", "?")
        return f"    # {sm}: session step '{method}' recorded (state-machine, auto-asserted)"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config
        method = getattr(step, "method", "?")
        return (
            f"socket.socket.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
