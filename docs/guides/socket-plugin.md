# SocketPlugin Guide

`SocketPlugin` intercepts `socket.socket` at the class level, patching `connect`, `send`, `sendall`, `recv`, and `close`. It is included in core bigfoot -- no extra required.

## Setup

In pytest, access `SocketPlugin` through the `bigfoot.socket_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import bigfoot

def test_echo_client():
    (bigfoot.socket_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("sendall",  returns=None)
        .expect("recv",     returns=b"pong")
        .expect("close",    returns=None))

    with bigfoot:
        import socket
        sock = socket.socket()
        sock.connect(("127.0.0.1", 9999))
        sock.sendall(b"ping")
        data = sock.recv(1024)
        sock.close()

    assert data == b"pong"

    bigfoot.socket_mock.assert_connect(host="127.0.0.1", port=9999)
    bigfoot.socket_mock.assert_sendall(data=b"ping")
    bigfoot.socket_mock.assert_recv(size=1024, data=b"pong")
    bigfoot.socket_mock.assert_close()
```

For manual use outside pytest, construct `SocketPlugin` explicitly:

```python
from bigfoot import StrictVerifier
from bigfoot.plugins.socket_plugin import SocketPlugin

verifier = StrictVerifier()
sock = SocketPlugin(verifier)
```

Each verifier may have at most one `SocketPlugin`. A second `SocketPlugin(verifier)` raises `ValueError`.

## State machine

```
disconnected --connect--> connected --send/sendall/recv--> connected --close--> closed
```

`connect()` transitions from `disconnected` to `connected`. `send()`, `sendall()`, and `recv()` are self-loops on `connected`. `close()` transitions to `closed`.

## Scripting a session

Use `new_session()` to create a `SessionHandle` and chain `.expect()` calls:

```python
(bigfoot.socket_mock
    .new_session()
    .expect("connect",  returns=None)
    .expect("send",     returns=5)
    .expect("recv",     returns=b"reply")
    .expect("close",    returns=None))
```

### `expect()` parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | Step name: `"connect"`, `"send"`, `"sendall"`, `"recv"`, or `"close"` |
| `returns` | `Any` | required | Value returned by the step (see below) |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused step causes `UnusedMocksError` at teardown |

### Return values by step

| Step | `returns` type | Description |
|---|---|---|
| `connect` | `None` | No return value |
| `send` | `int` | Number of bytes sent |
| `sendall` | `None` | No return value (`sendall` returns `None` in the real API) |
| `recv` | `bytes` | Data received from the socket |
| `close` | `None` | No return value |

## Asserting interactions

Each step records an interaction on the timeline. Use the typed assertion helpers on `bigfoot.socket_mock`:

### `assert_connect(*, host, port)`

```python
bigfoot.socket_mock.assert_connect(host="127.0.0.1", port=8080)
```

### `assert_send(*, data)`

```python
bigfoot.socket_mock.assert_send(data=b"hello")
```

### `assert_sendall(*, data)`

```python
bigfoot.socket_mock.assert_sendall(data=b"hello")
```

### `assert_recv(*, size, data)`

Both `size` and `data` are required. `size` is the buffer size passed to `recv()`, and `data` is the bytes actually returned.

```python
bigfoot.socket_mock.assert_recv(size=1024, data=b"response")
```

### `assert_close()`

No fields are required.

```python
bigfoot.socket_mock.assert_close()
```

## Multiple connections

Sessions are consumed in registration order. The first `socket.connect()` pops the first queued session:

```python
def test_two_connections():
    (bigfoot.socket_mock
        .new_session()
        .expect("connect", returns=None)
        .expect("recv",    returns=b"first")
        .expect("close",   returns=None))

    (bigfoot.socket_mock
        .new_session()
        .expect("connect", returns=None)
        .expect("recv",    returns=b"second")
        .expect("close",   returns=None))

    with bigfoot:
        s1 = socket.socket()
        s2 = socket.socket()
        s1.connect(("127.0.0.1", 9001))
        s2.connect(("127.0.0.1", 9002))
        assert s1.recv(1024) == b"first"
        assert s2.recv(1024) == b"second"
        s1.close()
        s2.close()

    bigfoot.socket_mock.assert_connect(host="127.0.0.1", port=9001)
    bigfoot.socket_mock.assert_connect(host="127.0.0.1", port=9002)
    bigfoot.socket_mock.assert_recv(size=1024, data=b"first")
    bigfoot.socket_mock.assert_recv(size=1024, data=b"second")
    bigfoot.socket_mock.assert_close()
    bigfoot.socket_mock.assert_close()
```

## Full example

**Production code** (`examples/socket_example/app.py`):

```python
--8<-- "examples/socket_example/app.py"
```

**Test** (`examples/socket_example/test_app.py`):

```python
--8<-- "examples/socket_example/test_app.py"
```

## InvalidStateError

Calling a method from the wrong state raises `InvalidStateError` immediately. For example, calling `recv()` before `connect()`:

```
bigfoot.InvalidStateError: 'recv' called in state 'disconnected'; valid from: frozenset({'connected'})
```

Check the state machine diagram to ensure your session script matches the expected call order.
