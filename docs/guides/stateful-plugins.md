# Stateful Plugins

Most protocols are not a bag of independent calls. A database connection must be opened before queries can run. A socket must connect before it can send. SMTP must greet the server before submitting a message. The order matters, the state matters, and a test that does not enforce both can pass while the production code does something impossible.

tripwire's stateful plugins address this by modelling each protocol as an explicit state machine. Before your test runs, you write a session script: an ordered list of method calls you expect to happen, each paired with the value it should return. tripwire consumes that script step by step during the test and raises `InvalidStateError` immediately if a method is called from the wrong state.

This guide covers each stateful plugin with working examples derived from the test suite.

---

## How stateful plugins work

Every stateful plugin (except `RedisPlugin`, which is stateless) extends `StateMachinePlugin`. The core concepts are:

**States and transitions.** Each plugin defines a set of states and the methods that move between them. `SocketPlugin`, for example, starts in `disconnected`, moves to `connected` on `connect()`, and moves to `closed` on `close()`. Methods like `send` and `recv` keep the connection in `connected`.

**Sessions.** Before a test runs, you call `new_session()` to create a `SessionHandle` and chain `.expect()` calls on it to build the script. One session corresponds to one connection lifetime.

**FIFO binding.** Sessions are consumed in registration order. The first call to the connection entry point (e.g., `socket.connect()`) pops the first queued session and binds it to that connection object. If two connections are opened, they each get their own session in the order they were registered.

**Auto-assertion.** State machine interactions are marked as asserted the moment they are recorded. You do not call `tripwire.assert_interaction()` for stateful plugins. `verify_all()` still runs at teardown and will report any `required=True` steps that were configured but never consumed.

---

## SocketPlugin

`SocketPlugin` intercepts `socket.socket` at the class level, patching `connect`, `send`, `sendall`, `recv`, and `close`.

**State machine:**

```
disconnected --connect--> connected --send/sendall/recv--> connected --close--> closed
```

**Proxy:** `tripwire.socket`

### Quickstart

```python
import socket
import tripwire

def test_echo_client():
    (tripwire.socket
        .new_session()
        .expect("connect",  returns=None)
        .expect("sendall",  returns=None)
        .expect("recv",     returns=b"pong")
        .expect("close",    returns=None))

    with tripwire:
        sock = socket.socket()
        sock.connect(("127.0.0.1", 9999))
        sock.sendall(b"ping")
        data = sock.recv(1024)
        sock.close()

    assert data == b"pong"
    # verify_all() called automatically at teardown
```

No imports other than `tripwire` and `socket`. The proxy `tripwire.socket` auto-creates the plugin on the current test verifier the first time it is accessed.

### Scripting multiple connections

Sessions are consumed in registration order:

```python
def test_two_connections():
    (tripwire.socket
        .new_session()
        .expect("connect", returns=None)
        .expect("recv",    returns=b"first")
        .expect("close",   returns=None))

    (tripwire.socket
        .new_session()
        .expect("connect", returns=None)
        .expect("recv",    returns=b"second")
        .expect("close",   returns=None))

    with tripwire:
        s1 = socket.socket()
        s2 = socket.socket()
        s1.connect(("127.0.0.1", 9001))
        s2.connect(("127.0.0.1", 9002))
        assert s1.recv(1024) == b"first"
        assert s2.recv(1024) == b"second"
        s1.close()
        s2.close()
```

### InvalidStateError

Calling a method from the wrong state raises `InvalidStateError` immediately:

```python
def test_recv_before_connect():
    tripwire.socket.new_session()  # empty session

    with tripwire:
        sock = socket.socket()
        # Bind the session without connecting first by directly using _bind_connection:
        from tripwire.plugins.socket_plugin import SocketPlugin
        plugin = next(p for p in tripwire.current_verifier()._plugins if isinstance(p, SocketPlugin))
        handle = plugin._bind_connection(sock)
        # handle._state == "disconnected"

        with pytest.raises(tripwire.InvalidStateError) as exc_info:
            plugin._execute_step(handle, "recv", (1024,), {}, "socket:recv")

    exc = exc_info.value
    assert exc.method == "recv"
    assert exc.current_state == "disconnected"
    assert exc.valid_states == frozenset({"connected"})
```

In production scenarios `InvalidStateError` fires when the code under test calls socket methods in the wrong order.

---

## DatabasePlugin

