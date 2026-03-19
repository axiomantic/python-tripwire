"""Unit tests for McpPlugin."""

from __future__ import annotations

import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.mcp_plugin import (
    _MCP_AVAILABLE,
    McpMockConfig,
    McpPlugin,
    _McpSentinel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, McpPlugin]:
    """Return (verifier, plugin) with McpPlugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    McpPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, McpPlugin):
            return v, p
    p = McpPlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    from mcp.client.session import ClientSession
    from mcp.server.lowlevel.server import Server

    with McpPlugin._install_lock:
        McpPlugin._install_count = 0
        if McpPlugin._original_call_tool is not None:
            ClientSession.call_tool = McpPlugin._original_call_tool
            McpPlugin._original_call_tool = None
        if McpPlugin._original_read_resource is not None:
            ClientSession.read_resource = McpPlugin._original_read_resource
            McpPlugin._original_read_resource = None
        if McpPlugin._original_get_prompt is not None:
            ClientSession.get_prompt = McpPlugin._original_get_prompt
            McpPlugin._original_get_prompt = None
        if McpPlugin._original_handle_request is not None:
            Server._handle_request = McpPlugin._original_handle_request
            McpPlugin._original_handle_request = None


@pytest.fixture(autouse=True)
def clean_plugin_counts():
    """Ensure plugin install count starts and ends at 0 for every test."""
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_mcp_available_flag() -> None:
    assert _MCP_AVAILABLE is True


def test_activate_raises_when_mcp_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import bigfoot.plugins.mcp_plugin as _mp

    v, p = _make_verifier_with_plugin()
    monkeypatch.setattr(_mp, "_MCP_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install bigfoot[mcp] to use McpPlugin: pip install bigfoot[mcp]"
    )


# ---------------------------------------------------------------------------
# McpMockConfig dataclass
# ---------------------------------------------------------------------------


def test_mcp_mock_config_fields() -> None:
    config = McpMockConfig(
        direction="client",
        method="call_tool",
        key="my_tool",
        returns={"result": "ok"},
        raises=ValueError("boom"),
        required=False,
    )
    assert config.direction == "client"
    assert config.method == "call_tool"
    assert config.key == "my_tool"
    assert config.returns == {"result": "ok"}
    assert isinstance(config.raises, ValueError)
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


def test_mcp_mock_config_defaults() -> None:
    config = McpMockConfig(direction="client", method="call_tool", key="tool", returns={})
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patches() -> None:
    from mcp.client.session import ClientSession

    original_call_tool = ClientSession.call_tool
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert ClientSession.call_tool is not original_call_tool
    p.deactivate()


def test_deactivate_restores_patches() -> None:
    from mcp.client.session import ClientSession

    original_call_tool = ClientSession.call_tool
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert ClientSession.call_tool is original_call_tool


def test_reference_counting_nested() -> None:
    from mcp.client.session import ClientSession

    original_call_tool = ClientSession.call_tool
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert McpPlugin._install_count == 2

    p.deactivate()
    assert McpPlugin._install_count == 1
    assert ClientSession.call_tool is not original_call_tool

    p.deactivate()
    assert McpPlugin._install_count == 0
    assert ClientSession.call_tool is original_call_tool


# ---------------------------------------------------------------------------
# Basic client call_tool mock + assert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_call_tool_mock_and_assert(bigfoot_verifier: StrictVerifier) -> None:
    """Client call_tool: mock registers, patched method returns mock, assert passes."""
    from mcp.client.session import ClientSession

    import bigfoot

    mock_result = {"content": [{"type": "text", "text": "hello"}]}
    bigfoot.mcp_mock.mock_call_tool("my_tool", returns=mock_result)

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        result = await ClientSession.call_tool(session, "my_tool", {"arg1": "val1"})

    assert result == mock_result
    bigfoot.mcp_mock.assert_call_tool(
        "my_tool",
        arguments={"arg1": "val1"},
        direction="client",
    )


# ---------------------------------------------------------------------------
# Basic client read_resource mock + assert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_read_resource_mock_and_assert(bigfoot_verifier: StrictVerifier) -> None:
    """Client read_resource: mock registers, patched method returns mock, assert passes."""
    from mcp.client.session import ClientSession

    import bigfoot

    mock_result = {"contents": [{"uri": "file:///data.txt", "text": "content"}]}
    bigfoot.mcp_mock.mock_read_resource("file:///data.txt", returns=mock_result)

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        result = await ClientSession.read_resource(session, "file:///data.txt")

    assert result == mock_result
    bigfoot.mcp_mock.assert_read_resource(
        "file:///data.txt",
        direction="client",
    )


# ---------------------------------------------------------------------------
# Basic client get_prompt mock + assert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_get_prompt_mock_and_assert(bigfoot_verifier: StrictVerifier) -> None:
    """Client get_prompt: mock registers, patched method returns mock, assert passes."""
    from mcp.client.session import ClientSession

    import bigfoot

    mock_result = {"messages": [{"role": "user", "content": "hello"}]}
    bigfoot.mcp_mock.mock_get_prompt("greeting", returns=mock_result)

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        result = await ClientSession.get_prompt(session, "greeting", {"name": "world"})

    assert result == mock_result
    bigfoot.mcp_mock.assert_get_prompt(
        "greeting",
        arguments={"name": "world"},
        direction="client",
    )


# ---------------------------------------------------------------------------
# FIFO ordering (multiple mocks consumed in order)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_call_tool_fifo_ordering(bigfoot_verifier: StrictVerifier) -> None:
    """Multiple mocks for the same tool are consumed in FIFO order."""
    from mcp.client.session import ClientSession

    import bigfoot

    bigfoot.mcp_mock.mock_call_tool("tool_a", returns={"seq": 1})
    bigfoot.mcp_mock.mock_call_tool("tool_a", returns={"seq": 2})

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        first = await ClientSession.call_tool(session, "tool_a", {"x": "1"})
        second = await ClientSession.call_tool(session, "tool_a", {"x": "2"})

    assert first == {"seq": 1}
    assert second == {"seq": 2}

    bigfoot.mcp_mock.assert_call_tool("tool_a", arguments={"x": "1"}, direction="client")
    bigfoot.mcp_mock.assert_call_tool("tool_a", arguments={"x": "2"}, direction="client")


# ---------------------------------------------------------------------------
# Unasserted interaction error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unasserted_interaction_recorded(bigfoot_verifier: StrictVerifier) -> None:
    """Interactions are NOT auto-asserted; they appear in all_unasserted()."""
    from mcp.client.session import ClientSession

    import bigfoot

    bigfoot.mcp_mock.mock_call_tool("my_tool", returns={"ok": True})

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        await ClientSession.call_tool(session, "my_tool", {"k": "v"})

    timeline = bigfoot_verifier._timeline
    unasserted = timeline.all_unasserted()
    assert len(unasserted) == 1
    assert unasserted[0].source_id == "mcp:client:call_tool:my_tool"

    # Clean up by asserting
    bigfoot.mcp_mock.assert_call_tool("my_tool", arguments={"k": "v"}, direction="client")


# ---------------------------------------------------------------------------
# Unmocked interaction error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unmocked_call_tool_raises(bigfoot_verifier: StrictVerifier) -> None:
    """Calling a tool with no mock raises UnmockedInteractionError."""
    from mcp.client.session import ClientSession

    import bigfoot

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        with pytest.raises(UnmockedInteractionError) as exc_info:
            await ClientSession.call_tool(session, "unknown_tool", {})

    assert exc_info.value.source_id == "mcp:client:call_tool:unknown_tool"


@pytest.mark.asyncio
async def test_unmocked_read_resource_raises(bigfoot_verifier: StrictVerifier) -> None:
    """Calling read_resource with no mock raises UnmockedInteractionError."""
    from mcp.client.session import ClientSession

    import bigfoot

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        with pytest.raises(UnmockedInteractionError) as exc_info:
            await ClientSession.read_resource(session, "file:///nope.txt")

    assert exc_info.value.source_id == "mcp:client:read_resource:file:///nope.txt"


@pytest.mark.asyncio
async def test_unmocked_get_prompt_raises(bigfoot_verifier: StrictVerifier) -> None:
    """Calling get_prompt with no mock raises UnmockedInteractionError."""
    from mcp.client.session import ClientSession

    import bigfoot

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        with pytest.raises(UnmockedInteractionError) as exc_info:
            await ClientSession.get_prompt(session, "missing_prompt")

    assert exc_info.value.source_id == "mcp:client:get_prompt:missing_prompt"


# ---------------------------------------------------------------------------
# Unused mock error
# ---------------------------------------------------------------------------


def test_unused_mocks_reported() -> None:
    """Mocks registered but never triggered appear in get_unused_mocks()."""
    v, p = _make_verifier_with_plugin()
    p.mock_call_tool("tool_a", returns={"a": 1})
    p.mock_read_resource("file:///x.txt", returns={"x": 1})

    unused = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].key == "tool_a"
    assert unused[1].key == "file:///x.txt"


def test_unused_mocks_excludes_required_false() -> None:
    """Mocks with required=False are excluded from unused mocks."""
    v, p = _make_verifier_with_plugin()
    p.mock_call_tool("tool_a", returns={}, required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# Mismatch error (assert wrong tool_name)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assert_wrong_tool_name_raises(bigfoot_verifier: StrictVerifier) -> None:
    """assert_call_tool with wrong tool_name raises InteractionMismatchError."""
    from mcp.client.session import ClientSession

    import bigfoot

    bigfoot.mcp_mock.mock_call_tool("real_tool", returns={"ok": True})

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        await ClientSession.call_tool(session, "real_tool", {"k": "v"})

    with pytest.raises(InteractionMismatchError):
        bigfoot.mcp_mock.assert_call_tool("wrong_tool", arguments={"k": "v"}, direction="client")

    # Clean up by asserting correctly
    bigfoot.mcp_mock.assert_call_tool("real_tool", arguments={"k": "v"}, direction="client")


# ---------------------------------------------------------------------------
# assertable_fields returns all detail keys
# ---------------------------------------------------------------------------


def test_assertable_fields_returns_all_detail_keys_call_tool() -> None:
    """assertable_fields returns frozenset of all keys in interaction.details for call_tool."""
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:call_tool:my_tool",
        sequence=0,
        details={
            "direction": "client",
            "method": "call_tool",
            "tool_name": "my_tool",
            "arguments": {"x": 1},
        },
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset(
        {"direction", "method", "tool_name", "arguments"}
    )


def test_assertable_fields_returns_all_detail_keys_read_resource() -> None:
    """assertable_fields returns frozenset of all keys in interaction.details for read_resource."""
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:read_resource:file:///x.txt",
        sequence=0,
        details={
            "direction": "client",
            "method": "read_resource",
            "uri": "file:///x.txt",
        },
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset(
        {"direction", "method", "uri"}
    )


def test_assertable_fields_returns_all_detail_keys_get_prompt() -> None:
    """assertable_fields returns frozenset of all keys in interaction.details for get_prompt."""
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:get_prompt:greeting",
        sequence=0,
        details={
            "direction": "client",
            "method": "get_prompt",
            "prompt_name": "greeting",
            "arguments": {},
        },
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset(
        {"direction", "method", "prompt_name", "arguments"}
    )


# ---------------------------------------------------------------------------
# Missing assertion fields raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_assertion_fields_raises(bigfoot_verifier: StrictVerifier) -> None:
    """Incomplete fields in assert_interaction raises MissingAssertionFieldsError."""
    from mcp.client.session import ClientSession

    import bigfoot

    bigfoot.mcp_mock.mock_call_tool("my_tool", returns={"ok": True})

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        await ClientSession.call_tool(session, "my_tool", {"k": "v"})

    sentinel = _McpSentinel("mcp:client:call_tool:my_tool")
    with pytest.raises(MissingAssertionFieldsError):
        bigfoot.assert_interaction(sentinel, direction="client")

    # Clean up by asserting correctly
    bigfoot.mcp_mock.assert_call_tool("my_tool", arguments={"k": "v"}, direction="client")


# ---------------------------------------------------------------------------
# Server-side mocking
# ---------------------------------------------------------------------------


def test_server_mock_enqueues_correctly() -> None:
    """Server mocks are enqueued under the 'server:' prefix."""
    v, p = _make_verifier_with_plugin()
    p.mock_server_call_tool("server_tool", returns={"result": "done"})

    with p._registry_lock:
        queue = p._queues.get("server:call_tool:server_tool")
        assert queue is not None
        assert len(queue) == 1
        assert queue[0].direction == "server"
        assert queue[0].method == "call_tool"
        assert queue[0].key == "server_tool"
        assert queue[0].returns == {"result": "done"}


def test_server_read_resource_mock_enqueues_correctly() -> None:
    """Server read_resource mocks are enqueued correctly."""
    v, p = _make_verifier_with_plugin()
    p.mock_server_read_resource("file:///data.txt", returns={"data": "ok"})

    with p._registry_lock:
        queue = p._queues.get("server:read_resource:file:///data.txt")
        assert queue is not None
        assert len(queue) == 1
        assert queue[0].direction == "server"
        assert queue[0].method == "read_resource"


def test_server_get_prompt_mock_enqueues_correctly() -> None:
    """Server get_prompt mocks are enqueued correctly."""
    v, p = _make_verifier_with_plugin()
    p.mock_server_get_prompt("my_prompt", returns={"messages": []})

    with p._registry_lock:
        queue = p._queues.get("server:get_prompt:my_prompt")
        assert queue is not None
        assert len(queue) == 1
        assert queue[0].direction == "server"
        assert queue[0].method == "get_prompt"


# ---------------------------------------------------------------------------
# Raises parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_call_tool_raises_exception(bigfoot_verifier: StrictVerifier) -> None:
    """Mock with raises parameter raises the exception instead of returning."""
    from mcp.client.session import ClientSession

    import bigfoot

    bigfoot.mcp_mock.mock_call_tool("failing_tool", returns=None, raises=RuntimeError("boom"))

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        with pytest.raises(RuntimeError, match="boom"):
            await ClientSession.call_tool(session, "failing_tool")

    bigfoot.mcp_mock.assert_call_tool("failing_tool", arguments={}, direction="client")


# ---------------------------------------------------------------------------
# matches() field comparison
# ---------------------------------------------------------------------------


def test_matches_field_comparison() -> None:
    """matches() does field-by-field comparison."""
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:call_tool:my_tool",
        sequence=0,
        details={
            "direction": "client",
            "method": "call_tool",
            "tool_name": "my_tool",
            "arguments": {"x": 1},
        },
        plugin=p,
    )
    assert p.matches(interaction, {}) is True
    assert p.matches(interaction, {"tool_name": "my_tool"}) is True
    assert p.matches(interaction, {"tool_name": "wrong"}) is False
    assert p.matches(interaction, {"nonexistent": "field"}) is False


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


def test_format_interaction_call_tool() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:call_tool:my_tool",
        sequence=0,
        details={
            "direction": "client",
            "method": "call_tool",
            "tool_name": "my_tool",
            "arguments": {"x": 1},
        },
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[McpPlugin] client call_tool('my_tool', arguments={'x': 1})"


def test_format_interaction_read_resource() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:read_resource:file:///x.txt",
        sequence=0,
        details={
            "direction": "client",
            "method": "read_resource",
            "uri": "file:///x.txt",
        },
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[McpPlugin] client read_resource('file:///x.txt')"


def test_format_interaction_get_prompt() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:get_prompt:greeting",
        sequence=0,
        details={
            "direction": "client",
            "method": "get_prompt",
            "prompt_name": "greeting",
            "arguments": {"name": "world"},
        },
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[McpPlugin] client get_prompt('greeting', arguments={'name': 'world'})"


def test_format_mock_hint_client_call_tool() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:call_tool:my_tool",
        sequence=0,
        details={
            "direction": "client",
            "method": "call_tool",
            "tool_name": "my_tool",
            "arguments": {},
        },
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.mcp_mock.mock_call_tool('my_tool', returns=...)"


def test_format_mock_hint_server_read_resource() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:server:read_resource:file:///x.txt",
        sequence=0,
        details={
            "direction": "server",
            "method": "read_resource",
            "uri": "file:///x.txt",
        },
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.mcp_mock.mock_server_read_resource('file:///x.txt', returns=...)"


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("mcp:client:call_tool:my_tool", (), {})
    assert result == (
        "mcp client call_tool('my_tool') was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    bigfoot.mcp_mock.mock_call_tool('my_tool', returns=...)"
    )


def test_format_unmocked_hint_server() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("mcp:server:call_tool:server_tool", (), {})
    assert result == (
        "mcp server call_tool('server_tool') was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    bigfoot.mcp_mock.mock_server_call_tool('server_tool', returns=...)"
    )


def test_format_assert_hint_call_tool() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:call_tool:my_tool",
        sequence=0,
        details={
            "direction": "client",
            "method": "call_tool",
            "tool_name": "my_tool",
            "arguments": {"x": 1},
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.mcp_mock.assert_call_tool(\n"
        "        tool_name='my_tool',\n"
        "        arguments={'x': 1},\n"
        "        direction='client',\n"
        "    )"
    )


def test_format_assert_hint_read_resource() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:read_resource:file:///x.txt",
        sequence=0,
        details={
            "direction": "client",
            "method": "read_resource",
            "uri": "file:///x.txt",
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.mcp_mock.assert_read_resource(\n"
        "        uri='file:///x.txt',\n"
        "        direction='client',\n"
        "    )"
    )


def test_format_assert_hint_get_prompt() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mcp:client:get_prompt:greeting",
        sequence=0,
        details={
            "direction": "client",
            "method": "get_prompt",
            "prompt_name": "greeting",
            "arguments": {"name": "world"},
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.mcp_mock.assert_get_prompt(\n"
        "        prompt_name='greeting',\n"
        "        arguments={'name': 'world'},\n"
        "        direction='client',\n"
        "    )"
    )


def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = McpMockConfig(
        direction="client", method="call_tool", key="my_tool", returns={}
    )
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "mcp client call_tool('my_tool') was mocked (required=True) but never called.\n"
        "Registered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# _McpSentinel
# ---------------------------------------------------------------------------


def test_sentinel_source_id() -> None:
    sentinel = _McpSentinel("mcp:client:call_tool:my_tool")
    assert sentinel.source_id == "mcp:client:call_tool:my_tool"


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.mcp_mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_mock_proxy_mock_call_tool(bigfoot_verifier: StrictVerifier) -> None:
    """Module-level proxy routes mock_call_tool correctly."""
    from mcp.client.session import ClientSession

    import bigfoot

    bigfoot.mcp_mock.mock_call_tool("proxy_tool", returns={"proxied": True})

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        result = await ClientSession.call_tool(session, "proxy_tool", {"a": "b"})

    assert result == {"proxied": True}
    bigfoot.mcp_mock.assert_call_tool(
        "proxy_tool", arguments={"a": "b"}, direction="client"
    )


def test_mcp_mock_proxy_raises_outside_context() -> None:
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.mcp_mock.mock_call_tool
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# McpPlugin in __all__
# ---------------------------------------------------------------------------


def test_mcp_plugin_in_all() -> None:
    import bigfoot
    from bigfoot.plugins.mcp_plugin import McpPlugin as _McpPlugin

    assert bigfoot.McpPlugin is _McpPlugin
    assert type(bigfoot.mcp_mock).__name__ == "_McpProxy"


# ---------------------------------------------------------------------------
# Null arguments default to empty dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_none_arguments_become_empty_dict(
    bigfoot_verifier: StrictVerifier,
) -> None:
    """When arguments is None, interaction details record an empty dict."""
    from mcp.client.session import ClientSession

    import bigfoot

    bigfoot.mcp_mock.mock_call_tool("tool_no_args", returns={"ok": True})

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        await ClientSession.call_tool(session, "tool_no_args")

    bigfoot.mcp_mock.assert_call_tool(
        "tool_no_args", arguments={}, direction="client"
    )


@pytest.mark.asyncio
async def test_get_prompt_none_arguments_become_empty_dict(
    bigfoot_verifier: StrictVerifier,
) -> None:
    """When arguments is None, interaction details record an empty dict."""
    from mcp.client.session import ClientSession

    import bigfoot

    bigfoot.mcp_mock.mock_get_prompt("my_prompt", returns={"messages": []})

    with bigfoot.sandbox():
        session = object.__new__(ClientSession)
        await ClientSession.get_prompt(session, "my_prompt")

    bigfoot.mcp_mock.assert_get_prompt(
        "my_prompt", arguments={}, direction="client"
    )
