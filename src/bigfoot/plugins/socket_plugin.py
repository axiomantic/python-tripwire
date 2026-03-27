"""SocketPlugin: intercepts socket.socket connect/send/sendall/recv/close."""

import socket
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, cast

from bigfoot._context import GuardPassThrough, get_verifier_or_raise
from bigfoot._firewall_request import SocketFirewallRequest
from bigfoot._state_machine_plugin import StateMachinePlugin, _StepSentinel
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

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

_SOCKET_CONNECT_ORIGINAL: Callable[..., Any] = socket.socket.connect
_SOCKET_SEND_ORIGINAL: Callable[..., Any] = socket.socket.send
_SOCKET_SENDALL_ORIGINAL: Callable[..., Any] = socket.socket.sendall
_SOCKET_RECV_ORIGINAL: Callable[..., Any] = socket.socket.recv
_SOCKET_CLOSE_ORIGINAL: Callable[..., Any] = socket.socket.close


# ---------------------------------------------------------------------------
# Module-level helper: find the SocketPlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_socket_plugin(
    firewall_request: SocketFirewallRequest | None = None,
) -> "SocketPlugin | None":
    verifier = get_verifier_or_raise(_SOURCE_CONNECT, firewall_request=firewall_request)
    for plugin in verifier._plugins:
        if isinstance(plugin, SocketPlugin):
            return plugin
    return None


# ---------------------------------------------------------------------------
# SocketPlugin
# ---------------------------------------------------------------------------


