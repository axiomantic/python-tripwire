"""PikaPlugin: intercepts pika.BlockingConnection via class replacement."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._context import _get_verifier_or_raise
from bigfoot._state_machine_plugin import StateMachinePlugin, _StepSentinel
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import pika as pika_lib

    _PIKA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PIKA_AVAILABLE = False

# ---------------------------------------------------------------------------
# Import-time constant -- captured BEFORE any patches are installed.
# ---------------------------------------------------------------------------

_ORIGINAL_BLOCKING_CONNECTION: Any = pika_lib.BlockingConnection if _PIKA_AVAILABLE else None

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_CONNECT = "pika:connect"
_SOURCE_CHANNEL = "pika:channel"
_SOURCE_PUBLISH = "pika:publish"
_SOURCE_CONSUME = "pika:consume"
_SOURCE_ACK = "pika:ack"
_SOURCE_NACK = "pika:nack"
_SOURCE_CLOSE = "pika:close"

# ---------------------------------------------------------------------------
# Module-level helper: find the PikaPlugin on the active verifier
# ---------------------------------------------------------------------------


def _find_pika_plugin() -> PikaPlugin:
    verifier = _get_verifier_or_raise("pika:connect")
    for plugin in verifier._plugins:
        if isinstance(plugin, PikaPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot PikaPlugin interceptor is active but no "
        "PikaPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# _FakeChannel
# ---------------------------------------------------------------------------


class _FakeChannel:
    """Fake pika channel that routes all operations through PikaPlugin."""

    def __init__(self, connection: _FakeBlockingConnection) -> None:
        self._connection = connection

    def basic_publish(
        self,
        exchange: str = "",
        routing_key: str = "",
        body: bytes | str = b"",
        properties: Any = None,  # noqa: ANN401
        **kwargs: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        plugin = _find_pika_plugin()
        handle = plugin._lookup_session(self._connection)
        return plugin._execute_step(
            handle, "publish", (), {}, _SOURCE_PUBLISH,
            details={
                "exchange": exchange,
                "routing_key": routing_key,
                "body": body,
                "properties": properties,
            },
        )

    def basic_consume(
        self,
        queue: str = "",
        auto_ack: bool = False,
        **kwargs: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        plugin = _find_pika_plugin()
        handle = plugin._lookup_session(self._connection)
        return plugin._execute_step(
            handle, "consume", (), {}, _SOURCE_CONSUME,
            details={"queue": queue, "auto_ack": auto_ack},
        )

    def basic_ack(self, delivery_tag: int = 0, **kwargs: Any) -> Any:  # noqa: ANN401
        plugin = _find_pika_plugin()
        handle = plugin._lookup_session(self._connection)
        return plugin._execute_step(
            handle, "ack", (), {}, _SOURCE_ACK,
            details={"delivery_tag": delivery_tag},
        )

    def basic_nack(
        self,
        delivery_tag: int = 0,
        requeue: bool = True,
        **kwargs: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        plugin = _find_pika_plugin()
        handle = plugin._lookup_session(self._connection)
        return plugin._execute_step(
            handle, "nack", (), {}, _SOURCE_NACK,
            details={"delivery_tag": delivery_tag, "requeue": requeue},
        )


# ---------------------------------------------------------------------------
# _FakeBlockingConnection
# ---------------------------------------------------------------------------


class _FakeBlockingConnection:
    """Fake pika.BlockingConnection that routes all operations through PikaPlugin."""

    def __init__(self, parameters: Any = None, **kwargs: Any) -> None:  # noqa: ANN401
        plugin = _find_pika_plugin()
        plugin._bind_connection(self)
        handle = plugin._lookup_session(self)

        # Extract connection parameters
        if parameters is not None and hasattr(parameters, "host"):
            host = parameters.host
            port = parameters.port
            virtual_host = parameters.virtual_host
        else:
            host = "localhost"
            port = 5672
            virtual_host = "/"

        plugin._execute_step(
            handle, "connect", (), {}, _SOURCE_CONNECT,
            details={"host": host, "port": port, "virtual_host": virtual_host},
        )

    def channel(self, **kwargs: Any) -> _FakeChannel:  # noqa: ANN401
        plugin = _find_pika_plugin()
        handle = plugin._lookup_session(self)
        plugin._execute_step(
            handle, "channel", (), {}, _SOURCE_CHANNEL,
            details={},
        )
        return _FakeChannel(self)

    def close(self, **kwargs: Any) -> Any:  # noqa: ANN401
        plugin = _find_pika_plugin()
        handle = plugin._lookup_session(self)
        result = plugin._execute_step(
            handle, "close", (), {}, _SOURCE_CLOSE,
            details={},
        )
        plugin._release_session(self)
        return result


# ---------------------------------------------------------------------------
# PikaPlugin
# ---------------------------------------------------------------------------


class PikaPlugin(StateMachinePlugin):
    """Pika (RabbitMQ) interception plugin.

    Replaces pika.BlockingConnection with _FakeBlockingConnection at activate()
    time and restores the original at deactivate() time. Uses reference counting
    so nested sandboxes work correctly.

    States: disconnected -> connected -> channel_open -> closed
    close is also valid from connected (skipping channel_open).
    """

    # Class-level reference counting -- shared across all instances/verifiers.
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    # Saved original, restored when count reaches 0.
    _original_blocking_connection: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._connect_sentinel = _StepSentinel(_SOURCE_CONNECT)
        self._channel_sentinel = _StepSentinel(_SOURCE_CHANNEL)
        self._publish_sentinel = _StepSentinel(_SOURCE_PUBLISH)
        self._consume_sentinel = _StepSentinel(_SOURCE_CONSUME)
        self._ack_sentinel = _StepSentinel(_SOURCE_ACK)
        self._nack_sentinel = _StepSentinel(_SOURCE_NACK)
        self._close_sentinel = _StepSentinel(_SOURCE_CLOSE)

    @property
    def connect(self) -> _StepSentinel:
        return self._connect_sentinel

    @property
    def channel(self) -> _StepSentinel:
        return self._channel_sentinel

    @property
    def publish(self) -> _StepSentinel:
        return self._publish_sentinel

    @property
    def consume(self) -> _StepSentinel:
        return self._consume_sentinel

    @property
    def ack(self) -> _StepSentinel:
        return self._ack_sentinel

    @property
    def nack(self) -> _StepSentinel:
        return self._nack_sentinel

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
            "channel": {"connected": "channel_open"},
            "publish": {"channel_open": "channel_open"},
            "consume": {"channel_open": "channel_open"},
            "ack": {"channel_open": "channel_open"},
            "nack": {"channel_open": "channel_open"},
            "close": {
                "channel_open": "closed",
                "connected": "closed",
            },
        }

    def _unmocked_source_id(self) -> str:
        return "pika:connect"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        if not _PIKA_AVAILABLE:  # pragma: no cover
            return
        with PikaPlugin._install_lock:
            if PikaPlugin._install_count == 0:
                PikaPlugin._original_blocking_connection = pika_lib.BlockingConnection
                pika_lib.BlockingConnection = _FakeBlockingConnection
            PikaPlugin._install_count += 1

    def deactivate(self) -> None:
        if not _PIKA_AVAILABLE:  # pragma: no cover
            return
        with PikaPlugin._install_lock:
            PikaPlugin._install_count = max(0, PikaPlugin._install_count - 1)
            if PikaPlugin._install_count == 0:
                if PikaPlugin._original_blocking_connection is not None:
                    pika_lib.BlockingConnection = PikaPlugin._original_blocking_connection
                    PikaPlugin._original_blocking_connection = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        sid = interaction.source_id
        method = sid.split(":", 1)[-1] if ":" in sid else sid
        details = interaction.details
        if sid == _SOURCE_CONNECT:
            return (
                f"[PikaPlugin] pika.connect("
                f"host={details.get('host', '?')!r}, port={details.get('port', 0)!r}, "
                f"virtual_host={details.get('virtual_host', '/')!r})"
            )
        if sid == _SOURCE_CHANNEL:
            return "[PikaPlugin] connection.channel()"
        if sid == _SOURCE_PUBLISH:
            return (
                f"[PikaPlugin] channel.basic_publish("
                f"exchange={details.get('exchange', '')!r}, "
                f"routing_key={details.get('routing_key', '')!r})"
            )
        if sid == _SOURCE_CONSUME:
            return (
                f"[PikaPlugin] channel.basic_consume("
                f"queue={details.get('queue', '')!r}, "
                f"auto_ack={details.get('auto_ack', False)!r})"
            )
        if sid == _SOURCE_ACK:
            tag = details.get('delivery_tag', 0)
            return f"[PikaPlugin] channel.basic_ack(delivery_tag={tag!r})"
        if sid == _SOURCE_NACK:
            return (
                f"[PikaPlugin] channel.basic_nack("
                f"delivery_tag={details.get('delivery_tag', 0)!r}, "
                f"requeue={details.get('requeue', True)!r})"
            )
        if sid == _SOURCE_CLOSE:
            return "[PikaPlugin] connection.close()"
        return f"[PikaPlugin] pika.{method}(...)"

    def format_mock_hint(self, interaction: Interaction) -> str:
        sid = interaction.source_id
        method = sid.split(":")[-1] if ":" in sid else sid
        return f"    bigfoot.pika_mock.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":")[-1] if ":" in source_id else source_id
        return (
            f"pika.BlockingConnection.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.pika_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.pika_mock"
        sid = interaction.source_id
        if sid == _SOURCE_CONNECT:
            host = interaction.details.get("host", "?")
            port = interaction.details.get("port", 0)
            virtual_host = interaction.details.get("virtual_host", "/")
            return (
                f"    {sm}.assert_connect(host={host!r}, "
                f"port={port!r}, virtual_host={virtual_host!r})"
            )
        if sid == _SOURCE_CHANNEL:
            return f"    {sm}.assert_channel()"
        if sid == _SOURCE_PUBLISH:
            exchange = interaction.details.get("exchange", "")
            routing_key = interaction.details.get("routing_key", "")
            body = interaction.details.get("body")
            properties = interaction.details.get("properties")
            return (
                f"    {sm}.assert_publish("
                f"exchange={exchange!r}, routing_key={routing_key!r}, "
                f"body={body!r}, properties={properties!r})"
            )
        if sid == _SOURCE_CONSUME:
            queue = interaction.details.get("queue", "")
            auto_ack = interaction.details.get("auto_ack", False)
            return f"    {sm}.assert_consume(queue={queue!r}, auto_ack={auto_ack!r})"
        if sid == _SOURCE_ACK:
            delivery_tag = interaction.details.get("delivery_tag", 0)
            return f"    {sm}.assert_ack(delivery_tag={delivery_tag!r})"
        if sid == _SOURCE_NACK:
            delivery_tag = interaction.details.get("delivery_tag", 0)
            requeue = interaction.details.get("requeue", True)
            return f"    {sm}.assert_nack(delivery_tag={delivery_tag!r}, requeue={requeue!r})"
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
        """Return assertable fields for each step type.

        channel and close are state-transition-only steps with no data fields,
        so they return frozenset().
        """
        return frozenset(interaction.details.keys())

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def assert_connect(self, *, host: str, port: int, virtual_host: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._connect_sentinel, host=host, port=port, virtual_host=virtual_host
        )

    def assert_channel(self) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._channel_sentinel)

    def assert_publish(
        self, *, exchange: str, routing_key: str, body: Any, properties: Any = None,  # noqa: ANN401
    ) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._publish_sentinel,
            exchange=exchange, routing_key=routing_key, body=body, properties=properties,
        )

    def assert_consume(self, *, queue: str, auto_ack: bool) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._consume_sentinel, queue=queue, auto_ack=auto_ack
        )

    def assert_ack(self, *, delivery_tag: int) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._ack_sentinel, delivery_tag=delivery_tag
        )

    def assert_nack(self, *, delivery_tag: int, requeue: bool) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._nack_sentinel, delivery_tag=delivery_tag, requeue=requeue
        )

    def assert_close(self) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._close_sentinel)

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config
        method = getattr(step, "method", "?")
        return (
            f"pika.BlockingConnection.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
