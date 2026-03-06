# bigfoot

[![CI](https://github.com/axiomantic/bigfoot/actions/workflows/ci.yml/badge.svg)](https://github.com/axiomantic/bigfoot/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

*Tests with big feet.*

Every external call your code makes — HTTP, subprocess, sockets, databases — gets intercepted, queued, and verified against exactly what you said would happen. Unexpected call? Instant failure. Unasserted interaction? Teardown failure. Registered mock that never fired? Teardown failure.

Every call accounted for. Every assertion mandatory. No exceptions.

## Installation

```bash
pip install bigfoot                       # Core: MockPlugin + SubprocessPlugin + DatabasePlugin + SmtpPlugin + SocketPlugin + PopenPlugin + AsyncSubprocessPlugin
pip install bigfoot[psycopg2]             # + Psycopg2Plugin
pip install bigfoot[asyncpg]              # + AsyncpgPlugin
pip install bigfoot[http]                 # + HttpPlugin (httpx, requests, urllib)
pip install bigfoot[aiohttp]              # + aiohttp support for HttpPlugin
pip install bigfoot[websockets]           # + AsyncWebSocketPlugin (websockets library)
pip install bigfoot[websocket-client]     # + SyncWebSocketPlugin (websocket-client library)
pip install bigfoot[redis]                # + RedisPlugin (redis-py)
pip install bigfoot[matchers]             # + dirty-equals matchers
pip install bigfoot[dev]                  # All of the above + pytest, mypy, ruff
```

## Quick Start

```python
import bigfoot
import httpx

def test_payment_flow():
    bigfoot.http.mock_response("POST", "https://api.stripe.com/v1/charges",
                               json={"id": "ch_123"}, status=200)

    with bigfoot:
        response = httpx.post("https://api.stripe.com/v1/charges",
                              json={"amount": 5000})

    bigfoot.http.assert_request(
        method="POST", url="https://api.stripe.com/v1/charges",
        headers=IsMapping(), body=None,
    ).assert_response(status=200, headers=IsMapping(), body=IsMapping() | IsInstance(str))
    assert response.json()["id"] == "ch_123"
    # verify_all() called automatically at test teardown
```

## Mock Plugin

```python
import bigfoot

def test_service_calls():
    payment = bigfoot.mock("PaymentService")
    payment.charge.returns({"status": "ok"})
    payment.refund.required(False).returns(None)  # optional mock

    with bigfoot:
        result = payment.charge(order_id=42)

    bigfoot.assert_interaction(payment.charge, args=(42,), kwargs={"order_id": 42})
```

### Side Effects

```python
proxy.compute.returns(42)                   # Return a value
proxy.compute.returns(1).returns(2)         # FIFO: first call returns 1, second returns 2
proxy.fetch.raises(IOError("unavailable"))  # Raise an exception
proxy.transform.calls(lambda x: x.upper()) # Delegate to a function
proxy.log.required(False).returns(None)     # Optional: no UnusedMocksError if never called
```

## SubprocessPlugin

`SubprocessPlugin` intercepts `subprocess.run` and `shutil.which` — included in core bigfoot, no extra required.

```python
import bigfoot

def test_deploy():
    bigfoot.subprocess_mock.mock_which("git", returns="/usr/bin/git")
    bigfoot.subprocess_mock.mock_run(["git", "pull", "--ff-only"], returncode=0, stdout="Already up to date.\n")
    bigfoot.subprocess_mock.mock_run(["git", "tag", "v1.0"], returncode=0)

    with bigfoot:
        deploy()

    bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="git", returns="/usr/bin/git")
    bigfoot.assert_interaction(bigfoot.subprocess_mock.run, command=["git", "pull", "--ff-only"],
                               returncode=0, stdout="Already up to date.\n", stderr="")
    bigfoot.assert_interaction(bigfoot.subprocess_mock.run, command=["git", "tag", "v1.0"],
                               returncode=0, stdout="", stderr="")
```

### `mock_run` options

| Parameter | Type | Default | Description |
|---|---|---|---|
| `command` | `list[str]` | required | Full command list, matched exactly in FIFO order |
| `returncode` | `int` | `0` | Return code of the completed process |
| `stdout` | `str` | `""` | Captured stdout |
| `stderr` | `str` | `""` | Captured stderr |
| `raises` | `BaseException \| None` | `None` | Exception to raise after recording the interaction |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_which` options

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Binary name to match (e.g., `"git"`, `"docker"`) |
| `returns` | `str \| None` | required | Path returned by `shutil.which`, or `None` to simulate not found |
| `required` | `bool` | `False` | Whether an uncalled mock causes `UnusedMocksError` at teardown |

`shutil.which` is semi-permissive: unregistered names return `None` silently. Only registered names record interactions.

### Activate bouncer without mocks

```python
def test_no_subprocess_calls():
    bigfoot.subprocess_mock.install()  # any subprocess.run call will raise UnmockedInteractionError

    with bigfoot:
        result = function_that_should_not_call_subprocess()

    assert result == expected
```

## LoggingPlugin

`LoggingPlugin` intercepts Python's `logging` module -- included in core bigfoot, no extra required. All log calls inside a sandbox are swallowed (not actually emitted) and recorded on the timeline, requiring explicit assertion at teardown.

```python
import bigfoot
import logging

def test_audit_trail():
    logger = logging.getLogger("myapp.auth")

    with bigfoot:
        logger.info("User alice logged in")
        logger.warning("Rate limit approaching")

    bigfoot.log_mock.assert_info("User alice logged in", "myapp.auth")
    bigfoot.log_mock.assert_warning("Rate limit approaching", "myapp.auth")
```

### `mock_log` options

| Parameter | Type | Default | Description |
|---|---|---|---|
| `level` | `str` | required | Log level name: `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"` |
| `message` | `str` | required | The formatted log message |
| `logger_name` | `str \| None` | `None` | Logger name to match; `None` matches any logger |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### Assertion helpers

| Method | Description |
|---|---|
| `assert_log(level, message, logger_name)` | Assert the next log interaction (all 3 fields) |
| `assert_debug(message, logger_name)` | Convenience for `assert_log("DEBUG", ...)` |
| `assert_info(message, logger_name)` | Convenience for `assert_log("INFO", ...)` |
| `assert_warning(message, logger_name)` | Convenience for `assert_log("WARNING", ...)` |
| `assert_error(message, logger_name)` | Convenience for `assert_log("ERROR", ...)` |
| `assert_critical(message, logger_name)` | Convenience for `assert_log("CRITICAL", ...)` |

## PopenPlugin

`PopenPlugin` intercepts `subprocess.Popen` — separate from `SubprocessPlugin` (which intercepts `subprocess.run`). Both can be active in the same test.

Sessions are scripted with `new_session().expect(...)` before the sandbox:

```python
import bigfoot
import subprocess

def test_streaming_build():
    bigfoot.popen_mock.new_session() \
        .expect("spawn", returns=None) \
        .expect("communicate", returns=(b"Build complete\n", b"", 0))

    with bigfoot:
        proc = subprocess.Popen(["make", "all"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate()

    bigfoot.popen_mock.assert_spawn(command=["make", "all"], stdin=None)
    bigfoot.popen_mock.assert_communicate(input=None)
```

### Session script steps

| Step | `returns` value | Description |
|---|---|---|
| `"spawn"` | `None` | Spawns the fake process |
| `"communicate"` | `(stdout: bytes, stderr: bytes, returncode: int)` | Waits for process and returns output |
| `"wait"` | `returncode: int` | Waits for process exit without consuming output |

### Assertion helpers

| Method | Fields asserted |
|---|---|
| `assert_spawn(*, command, stdin)` | `command` (list), `stdin` (bytes or None) |
| `assert_communicate(*, input)` | `input` (bytes or None) |
| `assert_wait()` | no fields |

## AsyncSubprocessPlugin

`AsyncSubprocessPlugin` intercepts `asyncio.create_subprocess_exec` and `asyncio.create_subprocess_shell` -- the async complement to `PopenPlugin`. Both can be active in the same test.

Sessions are scripted with `new_session().expect(...)` before the sandbox:

```python
import asyncio
import bigfoot

async def test_async_build():
    bigfoot.async_subprocess_mock.new_session() \
        .expect("spawn", returns=None) \
        .expect("communicate", returns=(b"Build complete\n", b"", 0))

    with bigfoot:
        proc = await asyncio.create_subprocess_exec("make", "all")
        stdout, stderr = await proc.communicate()

    bigfoot.async_subprocess_mock.assert_spawn(command=["make", "all"], stdin=None)
    bigfoot.async_subprocess_mock.assert_communicate(input=None)
```

Shell commands use `create_subprocess_shell` and record `command` as a `str` instead of a `list`:

```python
async def test_async_shell():
    bigfoot.async_subprocess_mock.new_session() \
        .expect("spawn", returns=None) \
        .expect("communicate", returns=(b"HELLO\n", b"", 0))

    with bigfoot:
        proc = await asyncio.create_subprocess_shell("echo hello | tr a-z A-Z")
        stdout, stderr = await proc.communicate()

    bigfoot.async_subprocess_mock.assert_spawn(
        command="echo hello | tr a-z A-Z", stdin=None
    )
    bigfoot.async_subprocess_mock.assert_communicate(input=None)
```

### Session script steps

| Step | `returns` value | Description |
|---|---|---|
| `"spawn"` | `None` | Spawns the fake process |
| `"communicate"` | `(stdout: bytes, stderr: bytes, returncode: int)` | Waits for process and returns output |
| `"wait"` | `returncode: int` | Waits for process exit without consuming output |

### Assertion helpers

| Method | Fields asserted |
|---|---|
| `assert_spawn(*, command, stdin)` | `command` (list for exec, str for shell), `stdin` (bytes or None) |
| `assert_communicate(*, input)` | `input` (bytes or None) |
| `assert_wait()` | no fields |

## DatabasePlugin

`DatabasePlugin` intercepts `sqlite3.connect` — included in core bigfoot, no extra required.

Sessions follow the state machine: disconnected → connected → in_transaction → connected/closed.

```python
import bigfoot
import sqlite3

def test_save_user():
    bigfoot.db_mock.new_session() \
        .expect("connect", returns=None) \
        .expect("execute", returns=[]) \
        .expect("commit", returns=None) \
        .expect("close", returns=None)

    with bigfoot:
        conn = sqlite3.connect("users.db")
        conn.execute("INSERT INTO users (name) VALUES (?)", ("alice",))
        conn.commit()
        conn.close()

    bigfoot.db_mock.assert_connect(database="users.db")
    bigfoot.db_mock.assert_execute(sql="INSERT INTO users (name) VALUES (?)", parameters=("alice",))
    bigfoot.db_mock.assert_commit()
    bigfoot.db_mock.assert_close()
```

`execute()` returns a cursor proxy. The `returns` value from `.expect("execute", returns=rows)` is the list of rows available via `fetchone()`, `fetchall()`, and `fetchmany()`.

### Session script steps

| Step | `returns` value | Description |
|---|---|---|
| `"connect"` | `None` | Opens the database connection |
| `"execute"` | `list[row]` | Executes SQL; rows available via cursor fetch methods |
| `"commit"` | `None` | Commits the current transaction |
| `"rollback"` | `None` | Rolls back the current transaction |
| `"close"` | `None` | Closes the connection |

### Assertion helpers

| Method | Fields asserted |
|---|---|
| `assert_connect(*, database)` | `database` (str) |
| `assert_execute(*, sql, parameters)` | `sql` (str), `parameters` (any) |
| `assert_commit()` | no fields |
| `assert_rollback()` | no fields |
| `assert_close()` | no fields |

## Psycopg2Plugin

`Psycopg2Plugin` intercepts `psycopg2.connect` — requires `pip install bigfoot[psycopg2]`.

Sessions follow the same state machine as DatabasePlugin: disconnected -> connected -> in_transaction -> connected/closed.

```python
import bigfoot
import psycopg2

def test_save_user():
    bigfoot.psycopg2_mock.new_session() \
        .expect("connect", returns=None) \
        .expect("execute", returns=[]) \
        .expect("commit", returns=None) \
        .expect("close", returns=None)

    with bigfoot:
        conn = psycopg2.connect(host="localhost", dbname="app", user="admin")
        cur = conn.cursor()
        cur.execute("INSERT INTO users (name) VALUES (%s)", ("alice",))
        conn.commit()
        conn.close()

    bigfoot.psycopg2_mock.assert_connect(host="localhost", dbname="app", user="admin")
    bigfoot.psycopg2_mock.assert_execute(sql="INSERT INTO users (name) VALUES (%s)", parameters=("alice",))
    bigfoot.psycopg2_mock.assert_commit()
    bigfoot.psycopg2_mock.assert_close()
```

### Assertion helpers

| Method | Fields asserted |
|---|---|
| `assert_connect(**kwargs)` | whichever of `dsn`, `host`, `port`, `dbname`, `user` were used |
| `assert_execute(*, sql, parameters)` | `sql` (str), `parameters` (any) |
| `assert_commit()` | no fields |
| `assert_rollback()` | no fields |
| `assert_close()` | no fields |

## AsyncpgPlugin

`AsyncpgPlugin` intercepts `asyncpg.connect` — requires `pip install bigfoot[asyncpg]`.

asyncpg connections have query methods directly on the connection (no cursors). All methods are async.

```python
import bigfoot
import asyncpg

async def test_fetch_users():
    bigfoot.asyncpg_mock.new_session() \
        .expect("connect", returns=None) \
        .expect("fetch", returns=[{"id": 1, "name": "Alice"}]) \
        .expect("close", returns=None)

    with bigfoot:
        conn = await asyncpg.connect(host="localhost", database="app", user="admin")
        rows = await conn.fetch("SELECT id, name FROM users")
        await conn.close()

    bigfoot.asyncpg_mock.assert_connect(host="localhost", database="app", user="admin")
    bigfoot.asyncpg_mock.assert_fetch(query="SELECT id, name FROM users", args=[])
    bigfoot.asyncpg_mock.assert_close()
```

### Session script steps

| Step | `returns` value | Description |
|---|---|---|
| `"connect"` | `None` | Opens the database connection |
| `"execute"` | `str` | Executes SQL; returns status string (e.g., `"INSERT 0 1"`) |
| `"fetch"` | `list[dict]` | Returns list of Record-like dicts |
| `"fetchrow"` | `dict \| None` | Returns single Record-like dict or None |
| `"fetchval"` | `Any` | Returns single scalar value |
| `"close"` | `None` | Closes the connection |

### Assertion helpers

| Method | Fields asserted |
|---|---|
| `assert_connect(**kwargs)` | whichever of `dsn`, `host`, `port`, `database`, `user` were used |
| `assert_execute(*, query, args)` | `query` (str), `args` (list) |
| `assert_fetch(*, query, args)` | `query` (str), `args` (list) |
| `assert_fetchrow(*, query, args)` | `query` (str), `args` (list) |
| `assert_fetchval(*, query, args)` | `query` (str), `args` (list) |
| `assert_close()` | no fields |

## SmtpPlugin

`SmtpPlugin` replaces `smtplib.SMTP` with a fake — included in core bigfoot, no extra required.

Sessions follow the state machine: disconnected → connected → greeted → (authenticated | sending) → closed. `starttls` is a self-loop on `greeted`.

```python
import bigfoot
import smtplib

def test_send_notification():
    bigfoot.smtp_mock.new_session() \
        .expect("connect", returns=(220, b"OK")) \
        .expect("ehlo", returns=(250, b"OK")) \
        .expect("login", returns=(235, b"Authentication successful")) \
        .expect("sendmail", returns={}) \
        .expect("quit", returns=(221, b"Bye"))

    with bigfoot:
        smtp = smtplib.SMTP("mail.example.com", 587)
        smtp.ehlo("myapp.example.com")
        smtp.login("user@example.com", "secret")
        smtp.sendmail("user@example.com", ["admin@example.com"], "Subject: Alert\n\nBody")
        smtp.quit()

    bigfoot.smtp_mock.assert_connect(host="mail.example.com", port=587)
    bigfoot.smtp_mock.assert_ehlo(name="myapp.example.com")
    bigfoot.smtp_mock.assert_login(user="user@example.com", password="secret")
    bigfoot.smtp_mock.assert_sendmail(
        from_addr="user@example.com",
        to_addrs=["admin@example.com"],
        msg="Subject: Alert\n\nBody",
    )
    bigfoot.smtp_mock.assert_quit()
```

### Session script steps

| Step | `returns` value | Description |
|---|---|---|
| `"connect"` | `(code: int, message: bytes)` | Establishes connection (called automatically by `smtplib.SMTP(host, port)`) |
| `"ehlo"` | `(code: int, message: bytes)` | EHLO greeting |
| `"helo"` | `(code: int, message: bytes)` | HELO greeting (alternative to ehlo) |
| `"starttls"` | `(code: int, message: bytes)` | Upgrades to TLS (self-loop on greeted) |
| `"login"` | `(code: int, message: bytes)` | Authenticates |
| `"sendmail"` | `dict` | Sends a raw message string |
| `"send_message"` | `dict` | Sends an `email.message.Message` object |
| `"quit"` | `(code: int, message: bytes)` | Closes the session |

### Assertion helpers

| Method | Fields asserted |
|---|---|
| `assert_connect(*, host, port)` | `host` (str), `port` (int) |
| `assert_ehlo(*, name)` | `name` (str) |
| `assert_helo(*, name)` | `name` (str) |
| `assert_starttls()` | no fields |
| `assert_login(*, user, password)` | `user` (str), `password` (str) |
| `assert_sendmail(*, from_addr, to_addrs, msg)` | `from_addr` (str), `to_addrs` (any), `msg` (any) |
| `assert_send_message(*, msg)` | `msg` (any) |
| `assert_quit()` | no fields |

## SocketPlugin

`SocketPlugin` intercepts `socket.socket.connect`, `send`, `sendall`, `recv`, and `close` — included in core bigfoot, no extra required.

Sessions follow the state machine: disconnected → connected → closed.

```python
import bigfoot
import socket

def test_tcp_client():
    bigfoot.socket_mock.new_session() \
        .expect("connect", returns=None) \
        .expect("sendall", returns=None) \
        .expect("recv", returns=b"HTTP/1.1 200 OK\r\n\r\n") \
        .expect("close", returns=None)

    with bigfoot:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("api.example.com", 80))
        sock.sendall(b"GET / HTTP/1.1\r\nHost: api.example.com\r\n\r\n")
        response = sock.recv(4096)
        sock.close()

    bigfoot.socket_mock.assert_connect(host="api.example.com", port=80)
    bigfoot.socket_mock.assert_sendall(data=b"GET / HTTP/1.1\r\nHost: api.example.com\r\n\r\n")
    bigfoot.socket_mock.assert_recv(size=4096, data=b"HTTP/1.1 200 OK\r\n\r\n")
    bigfoot.socket_mock.assert_close()
```

### Session script steps

| Step | `returns` value | Description |
|---|---|---|
| `"connect"` | `None` | Connects to `(host, port)` |
| `"send"` | `int` (bytes sent) | Sends data; returns byte count |
| `"sendall"` | `None` | Sends all data |
| `"recv"` | `bytes` | Receives data; `returns` is the data delivered to the caller |
| `"close"` | `None` | Closes the socket |

### Assertion helpers

| Method | Fields asserted |
|---|---|
| `assert_connect(*, host, port)` | `host` (str), `port` (int) |
| `assert_send(*, data)` | `data` (bytes) |
| `assert_sendall(*, data)` | `data` (bytes) |
| `assert_recv(*, size, data)` | `size` (int, the buffer size requested), `data` (bytes, the data returned) |
| `assert_close()` | no fields |

## RedisPlugin

`RedisPlugin` intercepts `redis.Redis` commands — requires `bigfoot[redis]`.

Each command has its own FIFO queue. There are no state transitions; commands are stateless.

```python
import bigfoot
import redis

def test_cache_lookup():
    bigfoot.redis_mock.mock_command("GET", returns=b"cached_value")
    bigfoot.redis_mock.mock_command("SET", returns=True)

    with bigfoot:
        client = redis.Redis()
        value = client.get("session:abc123")
        client.set("session:abc123", b"new_value")

    bigfoot.redis_mock.assert_command("GET", args=("session:abc123",))
    bigfoot.redis_mock.assert_command("SET", args=("session:abc123", b"new_value"))
```

### `mock_command` options

| Parameter | Type | Default | Description |
|---|---|---|---|
| `command` | `str` | required | Redis command name (case-insensitive, stored uppercase) |
| `returns` | `Any` | required | Value returned when the mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### Assertion helpers

`assert_command(command, args=(), kwargs=None)` — asserts the next Redis interaction. All three fields (`command`, `args`, `kwargs`) are required.

```python
bigfoot.redis_mock.assert_command("GET", args=("mykey",))
bigfoot.redis_mock.assert_command("SET", args=("mykey", b"value"), kwargs={})
```

## AsyncWebSocketPlugin

`AsyncWebSocketPlugin` intercepts `websockets.connect` (the `websockets` library, async) — requires `bigfoot[websockets]`.

Sessions follow the state machine: connecting → open → closed.

```python
import bigfoot
import websockets

async def test_price_feed():
    bigfoot.async_websocket_mock.new_session() \
        .expect("connect", returns=None) \
        .expect("recv", returns='{"price": 42000}') \
        .expect("send", returns=None) \
        .expect("close", returns=None)

    async with bigfoot:
        async with websockets.connect("wss://feed.example.com/prices") as ws:
            message = await ws.recv()
            await ws.send('{"action": "subscribe", "symbol": "BTC"}')

    bigfoot.async_websocket_mock.assert_connect(uri="wss://feed.example.com/prices")
    bigfoot.async_websocket_mock.assert_recv(message='{"price": 42000}')
    bigfoot.async_websocket_mock.assert_send(message='{"action": "subscribe", "symbol": "BTC"}')
    bigfoot.async_websocket_mock.assert_close()
```

The `close` step is automatically executed when the `async with websockets.connect(...)` block exits, unless you explicitly call `ws.close()` first.

### Session script steps

| Step | `returns` value | Description |
|---|---|---|
| `"connect"` | `None` | Establishes the WebSocket connection |
| `"send"` | `None` | Sends a message |
| `"recv"` | `str \| bytes` | Receives a message; `returns` is the data delivered to the caller |
| `"close"` | `None` | Closes the connection |

### Assertion helpers

| Method | Fields asserted |
|---|---|
| `assert_connect(*, uri)` | `uri` (str) |
| `assert_send(*, message)` | `message` (any) |
| `assert_recv(*, message)` | `message` (any) |
| `assert_close()` | no fields |

## SyncWebSocketPlugin

`SyncWebSocketPlugin` intercepts `websocket.create_connection` (the `websocket-client` library, synchronous) — requires `bigfoot[websocket-client]`.

Sessions follow the same state machine as `AsyncWebSocketPlugin`: connecting → open → closed.

```python
import bigfoot
import websocket

def test_sync_chat_client():
    bigfoot.sync_websocket_mock.new_session() \
        .expect("connect", returns=None) \
        .expect("send", returns=None) \
        .expect("recv", returns='{"status": "ok"}') \
        .expect("close", returns=None)

    with bigfoot:
        ws = websocket.create_connection("wss://chat.example.com/ws")
        ws.send('{"action": "ping"}')
        reply = ws.recv()
        ws.close()

    bigfoot.sync_websocket_mock.assert_connect(uri="wss://chat.example.com/ws")
    bigfoot.sync_websocket_mock.assert_send(message='{"action": "ping"}')
    bigfoot.sync_websocket_mock.assert_recv(message='{"status": "ok"}')
    bigfoot.sync_websocket_mock.assert_close()
```

### Session script steps

| Step | `returns` value | Description |
|---|---|---|
| `"connect"` | `None` | Establishes the WebSocket connection |
| `"send"` | `None` | Sends a message |
| `"recv"` | `str \| bytes` | Receives a message; `returns` is the data delivered to the caller |
| `"close"` | `None` | Closes the connection |

### Assertion helpers

| Method | Fields asserted |
|---|---|
| `assert_connect(*, uri)` | `uri` (str) |
| `assert_send(*, message)` | `message` (any) |
| `assert_recv(*, message)` | `message` (any) |
| `assert_close()` | no fields |

## Async Tests

`bigfoot` and `bigfoot.in_any_order()` both support `async with`:

```python
import bigfoot
import httpx

async def test_async_flow():
    bigfoot.http.mock_response("GET", "https://api.example.com/items", json=[])

    async with bigfoot:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.example.com/items")

    bigfoot.http.assert_request(method="GET", url="https://api.example.com/items",
                               headers=IsMapping(), body=None,
    ).assert_response(status=200, headers=IsMapping(), body="[]")
```

## Concurrent Assertions

When tests make concurrent HTTP requests (e.g., via `asyncio.TaskGroup`), use `in_any_order()` to relax the FIFO ordering requirement:

```python
import bigfoot
import asyncio, httpx

async def test_concurrent():
    bigfoot.http.mock_response("GET", "https://api.example.com/a", json={"a": 1})
    bigfoot.http.mock_response("GET", "https://api.example.com/b", json={"b": 2})

    async with bigfoot:
        async with asyncio.TaskGroup() as tg:
            ta = tg.create_task(httpx.AsyncClient().get("https://api.example.com/a"))
            tb = tg.create_task(httpx.AsyncClient().get("https://api.example.com/b"))

    with bigfoot.in_any_order():
        bigfoot.http.assert_request(method="GET", url="https://api.example.com/a",
                                    headers=IsMapping(), body=None,
        ).assert_response(status=200, headers=IsMapping(), body=IsMapping())
        bigfoot.http.assert_request(method="GET", url="https://api.example.com/b",
                                    headers=IsMapping(), body=None,
        ).assert_response(status=200, headers=IsMapping(), body=IsMapping())
```

`in_any_order()` operates globally across all plugin types (mock and HTTP).

## Spy / Pass-Through

### Spy: delegating to a real implementation

`bigfoot.spy(name, real)` creates a `MockProxy` that delegates to `real` when its call queue is empty. Queue entries take priority; the real object is called only when no mock entry remains. The interaction is recorded on the timeline regardless.

```python
import bigfoot

real_service = PaymentService()
payment = bigfoot.spy("PaymentService", real_service)
payment.charge.returns({"id": "mock-123"})  # queue entry: takes priority

with bigfoot:
    result1 = payment.charge(100)   # uses queue entry
    result2 = payment.charge(200)   # queue empty: delegates to real_service.charge(200)

bigfoot.assert_interaction(payment.charge, args=(100,), kwargs={})
bigfoot.assert_interaction(payment.charge, args=(200,), kwargs={})
```

`bigfoot.mock("PaymentService", wraps=real_service)` is the keyword-argument form and is equivalent.

### HTTP pass-through: real HTTP calls

`bigfoot.http.pass_through(method, url)` registers a permanent routing rule. When a request matches the rule and no mock matches first, the real HTTP call is made through the original transport. The interaction is still recorded on the timeline and must be asserted.

```python
import bigfoot, httpx

def test_mixed():
    bigfoot.http.mock_response("GET", "https://api.example.com/cached", json={"data": "cached"})
    bigfoot.http.pass_through("GET", "https://api.example.com/live")

    with bigfoot:
        mocked = httpx.get("https://api.example.com/cached")   # returns mock
        real   = httpx.get("https://api.example.com/live")     # makes real HTTP call

    bigfoot.http.assert_request(method="GET", url="https://api.example.com/cached",
                               headers=IsMapping(), body=None,
    ).assert_response(status=200, headers=IsMapping(), body=IsMapping() | IsInstance(str))
    bigfoot.http.assert_request(method="GET", url="https://api.example.com/live",
                               headers=IsMapping(), body=None,
    ).assert_response(status=200, headers=IsMapping(), body=IsMapping() | IsInstance(str))
```

Pass-through rules are routing hints, not assertions. Unused pass-through rules do not raise `UnusedMocksError`.

## pytest Integration

No fixture injection required. Install bigfoot and `import bigfoot` in any test:

```python
import bigfoot

def test_something():
    svc = bigfoot.mock("MyService")
    svc.call.returns("ok")

    with bigfoot:
        result = svc.call()

    bigfoot.assert_interaction(svc.call)
    # verify_all() runs at teardown automatically
```

`with bigfoot:` is shorthand for `with bigfoot.sandbox():`. Both return the active verifier, so `with bigfoot as v:` works if you need the verifier instance directly.

An explicit `bigfoot_verifier` fixture is available as an escape hatch when you need direct access to the `StrictVerifier` object.

## HTTP Interception Scope

`HttpPlugin` intercepts at the transport/adapter level:

- `httpx.Client` and `httpx.AsyncClient` (class-level transport patch)
- `requests.get()`, `requests.Session`, etc. (class-level adapter patch)
- `urllib.request.urlopen()` (via `install_opener`)
- `asyncio.BaseEventLoop.run_in_executor` (propagates context to thread pool executors)

Not intercepted: `httpx.ASGITransport`, `httpx.WSGITransport`.

### aiohttp support

When `bigfoot[aiohttp]` is installed, `HttpPlugin` also intercepts `aiohttp.ClientSession` requests. The same `mock_response()`, `assert_request()`, and `assert_response()` APIs work identically:

```python
import bigfoot
import aiohttp

async def test_aiohttp():
    bigfoot.http.mock_response("GET", "https://api.example.com/data", json={"value": 42})

    async with bigfoot:
        async with aiohttp.ClientSession() as session:
            response = await session.get("https://api.example.com/data")
            assert response.status == 200
            body = await response.json()
            assert body == {"value": 42}

    bigfoot.http.assert_request("GET", "https://api.example.com/data",
                                headers={}, body="",
                                require_response=True) \
        .assert_response(200, {"content-type": "application/json"}, '{"value": 42}')
```

aiohttp is optional. If not installed, `HttpPlugin` works normally for httpx, requests, and urllib.

## HTTP Plugin: assert_request and require_response

By default, `assert_request()` is terminal: it asserts four request fields (`method`, `url`, `request_headers`, `request_body`) and returns `None`.

When `require_response=True` (per-call or via project config), `assert_request()` instead returns an `HttpAssertionBuilder`. You must chain `.assert_response()` to complete the assertion with all seven fields.

```python
# Default: assert request only (4 fields)
bigfoot.http.assert_request(
    method="POST",
    url="https://api.example.com/orders",
    headers=IsMapping(),
    body=IsInstance(str),
)

# With require_response=True: assert request + response (7 fields)
bigfoot.http.assert_request(
    method="POST",
    url="https://api.example.com/orders",
    headers=IsMapping(),
    body=IsInstance(str),
    require_response=True,
).assert_response(
    status=201,
    headers=IsMapping(),
    body=IsMapping() | IsInstance(str),
)
```

`assert_response(status, headers, body)` — all three arguments are positional-or-keyword and required.

## Error Messages

bigfoot errors include copy-pasteable remediation hints:

```
UnmockedInteractionError: source_id='mock:PaymentService.charge', args=('order_42',), kwargs={},
hint='Unexpected call to PaymentService.charge

  Called with: args=('order_42',), kwargs={}

  To mock this interaction, add before your sandbox:
    bigfoot.mock("PaymentService").charge.returns(<value>)

  Or to mark it optional:
    bigfoot.mock("PaymentService").charge.required(False).returns(<value>)'
```

## Configuration

bigfoot reads `[tool.bigfoot]` from the nearest `pyproject.toml` (searching up from the working directory at test-session start). Configuration sets project-level defaults; per-call arguments override them.

### HTTP plugin

```toml
[tool.bigfoot.http]
require_response = true  # Require .assert_response() on every HTTP assertion
```

When `require_response = true`, every call to `http.assert_request()` returns an `HttpAssertionBuilder`. You must chain `.assert_response()` to complete the assertion with all seven fields (method, url, request\_headers, request\_body, status, response\_headers, response\_body). This enforces that tests verify both the outgoing request and the incoming response.

The per-call `require_response` argument to `assert_request()` overrides the project-level setting for a single assertion.

Config discovery walks up from the current working directory until it finds a `pyproject.toml`. A malformed `pyproject.toml` raises `tomllib.TOMLDecodeError`. Unknown keys inside `[tool.bigfoot]` are silently ignored for forward-compatibility.

**Future config candidates** (not yet implemented): `[tool.bigfoot.subprocess] which_strict_mode`, `[tool.bigfoot.redis] command_required_default`.

## Public API

```python
import bigfoot

# Module-level (preferred in pytest)
bigfoot.mock("Name")                    # create/retrieve a named MockProxy
bigfoot.mock("Name", wraps=real)        # spy: delegate to real when queue empty
bigfoot.spy("Name", real)              # positional form of wraps=
bigfoot                                 # preferred sandbox shorthand: `with bigfoot:` or `async with bigfoot:`
bigfoot.sandbox()                       # explicit form; equivalent to `with bigfoot:`
bigfoot.assert_interaction(source, **fields)  # assert next interaction; ALL assertable fields required
bigfoot.in_any_order()                  # relax FIFO ordering for assertions
bigfoot.verify_all()                    # explicit verification (automatic in pytest)
bigfoot.current_verifier()              # access the StrictVerifier directly
bigfoot.http                            # proxy to the HttpPlugin for this test
bigfoot.subprocess_mock                 # proxy to the SubprocessPlugin for this test
bigfoot.popen_mock                      # proxy to the PopenPlugin for this test
bigfoot.smtp_mock                       # proxy to the SmtpPlugin for this test
bigfoot.socket_mock                     # proxy to the SocketPlugin for this test
bigfoot.db_mock                         # proxy to the DatabasePlugin for this test
bigfoot.psycopg2_mock                   # proxy to the Psycopg2Plugin for this test
bigfoot.asyncpg_mock                    # proxy to the AsyncpgPlugin for this test
bigfoot.async_websocket_mock            # proxy to the AsyncWebSocketPlugin for this test
bigfoot.sync_websocket_mock             # proxy to the SyncWebSocketPlugin for this test
bigfoot.redis_mock                      # proxy to the RedisPlugin for this test
bigfoot.async_subprocess_mock           # proxy to the AsyncSubprocessPlugin for this test

# Classes (for manual use or custom plugins)
from bigfoot import (
    StrictVerifier,
    SandboxContext,
    InAnyOrderContext,
    MockPlugin,
    AsyncSubprocessPlugin,
    DatabasePlugin,
    Psycopg2Plugin,
    AsyncpgPlugin,
    PopenPlugin,
    SmtpPlugin,
    SocketPlugin,
    AsyncWebSocketPlugin,
    SyncWebSocketPlugin,
    RedisPlugin,
    BigfootError,
    AssertionInsideSandboxError,
    AutoAssertError,
    InvalidStateError,
    NoActiveVerifierError,
    UnmockedInteractionError,
    UnassertedInteractionsError,
    UnusedMocksError,
    VerificationError,
    InteractionMismatchError,
    MissingAssertionFieldsError,
    SandboxNotActiveError,
    ConflictError,
)
from bigfoot.plugins.http import HttpPlugin  # requires bigfoot[http]
from bigfoot.plugins.subprocess import SubprocessPlugin
```

### Error classes

| Class | When raised |
|---|---|
| `UnmockedInteractionError` | An intercepted call fired with no matching registered mock |
| `UnassertedInteractionsError` | Teardown: timeline has interactions not matched by `assert_interaction()` |
| `UnusedMocksError` | Teardown: required mocks were registered but never triggered |
| `VerificationError` | Teardown: both `UnassertedInteractionsError` and `UnusedMocksError` apply |
| `InteractionMismatchError` | `assert_interaction()` expected fields do not match the next interaction |
| `MissingAssertionFieldsError` | `assert_interaction()` caller omitted one or more assertable fields |
| `AssertionInsideSandboxError` | `assert_interaction()` called while the sandbox is still active |
| `SandboxNotActiveError` | An intercepted call fired while no sandbox is active |
| `NoActiveVerifierError` | Module-level bigfoot function called outside a test context |
| `ConflictError` | Another library already patched the target at `activate()` time |
| `AutoAssertError` | Plugin called `mark_asserted()` during `record()` (auto-assert anti-pattern) |
| `InvalidStateError` | State-machine method called from an invalid state |

## Requirements

- Python 3.11+
- pytest (for automatic per-test verifier and `verify_all()` at teardown)

## License

MIT
