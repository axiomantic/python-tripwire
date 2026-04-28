# WebSocket Plugins Guide

tripwire provides two WebSocket plugins covering both major Python WebSocket libraries:

- **AsyncWebSocketPlugin** intercepts `websockets.connect` (the `websockets` library for async usage)
- **SyncWebSocketPlugin** intercepts `websocket.create_connection` (the `websocket-client` library for sync usage)

Both use the same state machine and assertion pattern.

## Installation

=== "Async (websockets)"

    ```bash
    pip install python-tripwire[websockets]
    ```

=== "Sync (websocket-client)"

    ```bash
    pip install python-tripwire[websocket-client]
    ```

## State machine

Both plugins share the same state machine:

```
connecting --connect--> open --send/recv--> open --close--> closed
```

The `connect` step fires during `websockets.connect().__aenter__()` (async) or `websocket.create_connection()` (sync). `send()` and `recv()` are self-loops on `open`. `close()` transitions to `closed`.

---

## AsyncWebSocketPlugin

**Proxy:** `tripwire.async_websocket`

### Setup

```python
import tripwire

async def test_ws_echo():
    (tripwire.async_websocket
        .new_session()
        .expect("connect", returns=None)
        .expect("send",    returns=None)
        .expect("recv",    returns="pong")
        .expect("close",   returns=None))

    with tripwire:
        import websockets
        async with websockets.connect("ws://localhost:8765") as ws:
            await ws.send("ping")
            message = await ws.recv()
            await ws.close()

    assert message == "pong"

    tripwire.async_websocket.assert_connect(uri="ws://localhost:8765")
    tripwire.async_websocket.assert_send(message="ping")
    tripwire.async_websocket.assert_recv(message="pong")
    tripwire.async_websocket.assert_close()
```

For manual use outside pytest:

```python
from tripwire import StrictVerifier
from tripwire.plugins.websocket_plugin import AsyncWebSocketPlugin

verifier = StrictVerifier()
ws = AsyncWebSocketPlugin(verifier)
```

### Context manager behavior

`websockets.connect()` returns an async context manager. The session is popped from the queue at `websockets.connect()` call time (not at `__aenter__` time). The `connect` step executes when the `async with` block is entered.

If you call `ws.close()` explicitly inside the `async with` block, the automatic close on `__aexit__` is skipped (the plugin detects that the session is already released).

### Two concurrent connections

Sessions are consumed in registration order:

```python
async def test_two_ws_connections():
    (tripwire.async_websocket
        .new_session()
        .expect("connect", returns=None)
        .expect("recv",    returns="first")
        .expect("close",   returns=None))

    (tripwire.async_websocket
        .new_session()
        .expect("connect", returns=None)
        .expect("recv",    returns="second")
        .expect("close",   returns=None))

    with tripwire:
        cm1 = websockets.connect("ws://localhost:8765")
        cm2 = websockets.connect("ws://localhost:8765")
        async with cm1 as ws1:
            async with cm2 as ws2:
                assert await ws1.recv() == "first"
                assert await ws2.recv() == "second"

    tripwire.async_websocket.assert_connect(uri="ws://localhost:8765")
    tripwire.async_websocket.assert_connect(uri="ws://localhost:8765")
    tripwire.async_websocket.assert_recv(message="first")
    tripwire.async_websocket.assert_recv(message="second")
    tripwire.async_websocket.assert_close()
    tripwire.async_websocket.assert_close()
```

### Assertion helpers

#### `assert_connect(*, uri)`

```python
tripwire.async_websocket.assert_connect(uri="ws://localhost:8765")
```

#### `assert_send(*, message)`

```python
tripwire.async_websocket.assert_send(message="hello")
```

#### `assert_recv(*, message)`

```python
tripwire.async_websocket.assert_recv(message="world")
```

#### `assert_close()`

No fields are required.

```python
tripwire.async_websocket.assert_close()
```

---

## SyncWebSocketPlugin

**Proxy:** `tripwire.sync_websocket`

### Setup

```python
import tripwire

def test_sync_ws():
    (tripwire.sync_websocket
        .new_session()
        .expect("connect", returns=None)
        .expect("send",    returns=None)
        .expect("recv",    returns="hello")
        .expect("close",   returns=None))

    with tripwire:
        import websocket
        ws = websocket.create_connection("ws://localhost:8765")
        ws.send("hi")
        message = ws.recv()
        ws.close()

    assert message == "hello"

    tripwire.sync_websocket.assert_connect(uri="ws://localhost:8765")
    tripwire.sync_websocket.assert_send(message="hi")
    tripwire.sync_websocket.assert_recv(message="hello")
    tripwire.sync_websocket.assert_close()
```

For manual use outside pytest:

```python
from tripwire import StrictVerifier
from tripwire.plugins.websocket_plugin import SyncWebSocketPlugin

verifier = StrictVerifier()
ws = SyncWebSocketPlugin(verifier)
```

### Behavior

The `connect` step executes immediately inside `create_connection()` before the function returns. The returned `ws` object is then in the `open` state, ready for `send()` and `recv()` calls.

### Assertion helpers

#### `assert_connect(*, uri)`

```python
tripwire.sync_websocket.assert_connect(uri="ws://localhost:8765")
```

#### `assert_send(*, message)`

```python
tripwire.sync_websocket.assert_send(message="hello")
```

#### `assert_recv(*, message)`

```python
tripwire.sync_websocket.assert_recv(message="world")
```

#### `assert_close()`

No fields are required.

```python
tripwire.sync_websocket.assert_close()
```

---

## Scripting sessions (both plugins)

Both plugins use `new_session()` and `.expect()` with the same parameters:

### `expect()` parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | Step name: `"connect"`, `"send"`, `"recv"`, or `"close"` |
| `returns` | `Any` | required | Value returned by the step (see below) |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused step causes `UnusedMocksError` at teardown |

### Return values by step

| Step | `returns` type | Description |
|---|---|---|
| `connect` | `None` | Connection is established |
| `send` | `None` | No return value |
| `recv` | `str \| bytes` | Data received from the WebSocket |
| `close` | `None` | No return value |

## Full example

**Production code** (`examples/websocket_example/app.py`):

```python
--8<-- "examples/websocket_example/app.py"
```

**Test** (`examples/websocket_example/test_app.py`):

```python
--8<-- "examples/websocket_example/test_app.py"
```
