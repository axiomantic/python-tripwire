"""McpPlugin: intercepts MCP ClientSession and Server handler calls with per-method FIFO queues."""

from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import _get_verifier_or_raise
from bigfoot._errors import UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    from mcp.client.session import ClientSession as _ClientSession
    from mcp.server.lowlevel.server import Server as _Server

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MCP_AVAILABLE = False


# ---------------------------------------------------------------------------
# McpMockConfig
# ---------------------------------------------------------------------------


@dataclass
class McpMockConfig:
    """Configuration for a single mocked MCP call invocation.

    Attributes:
        direction: "client" or "server".
        method: One of "call_tool", "read_resource", "get_prompt".
        key: The tool_name, uri, or prompt_name used for queue keying.
        returns: The value to return when this mock is consumed.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the mock is reported as unused if never triggered.
        registration_traceback: Captured automatically at creation time.
    """

    direction: str
    method: str
    key: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper: find the McpPlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_mcp_plugin() -> McpPlugin:
    verifier = _get_verifier_or_raise("mcp:client:call_tool")
    for plugin in verifier._plugins:
        if isinstance(plugin, McpPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot McpPlugin interceptor is active but no "
        "McpPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _McpSentinel:
    """Opaque handle carrying a source_id; passed to assert_interaction for source matching."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


# ---------------------------------------------------------------------------
# Patched ClientSession methods
# ---------------------------------------------------------------------------


async def _patched_call_tool(
    self: Any,  # noqa: ANN401
    name: str,
    arguments: dict[str, Any] | None = None,
    *args: Any,  # noqa: ANN401
    **kwargs: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    plugin = _get_mcp_plugin()
    queue_key = f"client:call_tool:{name}"
    source_id = f"mcp:{queue_key}"

    with plugin._registry_lock:
        queue = plugin._queues.get(queue_key)
        if not queue:
            kw = {"name": name, "arguments": arguments}
            hint = plugin.format_unmocked_hint(source_id, (), kw)
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(),
                kwargs=kw,
                hint=hint,
            )
        config = queue.popleft()

    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details={
            "direction": "client",
            "method": "call_tool",
            "tool_name": name,
            "arguments": arguments if arguments is not None else {},
        },
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


async def _patched_read_resource(
    self: Any,  # noqa: ANN401
    uri: Any,  # noqa: ANN401
    *args: Any,  # noqa: ANN401
    **kwargs: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    plugin = _get_mcp_plugin()
    uri_str = str(uri)
    queue_key = f"client:read_resource:{uri_str}"
    source_id = f"mcp:{queue_key}"

    with plugin._registry_lock:
        queue = plugin._queues.get(queue_key)
        if not queue:
            hint = plugin.format_unmocked_hint(source_id, (), {"uri": uri_str})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(),
                kwargs={"uri": uri_str},
                hint=hint,
            )
        config = queue.popleft()

    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details={
            "direction": "client",
            "method": "read_resource",
            "uri": uri_str,
        },
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


async def _patched_get_prompt(
    self: Any,  # noqa: ANN401
    name: str,
    arguments: dict[str, str] | None = None,
    *args: Any,  # noqa: ANN401
    **kwargs: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    plugin = _get_mcp_plugin()
    queue_key = f"client:get_prompt:{name}"
    source_id = f"mcp:{queue_key}"

    with plugin._registry_lock:
        queue = plugin._queues.get(queue_key)
        if not queue:
            kw = {"name": name, "arguments": arguments}
            hint = plugin.format_unmocked_hint(source_id, (), kw)
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(),
                kwargs=kw,
                hint=hint,
            )
        config = queue.popleft()

    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details={
            "direction": "client",
            "method": "get_prompt",
            "prompt_name": name,
            "arguments": arguments if arguments is not None else {},
        },
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


# ---------------------------------------------------------------------------
# Patched Server._handle_request
# ---------------------------------------------------------------------------


async def _patched_handle_request(
    self: Any,  # noqa: ANN401
    message: Any,  # noqa: ANN401
    req: Any,  # noqa: ANN401
    session: Any,  # noqa: ANN401
    lifespan_context: Any,  # noqa: ANN401
    raise_exceptions: bool,
) -> None:
    """Wrapped _handle_request that intercepts call_tool, read_resource, get_prompt requests."""
    import mcp.types as types  # noqa: PLC0415

    plugin = _get_mcp_plugin()

    # Determine if this is a request type we intercept
    req_type = type(req)
    direction = "server"
    method: str | None = None
    key: str | None = None
    details: dict[str, Any] | None = None

    if req_type is types.CallToolRequest:
        method = "call_tool"
        tool_name = req.params.name
        arguments = dict(req.params.arguments) if req.params.arguments else {}
        key = tool_name
        details = {
            "direction": direction,
            "method": method,
            "tool_name": tool_name,
            "arguments": arguments,
        }
    elif req_type is types.ReadResourceRequest:
        method = "read_resource"
        uri_str = str(req.params.uri)
        key = uri_str
        details = {
            "direction": direction,
            "method": method,
            "uri": uri_str,
        }
    elif req_type is types.GetPromptRequest:
        method = "get_prompt"
        prompt_name = req.params.name
        arguments = dict(req.params.arguments) if req.params.arguments else {}
        key = prompt_name
        details = {
            "direction": direction,
            "method": method,
            "prompt_name": prompt_name,
            "arguments": arguments,
        }

    if method is not None and key is not None and details is not None:
        queue_key = f"server:{method}:{key}"
        source_id = f"mcp:{queue_key}"

        with plugin._registry_lock:
            queue = plugin._queues.get(queue_key)
            if not queue:
                hint = plugin.format_unmocked_hint(source_id, (), details)
                raise UnmockedInteractionError(
                    source_id=source_id,
                    args=(),
                    kwargs=details,
                    hint=hint,
                )
            config = queue.popleft()

        interaction = Interaction(
            source_id=source_id,
            sequence=0,
            details=details,
            plugin=plugin,
        )
        plugin.record(interaction)

        if config.raises is not None:
            raise config.raises

        # Respond with the mock return value
        await message.respond(config.returns)
        return

    # For non-intercepted request types, delegate to the original handler
    await McpPlugin._original_handle_request(
        self, message, req, session, lifespan_context, raise_exceptions,
    )


# ---------------------------------------------------------------------------
# McpPlugin
# ---------------------------------------------------------------------------


class McpPlugin(BasePlugin):
    """MCP interception plugin.

    Patches ClientSession.call_tool, ClientSession.read_resource,
    ClientSession.get_prompt and Server._handle_request at the class level.
    Uses reference counting so nested sandboxes work correctly.

    Each (direction, method, key) triple has its own FIFO deque of McpMockConfig objects.
    """

    # Class-level reference counting -- shared across all instances/verifiers.
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    # Saved originals, restored when count reaches 0.
    _original_call_tool: ClassVar[Any] = None
    _original_read_resource: ClassVar[Any] = None
    _original_get_prompt: ClassVar[Any] = None
    _original_handle_request: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[McpMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal: enqueue a mock config
    # ------------------------------------------------------------------

    def _enqueue(
        self,
        direction: str,
        method: str,
        key: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        config = McpMockConfig(
            direction=direction,
            method=method,
            key=key,
            returns=returns,
            raises=raises,
            required=required,
        )
        queue_key = f"{direction}:{method}:{key}"
        with self._registry_lock:
            if queue_key not in self._queues:
                self._queues[queue_key] = deque()
            self._queues[queue_key].append(config)

    # ------------------------------------------------------------------
    # Public API: register client mocks
    # ------------------------------------------------------------------

    def mock_call_tool(
        self,
        tool_name: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a client call_tool invocation."""
        self._enqueue(
            "client", "call_tool", tool_name,
            returns=returns, raises=raises, required=required,
        )

    def mock_read_resource(
        self,
        uri: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a client read_resource invocation."""
        self._enqueue(
            "client", "read_resource", uri,
            returns=returns, raises=raises, required=required,
        )

    def mock_get_prompt(
        self,
        prompt_name: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a client get_prompt invocation."""
        self._enqueue(
            "client", "get_prompt", prompt_name,
            returns=returns, raises=raises, required=required,
        )

    # ------------------------------------------------------------------
    # Public API: register server mocks
    # ------------------------------------------------------------------

    def mock_server_call_tool(
        self,
        tool_name: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a server call_tool handler invocation."""
        self._enqueue(
            "server", "call_tool", tool_name,
            returns=returns, raises=raises, required=required,
        )

    def mock_server_read_resource(
        self,
        uri: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a server read_resource handler invocation."""
        self._enqueue(
            "server", "read_resource", uri,
            returns=returns, raises=raises, required=required,
        )

    def mock_server_get_prompt(
        self,
        prompt_name: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a server get_prompt handler invocation."""
        self._enqueue(
            "server", "get_prompt", prompt_name,
            returns=returns, raises=raises, required=required,
        )

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted module-level patch installation."""
        if not _MCP_AVAILABLE:
            raise ImportError(
                "Install bigfoot[mcp] to use McpPlugin: pip install bigfoot[mcp]"
            )
        with McpPlugin._install_lock:
            if McpPlugin._install_count == 0:
                McpPlugin._original_call_tool = _ClientSession.call_tool
                McpPlugin._original_read_resource = _ClientSession.read_resource
                McpPlugin._original_get_prompt = _ClientSession.get_prompt
                McpPlugin._original_handle_request = _Server._handle_request

                _ClientSession.call_tool = _patched_call_tool  # type: ignore[method-assign]
                _ClientSession.read_resource = _patched_read_resource  # type: ignore[method-assign]
                _ClientSession.get_prompt = _patched_get_prompt  # type: ignore[method-assign]
                _Server._handle_request = _patched_handle_request  # type: ignore[method-assign]
            McpPlugin._install_count += 1

    def deactivate(self) -> None:
        with McpPlugin._install_lock:
            McpPlugin._install_count = max(0, McpPlugin._install_count - 1)
            if McpPlugin._install_count == 0:
                if McpPlugin._original_call_tool is not None:
                    _ClientSession.call_tool = McpPlugin._original_call_tool  # type: ignore[method-assign]
                    McpPlugin._original_call_tool = None
                if McpPlugin._original_read_resource is not None:
                    _ClientSession.read_resource = McpPlugin._original_read_resource  # type: ignore[method-assign]
                    McpPlugin._original_read_resource = None
                if McpPlugin._original_get_prompt is not None:
                    _ClientSession.get_prompt = McpPlugin._original_get_prompt  # type: ignore[method-assign]
                    McpPlugin._original_get_prompt = None
                if McpPlugin._original_handle_request is not None:
                    _Server._handle_request = McpPlugin._original_handle_request  # type: ignore[method-assign]
                    McpPlugin._original_handle_request = None

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

    def get_unused_mocks(self) -> list[McpMockConfig]:
        """Return all McpMockConfig with required=True still in any queue."""
        unused: list[McpMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        direction = interaction.details.get("direction", "?")
        method = interaction.details.get("method", "?")
        if method == "call_tool":
            tool_name = interaction.details.get("tool_name", "?")
            arguments = interaction.details.get("arguments", {})
            return f"[McpPlugin] {direction} call_tool({tool_name!r}, arguments={arguments!r})"
        if method == "read_resource":
            uri = interaction.details.get("uri", "?")
            return f"[McpPlugin] {direction} read_resource({uri!r})"
        if method == "get_prompt":
            prompt_name = interaction.details.get("prompt_name", "?")
            arguments = interaction.details.get("arguments", {})
            return f"[McpPlugin] {direction} get_prompt({prompt_name!r}, arguments={arguments!r})"
        return f"[McpPlugin] {direction} {method}"

    def format_mock_hint(self, interaction: Interaction) -> str:
        direction = interaction.details.get("direction", "?")
        method = interaction.details.get("method", "?")
        prefix = "bigfoot.mcp_mock"
        if direction == "server":
            if method == "call_tool":
                tool_name = interaction.details.get("tool_name", "?")
                return f"    {prefix}.mock_server_call_tool({tool_name!r}, returns=...)"
            if method == "read_resource":
                uri = interaction.details.get("uri", "?")
                return f"    {prefix}.mock_server_read_resource({uri!r}, returns=...)"
            if method == "get_prompt":
                prompt_name = interaction.details.get("prompt_name", "?")
                return f"    {prefix}.mock_server_get_prompt({prompt_name!r}, returns=...)"
        else:
            if method == "call_tool":
                tool_name = interaction.details.get("tool_name", "?")
                return f"    {prefix}.mock_call_tool({tool_name!r}, returns=...)"
            if method == "read_resource":
                uri = interaction.details.get("uri", "?")
                return f"    {prefix}.mock_read_resource({uri!r}, returns=...)"
            if method == "get_prompt":
                prompt_name = interaction.details.get("prompt_name", "?")
                return f"    {prefix}.mock_get_prompt({prompt_name!r}, returns=...)"
        return f"    {prefix}.mock_{method}(..., returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        # source_id is like "mcp:client:call_tool:my_tool"
        parts = source_id.split(":", 3)
        direction = parts[1] if len(parts) > 1 else "?"
        method = parts[2] if len(parts) > 2 else "?"
        key = parts[3] if len(parts) > 3 else "?"
        prefix = "bigfoot.mcp_mock"

        if direction == "server":
            mock_fn = f"mock_server_{method}"
        else:
            mock_fn = f"mock_{method}"

        return (
            f"mcp {direction} {method}({key!r}) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    {prefix}.{mock_fn}({key!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.mcp_mock"
        direction = interaction.details.get("direction", "?")
        method = interaction.details.get("method", "?")

        if method == "call_tool":
            tool_name = interaction.details.get("tool_name", "?")
            arguments = interaction.details.get("arguments", {})
            return (
                f"    {sm}.assert_call_tool(\n"
                f"        tool_name={tool_name!r},\n"
                f"        arguments={arguments!r},\n"
                f"        direction={direction!r},\n"
                f"    )"
            )
        if method == "read_resource":
            uri = interaction.details.get("uri", "?")
            return (
                f"    {sm}.assert_read_resource(\n"
                f"        uri={uri!r},\n"
                f"        direction={direction!r},\n"
                f"    )"
            )
        if method == "get_prompt":
            prompt_name = interaction.details.get("prompt_name", "?")
            arguments = interaction.details.get("arguments", {})
            return (
                f"    {sm}.assert_get_prompt(\n"
                f"        prompt_name={prompt_name!r},\n"
                f"        arguments={arguments!r},\n"
                f"        direction={direction!r},\n"
                f"    )"
            )
        return f"    # {sm}: unknown method={method!r}"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config: McpMockConfig = mock_config  # type: ignore[assignment]
        direction = getattr(config, "direction", "?")
        method = getattr(config, "method", "?")
        key = getattr(config, "key", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"mcp {direction} {method}({key!r}) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def assert_call_tool(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        direction: str = "client",
    ) -> None:
        """Assert the next call_tool interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"mcp:{direction}:call_tool:{tool_name}"
        sentinel = _McpSentinel(source_id)
        expected: dict[str, Any] = {
            "direction": direction,
            "method": "call_tool",
            "tool_name": tool_name,
            "arguments": arguments if arguments is not None else {},
        }
        _get_test_verifier_or_raise().assert_interaction(sentinel, **expected)

    def assert_read_resource(
        self,
        uri: str,
        *,
        direction: str = "client",
    ) -> None:
        """Assert the next read_resource interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"mcp:{direction}:read_resource:{uri}"
        sentinel = _McpSentinel(source_id)
        expected: dict[str, Any] = {
            "direction": direction,
            "method": "read_resource",
            "uri": uri,
        }
        _get_test_verifier_or_raise().assert_interaction(sentinel, **expected)

    def assert_get_prompt(
        self,
        prompt_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        direction: str = "client",
    ) -> None:
        """Assert the next get_prompt interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"mcp:{direction}:get_prompt:{prompt_name}"
        sentinel = _McpSentinel(source_id)
        expected: dict[str, Any] = {
            "direction": direction,
            "method": "get_prompt",
            "prompt_name": prompt_name,
            "arguments": arguments if arguments is not None else {},
        }
        _get_test_verifier_or_raise().assert_interaction(sentinel, **expected)
