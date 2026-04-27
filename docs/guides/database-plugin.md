# DatabasePlugin Guide

`DatabasePlugin` intercepts `sqlite3.connect()` and returns a fake connection object that routes all operations through a session script. It is included in core tripwire -- no extra required.

## Setup

In pytest, access `DatabasePlugin` through the `tripwire.db_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_select_users():
    (tripwire.db_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("execute",  returns=[[1, "Alice"], [2, "Bob"]])
        .expect("close",    returns=None))

    with tripwire:
        import sqlite3
        conn = sqlite3.connect(":memory:")
        cursor = conn.execute("SELECT id, name FROM users")
        rows = cursor.fetchall()
        conn.close()

    assert rows == [[1, "Alice"], [2, "Bob"]]

    tripwire.db_mock.assert_connect(database=":memory:")
    tripwire.db_mock.assert_execute(sql="SELECT id, name FROM users", parameters=())
    tripwire.db_mock.assert_close()
```

For manual use outside pytest, construct `DatabasePlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.database_plugin import DatabasePlugin

verifier = StrictVerifier()
db = DatabasePlugin(verifier)
```

Each verifier may have at most one `DatabasePlugin`. A second `DatabasePlugin(verifier)` raises `ValueError`.

## State machine

```
disconnected --connect--> connected --execute--> in_transaction
in_transaction --execute--> in_transaction
in_transaction --commit--> connected
in_transaction --rollback--> connected
connected --close--> closed
in_transaction --close--> closed
```

`sqlite3.connect()` transitions from `disconnected` to `connected`. Each `execute()` moves the connection into `in_transaction`. `commit()` and `rollback()` return it to `connected`. `close()` can be called from either `connected` or `in_transaction`.

## Scripting a session

Use `new_session()` to create a `SessionHandle` and chain `.expect()` calls:

```python
(tripwire.db_mock
    .new_session()
    .expect("connect",  returns=None)
    .expect("execute",  returns=[["row1"], ["row2"]])
    .expect("commit",   returns=None)
    .expect("close",    returns=None))
```

### `expect()` parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | Step name: `"connect"`, `"execute"`, `"commit"`, `"rollback"`, or `"close"` |
| `returns` | `Any` | required | Value returned by the step (see below) |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused step causes `UnusedMocksError` at teardown |

### Return values by step

| Step | `returns` type | Description |
|---|---|---|
| `connect` | `None` | The fake connection is constructed automatically |
| `execute` | `list[list]` | Rows returned by `fetchone()`, `fetchall()`, or `fetchmany()` |
| `commit` | `None` | No return value |
| `rollback` | `None` | No return value |
| `close` | `None` | No return value |

## Cursor proxy behavior

The fake connection's `execute()` method returns a cursor proxy. The rows you specify in `returns=` are available through the standard cursor methods:

```python
(tripwire.db_mock
    .new_session()
    .expect("connect",  returns=None)
    .expect("execute",  returns=[[1, "Alice"], [2, "Bob"], [3, "Carol"]])
    .expect("close",    returns=None))

with tripwire:
    conn = sqlite3.connect(":memory:")
    cursor = conn.execute("SELECT id, name FROM users")

    # fetchone() returns rows one at a time
    first = cursor.fetchone()   # [1, "Alice"]

    # fetchmany(size) returns a batch
    batch = cursor.fetchmany(1) # [[2, "Bob"]]

    # fetchall() returns remaining rows
    rest = cursor.fetchall()    # [[3, "Carol"]]

    conn.close()
```

You can also use `conn.cursor()` to create a cursor first, then call `cursor.execute()`:

```python
with tripwire:
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("SELECT val FROM t")
    rows = cur.fetchall()
    conn.close()
```

Both styles produce the same interactions on the timeline.

## Asserting interactions

Each step records an interaction on the timeline. Use the typed assertion helpers on `tripwire.db_mock`:

### `assert_connect(*, database)`

Asserts the next connect interaction. The `database` field is required.

```python
tripwire.db_mock.assert_connect(database=":memory:")
```

### `assert_execute(*, sql, parameters)`

Asserts the next execute interaction. Both `sql` and `parameters` are required.

```python
tripwire.db_mock.assert_execute(sql="INSERT INTO users (name) VALUES (?)", parameters=("Alice",))
```

### `assert_commit()`

Asserts the next commit interaction. No fields are required.

```python
tripwire.db_mock.assert_commit()
```

### `assert_rollback()`

Asserts the next rollback interaction. No fields are required.

```python
tripwire.db_mock.assert_rollback()
```

### `assert_close()`

Asserts the next close interaction. No fields are required.

```python
tripwire.db_mock.assert_close()
```

## Commit and rollback

Each `execute()` moves the connection into `in_transaction`. `commit()` and `rollback()` return it to `connected`, allowing further `execute()` calls:

```python
def test_commit_then_execute():
    (tripwire.db_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("execute",  returns=[])
        .expect("commit",   returns=None)
        .expect("execute",  returns=[])
        .expect("close",    returns=None))

    with tripwire:
        conn = sqlite3.connect(":memory:")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.execute("INSERT INTO t VALUES (2)")
        conn.close()

    tripwire.db_mock.assert_connect(database=":memory:")
    tripwire.db_mock.assert_execute(sql="INSERT INTO t VALUES (1)", parameters=())
    tripwire.db_mock.assert_commit()
    tripwire.db_mock.assert_execute(sql="INSERT INTO t VALUES (2)", parameters=())
    tripwire.db_mock.assert_close()
```

Calling `commit()` from `connected` (before any `execute()`) raises `InvalidStateError`.

## Full example

**Production code** (`examples/database_example/app.py`):

```python
--8<-- "examples/database_example/app.py"
```

**Test** (`examples/database_example/test_app.py`):

```python
--8<-- "examples/database_example/test_app.py"
```