`DatabasePlugin` intercepts `sqlite3.connect()` and returns a fake connection object that routes all operations through the session script. The fake connection supports `execute()`, `cursor()`, `commit()`, `rollback()`, and `close()`.

**State machine:**

```
connected --execute--> in_transaction --execute--> in_transaction
in_transaction --commit/rollback--> connected
connected/in_transaction --close--> closed
```

**Proxy:** `tripwire.db`

### Quickstart

```python
import sqlite3
import tripwire

def test_select_users():
    (tripwire.db
        .new_session()
        .expect("execute", returns=[[1, "Alice"], [2, "Bob"]])
        .expect("close",   returns=None))

    with tripwire:
        conn = sqlite3.connect(":memory:")
        cursor = conn.execute("SELECT id, name FROM users")
        rows = cursor.fetchall()
        conn.close()

    assert rows == [[1, "Alice"], [2, "Bob"]]
```

### Using a cursor

```python
def test_cursor_style():
    (tripwire.db
        .new_session()
        .expect("execute", returns=[["x"], ["y"]])
        .expect("close",   returns=None))

    with tripwire:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute("SELECT val FROM t")
        rows = cur.fetchall()
        conn.close()

    assert rows == [["x"], ["y"]]
```

The fake cursor also supports `fetchone()`, `fetchmany(size)`, and iteration.

### Commit and rollback

Each `execute()` moves the connection into `in_transaction`. `commit()` and `rollback()` both return it to `connected`. This means you can test multiple transaction boundaries in a single session:

```python
def test_commit_then_execute():
    (tripwire.db
        .new_session()
        .expect("execute",  returns=[])
        .expect("commit",   returns=None)
        .expect("execute",  returns=[])   # valid only after commit reset state to "connected"
        .expect("close",    returns=None))

    with tripwire:
        conn = sqlite3.connect(":memory:")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.execute("INSERT INTO t VALUES (2)")
        conn.close()
```

Calling `commit()` from `connected` (before any `execute()`) raises `InvalidStateError`:

```python
with tripwire:
    conn = sqlite3.connect(":memory:")
    with pytest.raises(tripwire.InvalidStateError) as exc_info:
        conn.commit()
    conn.close()

assert exc_info.value.current_state == "connected"
assert exc_info.value.valid_states == frozenset({"in_transaction"})
```

---

## AsyncWebSocketPlugin

`AsyncWebSocketPlugin` intercepts `websockets.connect` and returns an async context manager that drives the session script.

**Requires:** `pip install pytest-tripwire[websockets]`

**State machine:**

```
connecting --connect (on __aenter__)--> open --send/recv--> open --close--> closed
```

**Proxy:** `tripwire.async_websocket`

### Quickstart

```python
import websockets
import tripwire
import pytest

async def test_ws_echo():
    (tripwire.async_websocket
        .new_session()
        .expect("connect", returns=None)
        .expect("send",    returns=None)
        .expect("recv",    returns="pong")
        .expect("close",   returns=None))

    with tripwire:
        async with websockets.connect("ws://localhost:8765") as ws:
            await ws.send("ping")
            message = await ws.recv()
            await ws.close()

    assert message == "pong"
```

The `connect` step executes when the `async with` block is entered. The `close` step executes when the block exits (or when you call `ws.close()` explicitly, whichever happens first — the plugin skips the automatic close if the session is already released).

### Two concurrent connections

Sessions are popped at `websockets.connect()` call time, not at `__aenter__` time:

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
```

---

## SyncWebSocketPlugin

`SyncWebSocketPlugin` intercepts `websocket.create_connection` from the `websocket-client` library and returns a fake connection object.

**Requires:** `pip install pytest-tripwire[websocket-client]`

**State machine:**

```
connecting --connect--> open --send/recv--> open --close--> closed
```

**Proxy:** `tripwire.sync_websocket`

### Quickstart

```python
import websocket
import tripwire

def test_sync_ws():
    (tripwire.sync_websocket
        .new_session()
        .expect("connect", returns=None)
        .expect("send",    returns=None)
        .expect("recv",    returns="hello")
        .expect("close",   returns=None))

    with tripwire:
        ws = websocket.create_connection("ws://localhost:8765")
        ws.send("hi")
        message = ws.recv()
        ws.close()

    assert message == "hello"
