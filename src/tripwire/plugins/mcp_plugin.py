"""McpPlugin: intercepts MCP ClientSession and Server handler calls with per-method FIFO queues."""

from __future__ import annotations

import threading
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

from tripwire._base_plugin import BasePlugin
from tripwire._context import GuardPassThrough, get_verifier_or_raise
from tripwire._errors import UnmockedInteractionError
from tripwire._firewall_request import McpFirewallRequest
from tripwire._timeline import Interaction

if TYPE_CHECKING:
    from tripwire._verifier import StrictVerifier

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


def _get_mcp_plugin(
    firewall_request: McpFirewallRequest | None = None,
) -> McpPlugin | None:
    verifier = get_verifier_or_raise("mcp:client:call_tool", firewall_request=firewall_request)
    for plugin in verifier._plugins:
        if isinstance(plugin, McpPlugin):
            return plugin
    return None


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
    _original = McpPlugin._original_call_tool
    assert _original is not None
    fw_request = McpFirewallRequest(tool_name=name, uri="")
    try:
        plugin = _get_mcp_plugin(firewall_request=fw_request)
    except GuardPassThrough:
        return await _original(self, name, arguments, *args, **kwargs)
    if plugin is None:
        return await _original(self, name, arguments, *args, **kwargs)
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

    details_ct: dict[str, Any] = {
        "direction": "client",
        "method": "call_tool",
        "tool_name": name,
        "arguments": arguments if arguments is not None else {},
    }
    if config.raises is not None:
        details_ct["raised"] = config.raises
    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details=details_ct,
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
    _original = McpPlugin._original_read_resource
    assert _original is not None
    fw_request = McpFirewallRequest(tool_name="", uri=str(uri))
    try:
        plugin = _get_mcp_plugin(firewall_request=fw_request)
    except GuardPassThrough:
        return await _original(self, uri, *args, **kwargs)
    if plugin is None:
        return await _original(self, uri, *args, **kwargs)
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

    details_rr: dict[str, Any] = {
        "direction": "client",
        "method": "read_resource",
        "uri": uri_str,
    }
    if config.raises is not None:
        details_rr["raised"] = config.raises
    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details=details_rr,
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
    _original = McpPlugin._original_get_prompt
    assert _original is not None
    fw_request = McpFirewallRequest(tool_name="", uri=name)
    try:
        plugin = _get_mcp_plugin(firewall_request=fw_request)
    except GuardPassThrough:
        return await _original(self, name, arguments, *args, **kwargs)
    if plugin is None:
        return await _original(self, name, arguments, *args, **kwargs)
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

    details_gp: dict[str, Any] = {
        "direction": "client",
        "method": "get_prompt",
        "prompt_name": name,
        "arguments": arguments if arguments is not None else {},
    }
    if config.raises is not None:
        details_gp["raised"] = config.raises
    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details=details_gp,
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

    _original = McpPlugin._original_handle_request
    assert _original is not None
    # Server-side: construct a generic firewall request (specific tool/uri not yet known)
    fw_request = McpFirewallRequest(tool_name="", uri="")
    try:
        plugin = _get_mcp_plugin(firewall_request=fw_request)
    except GuardPassThrough:
        await _original(
            self, message, req, session, lifespan_context, raise_exceptions,
        )
        return
    if plugin is None:
        await _original(
            self, message, req, session, lifespan_context, raise_exceptions,
        )
        return

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

        if config.raises is not None:
            details["raised"] = config.raises
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
    await _original(
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

    # Saved originals, restored when count reaches 0.
    _original_call_tool: ClassVar[Callable[..., Any] | None] = None
    _original_read_resource: ClassVar[Callable[..., Any] | None] = None
    _original_get_prompt: ClassVar[Callable[..., Any] | None] = None
    _original_handle_request: ClassVar[Callable[..., Any] | None] = None

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

    def install_patches(self) -> None:
        """Install MCP client/server patches."""
        if not _MCP_AVAILABLE:
            raise ImportError(
                "Install python-tripwire[mcp] to use McpPlugin: pip install python-tripwire[mcp]"
            )
        McpPlugin._original_call_tool = _ClientSession.call_tool
        McpPlugin._original_read_resource = _ClientSession.read_resource
        McpPlugin._original_get_prompt = _ClientSession.get_prompt
        McpPlugin._original_handle_request = _Server._handle_request

        setattr(_ClientSession, "call_tool", _patched_call_tool)
        setattr(_ClientSession, "read_resource", _patched_read_resource)
        setattr(_ClientSession, "get_prompt", _patched_get_prompt)
        setattr(_Server, "_handle_request", _patched_handle_request)

    def restore_patches(self) -> None:
        """Restore original MCP client/server functions."""
        if McpPlugin._original_call_tool is not None:
            setattr(_ClientSession, "call_tool", McpPlugin._original_call_tool)
            McpPlugin._original_call_tool = None
        if McpPlugin._original_read_resource is not None:
            setattr(_ClientSession, "read_resource", McpPlugin._original_read_resource)
            McpPlugin._original_read_resource = None
        if McpPlugin._original_get_prompt is not None:
            setattr(_ClientSession, "get_prompt", McpPlugin._original_get_prompt)
            McpPlugin._original_get_prompt = None
        if McpPlugin._original_handle_request is not None:
            setattr(_Server, "_handle_request", McpPlugin._original_handle_request)
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
        prefix = "tripwire.mcp"
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
        prefix = "tripwire.mcp"

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
        sm = "tripwire.mcp"
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
        config = cast(McpMockConfig, mock_config)
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

    _ABSENT: ClassVar[object] = object()

    def assert_call_tool(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        direction: str = "client",
        raised: Any = _ABSENT,  # noqa: ANN401
    ) -> None:
        """Assert the next call_tool interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"mcp:{direction}:call_tool:{tool_name}"
        sentinel = _McpSentinel(source_id)
        expected: dict[str, Any] = {
            "direction": direction,
            "method": "call_tool",
            "tool_name": tool_name,
            "arguments": arguments if arguments is not None else {},
        }
        if raised is not McpPlugin._ABSENT:
            expected["raised"] = raised
        _get_test_verifier_or_raise().assert_interaction(sentinel, **expected)

    def assert_read_resource(
        self,
        uri: str,
        *,
        direction: str = "client",
        raised: Any = _ABSENT,  # noqa: ANN401
    ) -> None:
        """Assert the next read_resource interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"mcp:{direction}:read_resource:{uri}"
        sentinel = _McpSentinel(source_id)
        expected: dict[str, Any] = {
            "direction": direction,
            "method": "read_resource",
            "uri": uri,
        }
        if raised is not McpPlugin._ABSENT:
            expected["raised"] = raised
        _get_test_verifier_or_raise().assert_interaction(sentinel, **expected)

    def assert_get_prompt(
        self,
        prompt_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        direction: str = "client",
        raised: Any = _ABSENT,  # noqa: ANN401
    ) -> None:
        """Assert the next get_prompt interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"mcp:{direction}:get_prompt:{prompt_name}"
        sentinel = _McpSentinel(source_id)
        expected: dict[str, Any] = {
            "direction": direction,
            "method": "get_prompt",
            "prompt_name": prompt_name,
            "arguments": arguments if arguments is not None else {},
        }
        if raised is not McpPlugin._ABSENT:
            expected["raised"] = raised
        _get_test_verifier_or_raise().assert_interaction(sentinel, **expected)
