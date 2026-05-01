# Psycopg2Plugin Guide

`Psycopg2Plugin` intercepts `psycopg2.connect()` and returns a fake connection object that routes all operations through a session script. It requires the `psycopg2` extra.

## Installation

```bash
pip install pytest-tripwire[psycopg2]
```

## Setup

In pytest, access `Psycopg2Plugin` through the `tripwire.psycopg2` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_select_users():
    (tripwire.psycopg2
        .new_session()
        .expect("connect",  returns=None)
        .expect("execute",  returns=[[1, "Alice"], [2, "Bob"]])
        .expect("close",    returns=None))

    with tripwire:
        import psycopg2
        conn = psycopg2.connect(dsn="dbname=myapp")
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM users")
        rows = cur.fetchall()
        conn.close()

    assert rows == [[1, "Alice"], [2, "Bob"]]

    tripwire.psycopg2.assert_connect(dsn="dbname=myapp")
    tripwire.psycopg2.assert_execute(sql="SELECT id, name FROM users", parameters=None)
    tripwire.psycopg2.assert_close()
```

For manual use outside pytest, construct `Psycopg2Plugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.psycopg2_plugin import Psycopg2Plugin

verifier = StrictVerifier()
pg = Psycopg2Plugin(verifier)
```

Each verifier may have at most one `Psycopg2Plugin`. A second `Psycopg2Plugin(verifier)` raises `ValueError`.

## State machine

```
disconnected --connect--> connected --execute--> in_transaction
in_transaction --execute--> in_transaction
in_transaction --commit--> connected
in_transaction --rollback--> connected
connected --close--> closed
in_transaction --close--> closed
```

`psycopg2.connect()` transitions from `disconnected` to `connected`. Each `execute()` moves the connection into `in_transaction`. `commit()` and `rollback()` return it to `connected`. `close()` can be called from either `connected` or `in_transaction`.

## Scripting a session

Use `new_session()` to create a `SessionHandle` and chain `.expect()` calls:

```python
(tripwire.psycopg2
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

## Connection parameters

The plugin supports both DSN and keyword-based connection parameters:

```python
# DSN style
psycopg2.connect(dsn="dbname=myapp host=localhost")

# Keyword style
psycopg2.connect(host="localhost", port=5432, dbname="myapp", user="admin")
```

The `assert_connect()` helper accepts whichever parameters were used:

```python
# For DSN connections
tripwire.psycopg2.assert_connect(dsn="dbname=myapp host=localhost")

# For keyword connections
tripwire.psycopg2.assert_connect(host="localhost", port=5432, dbname="myapp", user="admin")
```

## Cursor behavior

The fake connection's `cursor()` returns a cursor proxy. Call `execute()` on the cursor, then use standard fetch methods:

```python
(tripwire.psycopg2
    .new_session()
    .expect("connect",  returns=None)
    .expect("execute",  returns=[[1, "Alice"], [2, "Bob"], [3, "Carol"]])
    .expect("close",    returns=None))

with tripwire:
    conn = psycopg2.connect(dsn="dbname=test")
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM users")

    # fetchone() returns rows one at a time
    first = cur.fetchone()   # [1, "Alice"]

    # fetchmany(size) returns a batch
    batch = cur.fetchmany(1) # [[2, "Bob"]]

    # fetchall() returns remaining rows
    rest = cur.fetchall()    # [[3, "Carol"]]

    conn.close()
```

## Asserting interactions

Each step records an interaction on the timeline. Use the typed assertion helpers on `tripwire.psycopg2`:

### `assert_connect(**kwargs)`

Asserts the next connect interaction. Pass whichever connection fields were used.

```python
tripwire.psycopg2.assert_connect(dsn="dbname=myapp")
```

### `assert_execute(*, sql, parameters)`

Asserts the next execute interaction. Both `sql` and `parameters` are required.

```python
tripwire.psycopg2.assert_execute(
    sql="INSERT INTO users (name) VALUES (%s)",
    parameters=("Alice",),
)
```

### `assert_commit()`

Asserts the next commit interaction. No fields are required.

```python
tripwire.psycopg2.assert_commit()
```

### `assert_rollback()`

Asserts the next rollback interaction. No fields are required.

```python
tripwire.psycopg2.assert_rollback()
```

### `assert_close()`

Asserts the next close interaction. No fields are required.

```python
tripwire.psycopg2.assert_close()
```

## Full example

**Production code** (`examples/psycopg2_example/app.py`):

```python
--8<-- "examples/psycopg2_example/app.py"
```

**Test** (`examples/psycopg2_example/test_app.py`):

```python
--8<-- "examples/psycopg2_example/test_app.py"
```