```

The `connect` step executes immediately inside `create_connection()` (before the function returns). The returned `ws` object is then in the `open` state.

---

## PopenPlugin

`PopenPlugin` replaces `subprocess.Popen` with a fake class that routes process I/O and lifecycle methods through the session script.

**State machine:**

```
created --init (Popen() call)--> running --stdin.write/stdout.read/stderr.read--> running
running --communicate--> terminated
running --wait--> terminated (also releases the session)
```

**Proxy:** `tripwire.popen`

**Coexistence with SubprocessPlugin:** `SubprocessPlugin` patches `subprocess.run` and `shutil.which`. `PopenPlugin` patches `subprocess.Popen`. Both can be active in the same sandbox without interference.

### Quickstart: communicate()

The most common usage pattern. The `communicate` step returns a 3-tuple `(stdout_bytes, stderr_bytes, returncode)`:

```python
import subprocess
import tripwire

def test_run_command():
    (tripwire.popen
        .new_session()
        .expect("init",        returns=None)
        .expect("communicate", returns=(b"hello\n", b"", 0)))

    with tripwire:
        proc = subprocess.Popen(["echo", "hello"], stdout=subprocess.PIPE)
        stdout, stderr = proc.communicate()

    assert stdout == b"hello\n"
    assert stderr == b""
    assert proc.returncode == 0
```

### Non-zero exit code

```python
def test_failing_command():
    (tripwire.popen
        .new_session()
        .expect("init",        returns=None)
        .expect("communicate", returns=(b"", b"command not found", 127)))

    with tripwire:
        proc = subprocess.Popen(["bogus-cmd"])
        stdout, stderr = proc.communicate()

    assert proc.returncode == 127
    assert stderr == b"command not found"
```

### wait()

`wait()` returns the returncode directly and releases the session:

```python
def test_wait():
    (tripwire.popen
        .new_session()
        .expect("init", returns=None)
        .expect("wait", returns=0))

    with tripwire:
        proc = subprocess.Popen(["sleep", "1"])
        rc = proc.wait()

    assert rc == 0
    assert proc.returncode == 0
```

### Reading stdout/stderr streams manually

For code that reads `proc.stdout` and `proc.stderr` directly rather than using `communicate()`:

```python
def test_stream_read():
    (tripwire.popen
        .new_session()
        .expect("init",        returns=None)
        .expect("stdout.read", returns=b"output data"))

    with tripwire:
        proc = subprocess.Popen(["cmd"], stdout=subprocess.PIPE)
        data = proc.stdout.read()

    assert data == b"output data"
```

---

## SmtpPlugin

`SmtpPlugin` replaces `smtplib.SMTP` with a fake class that drives the session script. The `connect` step fires unconditionally during `smtplib.SMTP(host, port)` construction, matching the behaviour of the real `smtplib.SMTP`.

**State machine:**

```
disconnected --connect--> connected --ehlo/helo--> greeted
greeted --starttls--> greeted          (optional, self-loop)
greeted --login--> authenticated       (optional)
greeted/authenticated/sending --sendmail/send_message--> sending
sending/greeted/authenticated --quit--> closed
```

`starttls` and `login` are optional steps. Skip them in your session script for an unauthenticated flow.

**Proxy:** `tripwire.smtp`

### Full authenticated flow (ehlo + starttls + login + sendmail + quit)

```python
import smtplib
import tripwire

def test_send_authenticated_email():
    (tripwire.smtp
        .new_session()
        .expect("connect",  returns=None)
        .expect("ehlo",     returns=(250, b"OK"))
        .expect("starttls", returns=(220, b"Ready"))
        .expect("login",    returns=(235, b"Auth OK"))
        .expect("sendmail", returns={})
        .expect("quit",     returns=(221, b"Bye")))

    with tripwire:
        smtp = smtplib.SMTP("mail.example.com", 587)
        smtp.ehlo()
        smtp.starttls()
        smtp.login("user@example.com", "s3cret")
        smtp.sendmail(
            "from@example.com",
            ["to@example.com"],
            "Subject: hello\r\n\r\nhello",
        )
        smtp.quit()
```

### No-auth flow (ehlo + sendmail + quit)

```python
def test_send_unauthenticated_email():
    (tripwire.smtp
        .new_session()
        .expect("connect",  returns=None)
        .expect("ehlo",     returns=(250, b"OK"))
        .expect("sendmail", returns={})
        .expect("quit",     returns=(221, b"Bye")))

    with tripwire:
        smtp = smtplib.SMTP("mail.example.com", 25)
        smtp.ehlo()
        smtp.sendmail(
            "from@example.com",
            ["to@example.com"],
            "Subject: test\r\n\r\ntest",
        )
        smtp.quit()
