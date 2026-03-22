"""GrpcPlugin: intercepts grpc.insecure_channel and grpc.secure_channel.

Uses per-method FIFO queues.
"""

from __future__ import annotations

import threading
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import GuardPassThrough, _guard_allowlist, get_verifier_or_raise
from bigfoot._errors import UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import grpc as grpc_lib

    _GRPC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _GRPC_AVAILABLE = False


# ---------------------------------------------------------------------------
# GrpcMockConfig
# ---------------------------------------------------------------------------


@dataclass
class GrpcMockConfig:
    """Configuration for a single mocked gRPC call invocation.

    Attributes:
        method: The gRPC service method path (e.g., "/package.Service/Method").
        call_type: One of "unary_unary", "unary_stream", "stream_unary", "stream_stream".
        returns: The value to return when this mock is consumed.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the mock is reported as unused if never triggered.
        registration_traceback: Captured automatically at creation time
            for use in error messages.
    """

    method: str
    call_type: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper: find the GrpcPlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_grpc_plugin() -> GrpcPlugin:
    verifier = get_verifier_or_raise("grpc:channel")
    for plugin in verifier._plugins:
        if isinstance(plugin, GrpcPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot GrpcPlugin interceptor is active but no "
        "GrpcPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _GrpcSentinel:
    """Opaque handle carrying a source_id; passed to assert_interaction for source matching."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


# ---------------------------------------------------------------------------
# _MockStreamIterator
# ---------------------------------------------------------------------------


class _MockStreamIterator:
    """Yields pre-configured responses for server-streaming/bidi RPCs."""

    def __init__(self, responses: list[Any], raises: BaseException | None = None) -> None:
        self._responses = iter(responses)
        self._raises = raises

    def __iter__(self) -> _MockStreamIterator:
        return self

    def __next__(self) -> Any:  # noqa: ANN401
        try:
            return next(self._responses)
        except StopIteration:
            if self._raises is not None:
                raise self._raises
            raise


# ---------------------------------------------------------------------------
# _GrpcCallable: proxy for multi-callable objects
# ---------------------------------------------------------------------------


class _GrpcCallable:
    """Proxy returned by _FakeChannel.unary_unary() etc.

    When called, pops from the FIFO queue, records the interaction, and
    returns the mock response (or raises the configured exception).
    """

    def __init__(self, method: str, call_type: str) -> None:
        self._method = method
        self._call_type = call_type

    def __call__(
        self, request: Any = None, timeout: Any = None,  # noqa: ANN401
        metadata: Any = None, **kwargs: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        try:
            plugin = _get_grpc_plugin()
        except GuardPassThrough:
            # Guard mode allows grpc: this should not happen in normal flow
            # because the channel factory already returns a real channel when
            # guard allows. But handle it defensively.
            raise RuntimeError(
                "BUG: GuardPassThrough reached _GrpcCallable.__call__. "
                "The channel factory should have returned a real channel."
            ) from None
        queue_key = f"{self._call_type}:{self._method}"
        source_id = f"grpc:{self._call_type}:{self._method}"

        with plugin._registry_lock:
            queue = plugin._queues.get(queue_key)
            if not queue:
                hint = plugin.format_unmocked_hint(source_id, (), {})
                raise UnmockedInteractionError(
                    source_id=source_id,
                    args=(),
                    kwargs={},
                    hint=hint,
                )
            config = queue.popleft()

        # For streaming request types, eagerly consume the request iterator so
        # that details["request"] contains a concrete list rather than an
        # exhausted iterator.
        if self._call_type in ("stream_unary", "stream_stream"):
            request = list(request) if request is not None else []

        details_grpc: dict[str, Any] = {
            "method": self._method,
            "call_type": self._call_type,
            "request": request,
            "metadata": metadata,
        }
        if config.raises is not None:
            details_grpc["raised"] = config.raises
        interaction = Interaction(
            source_id=source_id,
            sequence=0,
            details=details_grpc,
            plugin=plugin,
        )
        plugin.record(interaction)
        # No mark_asserted() -- test authors must call assert_interaction() or typed helpers

        if config.raises is not None:
            if self._call_type in ("unary_stream", "stream_stream"):
                # For streaming responses with raises, return iterator that yields
                # partial results then raises
                return _MockStreamIterator(config.returns, raises=config.raises)
            raise config.raises

        if self._call_type in ("unary_stream", "stream_stream"):
            return _MockStreamIterator(config.returns)

        return config.returns


# ---------------------------------------------------------------------------
# _FakeChannel: proxy for grpc.Channel
# ---------------------------------------------------------------------------


class _FakeChannel:
    """Proxy channel returned by patched grpc.insecure_channel/secure_channel."""

    def __init__(self, target: str, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        self._target = target

    def unary_unary(self, method: str, *args: Any, **kwargs: Any) -> _GrpcCallable:  # noqa: ANN401
        return _GrpcCallable(method, "unary_unary")

    def unary_stream(self, method: str, *args: Any, **kwargs: Any) -> _GrpcCallable:  # noqa: ANN401
        return _GrpcCallable(method, "unary_stream")

    def stream_unary(self, method: str, *args: Any, **kwargs: Any) -> _GrpcCallable:  # noqa: ANN401
        return _GrpcCallable(method, "stream_unary")

    def stream_stream(self, method: str, *args: Any, **kwargs: Any) -> _GrpcCallable:  # noqa: ANN401
        return _GrpcCallable(method, "stream_stream")

    def subscribe(self, callback: Any, try_to_connect: bool = False) -> None:  # noqa: ANN401
        pass

    def unsubscribe(self, callback: Any) -> None:  # noqa: ANN401
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> _FakeChannel:
        return self

    def __exit__(self, *args: Any) -> None:  # noqa: ANN401
        self.close()


# ---------------------------------------------------------------------------
# Patched channel factories
# ---------------------------------------------------------------------------


def _patched_insecure_channel(target: str, *args: Any, **kwargs: Any) -> _FakeChannel:  # noqa: ANN401
    from bigfoot._errors import SandboxNotActiveError  # noqa: PLC0415

    _original = GrpcPlugin._original_insecure_channel
    assert _original is not None
    # Check allowlist FIRST - bypasses both guard and sandbox
    if "grpc" in _guard_allowlist.get():
        return cast(_FakeChannel, _original(target, *args, **kwargs))
    try:
        get_verifier_or_raise("grpc:channel")
    except GuardPassThrough:
        return cast(_FakeChannel, _original(target, *args, **kwargs))
    except SandboxNotActiveError:
        pass  # No sandbox, no guard: proceed with fake channel
    # GuardedCallError propagates naturally (not caught here)
    return _FakeChannel(target, *args, **kwargs)


def _patched_secure_channel(  # noqa: ANN401
    target: str, credentials: Any, *args: Any, **kwargs: Any,  # noqa: ANN401
) -> _FakeChannel:
    from bigfoot._errors import SandboxNotActiveError  # noqa: PLC0415

    _original = GrpcPlugin._original_secure_channel
    assert _original is not None
    # Check allowlist FIRST - bypasses both guard and sandbox
    if "grpc" in _guard_allowlist.get():
        return cast(_FakeChannel, _original(target, credentials, *args, **kwargs))
    try:
        get_verifier_or_raise("grpc:channel")
    except GuardPassThrough:
        return cast(_FakeChannel, _original(target, credentials, *args, **kwargs))
    except SandboxNotActiveError:
        pass  # No sandbox, no guard: proceed with fake channel
    # GuardedCallError propagates naturally (not caught here)
    return _FakeChannel(target, *args, **kwargs)


# ---------------------------------------------------------------------------
# GrpcPlugin
# ---------------------------------------------------------------------------


class GrpcPlugin(BasePlugin):
    """gRPC interception plugin.

    Patches grpc.insecure_channel and grpc.secure_channel at the module level.
    Uses reference counting so nested sandboxes work correctly.

    Each (call_type, method) pair has its own FIFO deque of GrpcMockConfig objects.
    """

    # Saved originals, restored when count reaches 0.
    _original_insecure_channel: ClassVar[Callable[..., Any] | None] = None
    _original_secure_channel: ClassVar[Callable[..., Any] | None] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[GrpcMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API: register mock calls
    # ------------------------------------------------------------------

    def _mock_call(
        self,
        call_type: str,
        method: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Internal: register a mock for a single gRPC call invocation."""
        config = GrpcMockConfig(
            method=method,
            call_type=call_type,
            returns=returns,
            raises=raises,
            required=required,
        )
        queue_key = f"{call_type}:{method}"
        with self._registry_lock:
            if queue_key not in self._queues:
                self._queues[queue_key] = deque()
            self._queues[queue_key].append(config)

    def mock_unary_unary(
        self,
        method: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a unary-unary gRPC call."""
        self._mock_call("unary_unary", method, returns=returns, raises=raises, required=required)

    def mock_unary_stream(
        self,
        method: str,
        *,
        returns: list[Any],
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a unary-stream (server streaming) gRPC call."""
        self._mock_call("unary_stream", method, returns=returns, raises=raises, required=required)

    def mock_stream_unary(
        self,
        method: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a stream-unary (client streaming) gRPC call."""
        self._mock_call("stream_unary", method, returns=returns, raises=raises, required=required)

    def mock_stream_stream(
        self,
        method: str,
        *,
        returns: list[Any],
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a stream-stream (bidi streaming) gRPC call."""
        self._mock_call("stream_stream", method, returns=returns, raises=raises, required=required)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def install_patches(self) -> None:
        """Install gRPC channel patches."""
        if not _GRPC_AVAILABLE:
            raise ImportError(
                "Install bigfoot[grpc] to use GrpcPlugin: pip install bigfoot[grpc]"
            )
        GrpcPlugin._original_insecure_channel = grpc_lib.insecure_channel
        GrpcPlugin._original_secure_channel = grpc_lib.secure_channel
        grpc_lib.insecure_channel = _patched_insecure_channel
        grpc_lib.secure_channel = _patched_secure_channel

    def restore_patches(self) -> None:
        """Restore original gRPC channel functions."""
        if GrpcPlugin._original_insecure_channel is not None:
            grpc_lib.insecure_channel = GrpcPlugin._original_insecure_channel
            GrpcPlugin._original_insecure_channel = None
        if GrpcPlugin._original_secure_channel is not None:
            grpc_lib.secure_channel = GrpcPlugin._original_secure_channel
            GrpcPlugin._original_secure_channel = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

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

    def get_unused_mocks(self) -> list[GrpcMockConfig]:
        """Return all GrpcMockConfig with required=True still in any queue."""
        unused: list[GrpcMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        call_type = interaction.details.get("call_type", "?")
        method = interaction.details.get("method", "?")
        return f"[GrpcPlugin] {call_type} {method}"

    def format_mock_hint(self, interaction: Interaction) -> str:
        call_type = interaction.details.get("call_type", "?")
        method = interaction.details.get("method", "?")
        return f"    bigfoot.grpc_mock.mock_{call_type}({method!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        # source_id is like "grpc:unary_unary:/pkg.Svc/Do"
        parts = source_id.split(":", 2)
        call_type = parts[1] if len(parts) > 1 else "?"
        method = parts[2] if len(parts) > 2 else "?"
        return (
            f"grpc.{call_type}({method!r}) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    bigfoot.grpc_mock.mock_{call_type}({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.grpc_mock"
        call_type = interaction.details.get("call_type", "?")
        method = interaction.details.get("method", "?")
        request = interaction.details.get("request")
        metadata = interaction.details.get("metadata")
        return (
            f"    {sm}.assert_{call_type}(\n"
            f"        method={method!r},\n"
            f"        request={request!r},\n"
            f"        metadata={metadata!r},\n"
            f"    )"
        )

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config = cast(GrpcMockConfig, mock_config)
        call_type = getattr(config, "call_type", "?")
        method = getattr(config, "method", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"grpc.{call_type}({method!r}) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    _ABSENT: ClassVar[object] = object()

    def _assert_call(
        self,
        call_type: str,
        method: str,
        request: Any,  # noqa: ANN401
        metadata: Any = None,  # noqa: ANN401
        raised: Any = _ABSENT,  # noqa: ANN401
    ) -> None:
        """Common implementation for typed assertion helpers."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"grpc:{call_type}:{method}"
        sentinel = _GrpcSentinel(source_id)
        expected: dict[str, Any] = {
            "method": method,
            "call_type": call_type,
            "request": request,
            "metadata": metadata,
        }
        if raised is not GrpcPlugin._ABSENT:
            expected["raised"] = raised
        _get_test_verifier_or_raise().assert_interaction(sentinel, **expected)

    def assert_unary_unary(
        self,
        method: str,
        request: Any,  # noqa: ANN401
        metadata: Any = None,  # noqa: ANN401
        raised: Any = _ABSENT,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next unary_unary interaction."""
        self._assert_call("unary_unary", method, request, metadata, raised)

    def assert_unary_stream(
        self,
        method: str,
        request: Any,  # noqa: ANN401
        metadata: Any = None,  # noqa: ANN401
        raised: Any = _ABSENT,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next unary_stream interaction."""
        self._assert_call("unary_stream", method, request, metadata, raised)

    def assert_stream_unary(
        self,
        method: str,
        request: Any,  # noqa: ANN401
        metadata: Any = None,  # noqa: ANN401
        raised: Any = _ABSENT,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next stream_unary interaction."""
        self._assert_call("stream_unary", method, request, metadata, raised)

    def assert_stream_stream(
        self,
        method: str,
        request: Any,  # noqa: ANN401
        metadata: Any = None,  # noqa: ANN401
        raised: Any = _ABSENT,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next stream_stream interaction."""
        self._assert_call("stream_stream", method, request, metadata, raised)