class SocketPlugin(StateMachinePlugin):
    """Socket interception plugin.

    Patches socket.socket.connect/send/sendall/recv/close at the class level.
    Uses reference counting so nested sandboxes work correctly.

    States: disconnected -> connected -> closed
    """

    # Saved originals, restored when count reaches 0.
    _original_connect: ClassVar[Callable[..., Any] | None] = None
    _original_send: ClassVar[Callable[..., Any] | None] = None
    _original_sendall: ClassVar[Callable[..., Any] | None] = None
    _original_recv: ClassVar[Callable[..., Any] | None] = None
    _original_close: ClassVar[Callable[..., Any] | None] = None

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        self._connect_sentinel = _StepSentinel(_SOURCE_CONNECT)
        self._send_sentinel = _StepSentinel(_SOURCE_SEND)
        self._sendall_sentinel = _StepSentinel(_SOURCE_SENDALL)
        self._recv_sentinel = _StepSentinel(_SOURCE_RECV)
        self._close_sentinel = _StepSentinel(_SOURCE_CLOSE)

    @property
    def connect(self) -> _StepSentinel:
        return self._connect_sentinel

    @property
    def send(self) -> _StepSentinel:
        return self._send_sentinel

    @property
    def sendall(self) -> _StepSentinel:
        return self._sendall_sentinel

    @property
    def recv(self) -> _StepSentinel:
        return self._recv_sentinel

    @property
    def close(self) -> _StepSentinel:
        return self._close_sentinel

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

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def install_patches(self) -> None:
        SocketPlugin._original_connect = socket.socket.connect
        SocketPlugin._original_send = socket.socket.send
        SocketPlugin._original_sendall = socket.socket.sendall
        SocketPlugin._original_recv = socket.socket.recv
        SocketPlugin._original_close = socket.socket.close

        def _patched_connect(sock_self: socket.socket, address: object) -> None:
            if isinstance(address, tuple) and len(address) >= 2:
                host = str(address[0])
                port = int(address[1])
            else:
                host = str(address)
                port = 0
            family_str = (
                sock_self.family.name
                if hasattr(sock_self.family, "name")
                else str(sock_self.family)
            )
            fw_request = SocketFirewallRequest(host=host, port=port, family=family_str)
            try:
                plugin = _get_socket_plugin(firewall_request=fw_request)
            except GuardPassThrough:
                return cast(None, _SOCKET_CONNECT_ORIGINAL(sock_self, address))
            if plugin is None:
                return cast(None, _SOCKET_CONNECT_ORIGINAL(sock_self, address))
            handle = plugin._bind_connection(sock_self)
            plugin._execute_step(
                handle, "connect", (address,), {}, _SOURCE_CONNECT,
                details={"host": host, "port": port},
            )

        def _patched_send(
            sock_self: socket.socket,
            data: bytes | bytearray | memoryview,
            flags: int = 0,
        ) -> int:
            try:
                plugin = _get_socket_plugin()
            except GuardPassThrough:
                return cast(int, _SOCKET_SEND_ORIGINAL(sock_self, data, flags))
            if plugin is None:
                return cast(int, _SOCKET_SEND_ORIGINAL(sock_self, data, flags))
            handle = plugin._lookup_session(sock_self)
            return int(
                plugin._execute_step(
                    handle, "send", (data,), {"flags": flags}, _SOURCE_SEND,
                    details={"data": bytes(data)},
                )
            )

        def _patched_sendall(
            sock_self: socket.socket,
            data: bytes | bytearray | memoryview,
            flags: int = 0,
        ) -> None:
            try:
                plugin = _get_socket_plugin()
            except GuardPassThrough:
                return cast(None, _SOCKET_SENDALL_ORIGINAL(sock_self, data, flags))
            if plugin is None:
                return cast(None, _SOCKET_SENDALL_ORIGINAL(sock_self, data, flags))
            handle = plugin._lookup_session(sock_self)
            plugin._execute_step(
                handle, "sendall", (data,), {"flags": flags}, _SOURCE_SENDALL,
                details={"data": bytes(data)},
            )

        def _patched_recv(sock_self: socket.socket, bufsize: int, flags: int = 0) -> bytes:
            try:
                plugin = _get_socket_plugin()
            except GuardPassThrough:
                return cast(bytes, _SOCKET_RECV_ORIGINAL(sock_self, bufsize, flags))
            if plugin is None:
                return cast(bytes, _SOCKET_RECV_ORIGINAL(sock_self, bufsize, flags))
            handle = plugin._lookup_session(sock_self)
            result, interaction = plugin._execute_step(
                handle, "recv", (bufsize,), {"flags": flags}, _SOURCE_RECV,
                details={"size": bufsize, "data": b""},
                return_interaction=True,
            )
            data = bytes(result)
            interaction.details["data"] = data
            return data

        def _patched_close(sock_self: socket.socket) -> None:
            try:
                plugin = _get_socket_plugin()
            except GuardPassThrough:
                return cast(None, _SOCKET_CLOSE_ORIGINAL(sock_self))
            if plugin is None:
                return cast(None, _SOCKET_CLOSE_ORIGINAL(sock_self))
            handle = plugin._lookup_session(sock_self)
            plugin._execute_step(
                handle, "close", (), {}, _SOURCE_CLOSE,
                details={},
            )
            plugin._release_session(sock_self)

        setattr(socket.socket, "connect", _patched_connect)
        setattr(socket.socket, "send", _patched_send)
        setattr(socket.socket, "sendall", _patched_sendall)
        setattr(socket.socket, "recv", _patched_recv)
        setattr(socket.socket, "close", _patched_close)

    def restore_patches(self) -> None:
        if SocketPlugin._original_connect is not None:
            setattr(socket.socket, "connect", SocketPlugin._original_connect)
            SocketPlugin._original_connect = None
        if SocketPlugin._original_send is not None:
            setattr(socket.socket, "send", SocketPlugin._original_send)
            SocketPlugin._original_send = None
        if SocketPlugin._original_sendall is not None:
            setattr(socket.socket, "sendall", SocketPlugin._original_sendall)
            SocketPlugin._original_sendall = None
        if SocketPlugin._original_recv is not None:
            setattr(socket.socket, "recv", SocketPlugin._original_recv)
            SocketPlugin._original_recv = None
        if SocketPlugin._original_close is not None:
            setattr(socket.socket, "close", SocketPlugin._original_close)
            SocketPlugin._original_close = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        sid = interaction.source_id
        method = sid.split(":", 1)[-1] if ":" in sid else sid
        details = interaction.details
        if sid == _SOURCE_CONNECT:
            return (
                f"[SocketPlugin] socket.connect(("
                f"{details.get('host', '?')!r}, {details.get('port', 0)!r}))"
            )
        if sid == _SOURCE_SEND:
            return f"[SocketPlugin] socket.send({details.get('data', b'')!r})"
        if sid == _SOURCE_SENDALL:
            return f"[SocketPlugin] socket.sendall({details.get('data', b'')!r})"
        if sid == _SOURCE_RECV:
            return f"[SocketPlugin] socket.recv({details.get('size', 0)!r})"
        if sid == _SOURCE_CLOSE:
            return "[SocketPlugin] socket.close()"
        return f"[SocketPlugin] socket.{method}(...)"

    def format_mock_hint(self, interaction: Interaction) -> str:
        sid = interaction.source_id
        method = sid.split(":", 1)[-1] if ":" in sid else sid
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
        sid = interaction.source_id
        if sid == _SOURCE_CONNECT:
            host = interaction.details.get("host", "?")
            port = interaction.details.get("port", 0)
            return f"    {sm}.assert_connect(host={host!r}, port={port!r})"
        if sid == _SOURCE_SEND:
            data = interaction.details.get("data", b"")
            return f"    {sm}.assert_send(data={data!r})"
        if sid == _SOURCE_SENDALL:
            data = interaction.details.get("data", b"")
            return f"    {sm}.assert_sendall(data={data!r})"
        if sid == _SOURCE_RECV:
            size = interaction.details.get("size", 0)
            data = interaction.details.get("data", b"")
            return f"    {sm}.assert_recv(size={size!r}, data={data!r})"
        if sid == _SOURCE_CLOSE:
            return f"    {sm}.assert_close()"
        return f"    # {sm}: unknown source_id={sid!r}"

    def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
        """Field-by-field comparison with dirty-equals support."""
        try:
            for key, expected_val in expected.items():
                actual_val = interaction.details.get(key)
                if expected_val != actual_val:
                    return False
            return True
        except Exception:
            return False

    def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
        """Return assertable fields for each step type."""
        if interaction.source_id == _SOURCE_CONNECT:
            return frozenset({"host", "port"})
        if interaction.source_id == _SOURCE_SEND:
            return frozenset({"data"})
        if interaction.source_id == _SOURCE_SENDALL:
            return frozenset({"data"})
        if interaction.source_id == _SOURCE_RECV:
            return frozenset({"size", "data"})
        if interaction.source_id == _SOURCE_CLOSE:
            return frozenset()
        return frozenset(interaction.details.keys())

    def assert_connect(self, *, host: str, port: int) -> None:
        """Assert the next socket connect interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._connect_sentinel, host=host, port=port
        )

    def assert_send(self, *, data: bytes) -> None:
        """Assert the next socket send interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._send_sentinel, data=data
        )

    def assert_sendall(self, *, data: bytes) -> None:
        """Assert the next socket sendall interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._sendall_sentinel, data=data
        )

    def assert_recv(self, *, size: int, data: bytes) -> None:
        """Assert the next socket recv interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._recv_sentinel, size=size, data=data
        )

    def assert_close(self) -> None:
        """Assert the next socket close interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._close_sentinel)

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config
        method = getattr(step, "method", "?")
        return (
            f"socket.socket.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
