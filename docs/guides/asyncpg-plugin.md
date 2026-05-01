# AsyncpgPlugin Guide

`AsyncpgPlugin` intercepts `asyncpg.connect()` and returns a fake async connection object that routes all operations through a session script. It requires the `asyncpg` extra.

## Installation

```bash
pip install pytest-tripwire[asyncpg]
```

## Setup

In pytest, access `AsyncpgPlugin` through the `tripwire.asyncpg` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

async def test_fetch_users():
    (tripwire.asyncpg
        .new_session()
        .expect("connect",  returns=None)
        .expect("fetch",    returns=[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}])
        .expect("close",    returns=None))

    with tripwire:
        import asyncpg
        conn = await asyncpg.connect(host="localhost", database="myapp", user="admin")
        rows = await conn.fetch("SELECT id, name FROM users")
        await conn.close()

    assert rows == [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

    tripwire.asyncpg.assert_connect(host="localhost", database="myapp", user="admin")
    tripwire.asyncpg.assert_fetch(query="SELECT id, name FROM users", args=[])
    tripwire.asyncpg.assert_close()
```

For manual use outside pytest, construct `AsyncpgPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.asyncpg_plugin import AsyncpgPlugin

verifier = StrictVerifier()
apg = AsyncpgPlugin(verifier)
```

Each verifier may have at most one `AsyncpgPlugin`. A second `AsyncpgPlugin(verifier)` raises `ValueError`.

## State machine

```
disconnected --connect--> connected
connected --execute--> connected
connected --fetch--> connected
connected --fetchrow--> connected
connected --fetchval--> connected
connected --close--> closed
```

Unlike psycopg2/sqlite3, asyncpg does not have an explicit transaction state for simple queries. All query methods (`execute`, `fetch`, `fetchrow`, `fetchval`) keep the connection in the `connected` state.

## Scripting a session

Use `new_session()` to create a `SessionHandle` and chain `.expect()` calls:

```python
(tripwire.asyncpg
    .new_session()
    .expect("connect",  returns=None)
    .expect("fetch",    returns=[{"id": 1}])
    .expect("execute",  returns="INSERT 0 1")
    .expect("close",    returns=None))
```

### `expect()` parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | Step name: `"connect"`, `"execute"`, `"fetch"`, `"fetchrow"`, `"fetchval"`, or `"close"` |
| `returns` | `Any` | required | Value returned by the step (see below) |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused step causes `UnusedMocksError` at teardown |

### Return values by step

| Step | `returns` type | Description |
|---|---|---|
| `connect` | `None` | The fake connection is constructed automatically |
| `execute` | `str` | Status string (e.g., `"INSERT 0 1"`, `"DELETE 3"`) |
| `fetch` | `list[dict]` | List of Record-like dicts |
| `fetchrow` | `dict \| None` | Single Record-like dict, or None if no match |
| `fetchval` | `Any` | Single scalar value |
| `close` | `None` | No return value |

## Connection parameters

The plugin supports both DSN and keyword-based connection parameters:

```python
# DSN style
await asyncpg.connect("postgresql://admin@localhost/myapp")

# Keyword style
await asyncpg.connect(host="localhost", port=5432, database="myapp", user="admin")
```

The `assert_connect()` helper accepts whichever parameters were used:

```python
# For DSN connections
tripwire.asyncpg.assert_connect(dsn="postgresql://admin@localhost/myapp")

# For keyword connections
tripwire.asyncpg.assert_connect(host="localhost", port=5432, database="myapp", user="admin")
```

## Asserting interactions

Each step records an interaction on the timeline. Use the typed assertion helpers on `tripwire.asyncpg`:

### `assert_connect(**kwargs)`

Asserts the next connect interaction. Pass whichever connection fields were used.

```python
tripwire.asyncpg.assert_connect(host="localhost", database="myapp", user="admin")
```

### `assert_execute(*, query, args)`

Asserts the next execute interaction. Both `query` and `args` are required.

```python
tripwire.asyncpg.assert_execute(
    query="INSERT INTO users (name) VALUES ($1)",
    args=["Alice"],
)
```

### `assert_fetch(*, query, args)`

Asserts the next fetch interaction. Both `query` and `args` are required.

```python
tripwire.asyncpg.assert_fetch(query="SELECT id, name FROM users", args=[])
```

### `assert_fetchrow(*, query, args)`

Asserts the next fetchrow interaction. Both `query` and `args` are required.

```python
tripwire.asyncpg.assert_fetchrow(
    query="SELECT id, name FROM users WHERE id = $1",
    args=[1],
)
```

### `assert_fetchval(*, query, args)`

Asserts the next fetchval interaction. Both `query` and `args` are required.

```python
tripwire.asyncpg.assert_fetchval(query="SELECT count(*) FROM users", args=[])
```

### `assert_close()`

Asserts the next close interaction. No fields are required.

```python
tripwire.asyncpg.assert_close()
```

## Full example

**Production code** (`examples/asyncpg_example/app.py`):

```python
--8<-- "examples/asyncpg_example/app.py"
```

**Test** (`examples/asyncpg_example/test_app.py`):

```python
--8<-- "examples/asyncpg_example/test_app.py"
```