```

The state machine validates that `sendmail` is called from `greeted` (after `ehlo` without login) or from `authenticated` (after login). Calling `sendmail` from `connected` (skipping `ehlo`) raises `InvalidStateError`.

---

## RedisPlugin

`RedisPlugin` intercepts `redis.Redis.execute_command` at the class level. Unlike the other stateful plugins, Redis commands carry no inherent ordering constraint — GET and SET do not depend on each other's state. `RedisPlugin` therefore extends `BasePlugin` directly and uses a per-command FIFO queue rather than a session handle.

**Requires:** `pip install pytest-tripwire[redis]`

**Proxy:** `tripwire.redis`

### Quickstart

```python
import redis
import tripwire

def test_cache_lookup():
    tripwire.redis.mock_command("GET", returns="cached_value")

    with tripwire:
        r = redis.Redis()
        value = r.execute_command("GET", "mykey")

    assert value == "cached_value"
```

### Multiple commands

Each command name has its own independent FIFO queue. Multiple `mock_command("GET", ...)` calls for different keys are consumed in registration order when `GET` is called:

```python
def test_get_set():
    tripwire.redis.mock_command("SET", returns=True)
    tripwire.redis.mock_command("GET", returns="first")
    tripwire.redis.mock_command("GET", returns="second")

    with tripwire:
        r = redis.Redis()
        r.execute_command("SET", "k", "v")
        v1 = r.execute_command("GET", "key1")
        v2 = r.execute_command("GET", "key2")

    assert v1 == "first"
    assert v2 == "second"
```

Command names are case-insensitive: `mock_command("get", ...)` matches `execute_command("GET", ...)`.

### Simulating errors

```python
def test_redis_error():
    import redis as redis_lib
    tripwire.redis.mock_command(
        "GET",
        returns=None,
        raises=redis_lib.exceptions.ResponseError("WRONGTYPE"),
    )

    with tripwire:
        r = redis.Redis()
        with pytest.raises(redis_lib.exceptions.ResponseError):
            r.execute_command("GET", "badkey")
```

---

## Common errors

### `InvalidStateError`

Raised when a method is called from a state it is not valid in. The error carries `source_id`, `method`, `current_state`, and `valid_states`.

```
tripwire.InvalidStateError: 'recv' called in state 'disconnected'; valid from: frozenset({'connected'})
```

**Fix:** Check the state machine diagram for the plugin. You likely have a missing step in your session script (e.g., no `connect` step before the first `recv`), or the code under test is calling methods out of order.

### `UnmockedInteractionError`

Raised when a connection entry point fires (e.g., `socket.connect()`, `sqlite3.connect()`, `subprocess.Popen()`) and no session is queued.

```
UnmockedInteractionError: source_id='socket:connect'
hint='socket.socket.connect(...) was called but no session was queued.
Register a session with:
    tripwire.socket.new_session().expect("connect", returns=...)'
```

**Fix:** Call `tripwire.socket.new_session()` (or the appropriate proxy) before entering the sandbox.

Also raised when the session script is exhausted but the code under test makes another call. In this case the hint shows the method that ran out of steps.

### `UnusedMocksError`

Raised at teardown when a `required=True` step was registered but never consumed. This means the code under test did not make all the calls you expected — typically a code path that exits early, skips a method, or closes the connection before all steps are consumed.

```
UnusedMocksError: 1 unused mock(s)
  socket.socket.recv(...) was mocked (required=True) but never called.
  Registered at:
    File "test_client.py", line 8, in test_send_receive
      .expect("recv", returns=b"pong")
```

**Fix:** Either the code under test is not reaching the expected call (investigate why), or the step is truly optional — mark it `required=False`:

```python
.expect("recv", returns=b"pong", required=False)
```

### Summary

| Error | When | Fix |
|---|---|---|
| `InvalidStateError` | Method called from wrong state | Add missing steps or fix call order in code under test |
| `UnmockedInteractionError` | Connection made without a queued session | Call `new_session()` before the sandbox |
| `UnmockedInteractionError` | Script exhausted mid-session | Add more `.expect()` calls |
| `UnusedMocksError` | Required step never consumed | Investigate early exit, or use `required=False` |
