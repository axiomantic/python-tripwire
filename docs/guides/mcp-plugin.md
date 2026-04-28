# McpPlugin Guide

`McpPlugin` intercepts `mcp.client.session.ClientSession` methods (`call_tool`, `read_resource`, `get_prompt`) and `mcp.server.lowlevel.server.Server._handle_request` at the class level. Each (direction, method, key) triple has its own independent FIFO queue, so you can mock multiple calls to different (or the same) MCP operations and they are consumed in registration order. The plugin supports both client-side and server-side interception.

## Installation

```bash
pip install python-tripwire[mcp]
```

This installs the `mcp` SDK.

## Setup

In pytest, access `McpPlugin` through the `tripwire.mcp` proxy. It auto-creates the plugin for the current test on first use:

```python
import pytest
import tripwire

@pytest.mark.asyncio
async def test_call_tool():
    from mcp.client.session import ClientSession

    tripwire.mcp.mock_call_tool(
        "my_tool",
        returns={"result": "ok"},
    )

    with tripwire:
        session = object.__new__(ClientSession)
        result = await session.call_tool("my_tool", {"key": "value"})

    assert result == {"result": "ok"}

    tripwire.mcp.assert_call_tool(
        "my_tool",
        arguments={"key": "value"},
        direction="client",
    )
```

For manual use outside pytest, construct `McpPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.mcp_plugin import McpPlugin

verifier = StrictVerifier()
mcp = McpPlugin(verifier)
```

Each verifier may have at most one `McpPlugin`. A second `McpPlugin(verifier)` raises `ValueError`.

## The direction field

Every MCP interaction is tagged with a `direction` -- either `"client"` or `"server"`:

- **`"client"`**: The code under test is an MCP client calling a remote server (via `ClientSession.call_tool`, `ClientSession.read_resource`, or `ClientSession.get_prompt`).
- **`"server"`**: The code under test is an MCP server receiving incoming requests (via `Server._handle_request`).

Client-side and server-side mocks use separate registration methods, and the `direction` parameter on assertion helpers lets you verify which side the interaction came from.

## Registering client mocks

Client mocks intercept `ClientSession` method calls. Three methods are available:

### `mock_call_tool(tool_name, *, returns, ...)`

```python
tripwire.mcp.mock_call_tool("get_weather", returns={"temp": "72F"})
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tool_name` | `str` | required | Name of the MCP tool to mock |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_read_resource(uri, *, returns, ...)`

```python
tripwire.mcp.mock_read_resource("file:///data.json", returns={"contents": "[1,2,3]"})
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `uri` | `str` | required | Resource URI to mock |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_get_prompt(prompt_name, *, returns, ...)`

```python
tripwire.mcp.mock_get_prompt("summarize", returns={"messages": [{"role": "user", "content": "..."}]})
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `prompt_name` | `str` | required | Name of the prompt to mock |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

## Registering server mocks

Server mocks intercept incoming requests handled by `Server._handle_request`. The API mirrors the client methods with a `mock_server_` prefix:

### `mock_server_call_tool(tool_name, *, returns, ...)`

```python
tripwire.mcp.mock_server_call_tool("calculate", returns={"result": 42})
```

### `mock_server_read_resource(uri, *, returns, ...)`

```python
tripwire.mcp.mock_server_read_resource("db://users/1", returns={"name": "Alice"})
```

### `mock_server_get_prompt(prompt_name, *, returns, ...)`

```python
tripwire.mcp.mock_server_get_prompt("greet", returns={"messages": [{"role": "assistant", "content": "Hello!"}]})
```

All three accept the same parameters as their client counterparts (`returns`, `raises`, `required`).

## FIFO queues

Each (direction, method, key) triple has its own independent FIFO queue. Multiple mocks for the same tool/resource/prompt are consumed in registration order:

```python
@pytest.mark.asyncio
async def test_multiple_tool_calls():
    tripwire.mcp.mock_call_tool("search", returns={"results": ["a"]})
    tripwire.mcp.mock_call_tool("search", returns={"results": ["b"]})

    with tripwire:
        from mcp.client.session import ClientSession
        session = object.__new__(ClientSession)
        r1 = await session.call_tool("search", {"query": "first"})
        r2 = await session.call_tool("search", {"query": "second"})

    assert r1 == {"results": ["a"]}
    assert r2 == {"results": ["b"]}

    tripwire.mcp.assert_call_tool("search", arguments={"query": "first"})
    tripwire.mcp.assert_call_tool("search", arguments={"query": "second"})
```

## Asserting interactions

Use the typed assertion helpers on `tripwire.mcp`. All recorded fields are required.

### `assert_call_tool(tool_name, *, arguments, direction)`

```python
tripwire.mcp.assert_call_tool(
    "get_weather",
    arguments={"city": "San Francisco"},
    direction="client",
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tool_name` | `str` | required | Name of the MCP tool |
| `arguments` | `dict[str, Any] \| None` | `None` | Arguments passed to the tool (defaults to `{}` if `None`) |
| `direction` | `str` | `"client"` | `"client"` or `"server"` |

### `assert_read_resource(uri, *, direction)`

```python
tripwire.mcp.assert_read_resource(
    "file:///data.json",
    direction="client",
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `uri` | `str` | required | Resource URI |
| `direction` | `str` | `"client"` | `"client"` or `"server"` |

### `assert_get_prompt(prompt_name, *, arguments, direction)`

```python
tripwire.mcp.assert_get_prompt(
    "summarize",
    arguments={"length": "short"},
    direction="client",
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `prompt_name` | `str` | required | Name of the prompt |
| `arguments` | `dict[str, Any] \| None` | `None` | Arguments passed to the prompt (defaults to `{}` if `None`) |
| `direction` | `str` | `"client"` | `"client"` or `"server"` |

## Simulating errors

Use the `raises` parameter to simulate MCP errors:

```python
@pytest.mark.asyncio
async def test_tool_error():
    tripwire.mcp.mock_call_tool(
        "flaky_tool",
        returns=None,
        raises=RuntimeError("MCP server unavailable"),
    )

    with tripwire:
        from mcp.client.session import ClientSession
        session = object.__new__(ClientSession)
        with pytest.raises(RuntimeError, match="MCP server unavailable"):
            await session.call_tool("flaky_tool", {"input": "data"})

    tripwire.mcp.assert_call_tool(
        "flaky_tool",
        arguments={"input": "data"},
        direction="client",
    )
```

## Full example

**Production code** (`examples/mcp_tool/app.py`):

```python
--8<-- "examples/mcp_tool/app.py"
```

**Test** (`examples/mcp_tool/test_app.py`):

```python
--8<-- "examples/mcp_tool/test_app.py"
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.mcp.mock_call_tool("analytics_ping", returns={"status": "ok"}, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code makes an MCP call that has no remaining mocks in its queue, tripwire raises `UnmockedInteractionError`:

```
mcp client call_tool('get_weather') was called but no mock was registered.
Register a mock with:
    tripwire.mcp.mock_call_tool('get_weather', returns=...)
```
