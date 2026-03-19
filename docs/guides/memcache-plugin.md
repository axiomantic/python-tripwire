# MemcachePlugin Guide

`MemcachePlugin` intercepts `pymemcache.client.base.Client` methods at the class level. It covers single-key operations (`get`, `set`, `delete`, `incr`, `decr`, etc.), multi-key batch operations (`get_multi`, `set_many`, `delete_many`, etc.), and uses a per-command FIFO queue rather than a session handle with state transitions.

## Installation

```bash
pip install bigfoot[pymemcache]
```

This installs `pymemcache`.

## Setup

In pytest, access `MemcachePlugin` through the `bigfoot.memcache_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import bigfoot

def test_session_cache():
    bigfoot.memcache_mock.mock_command("GET", returns=b"user:42")

    with bigfoot:
        from pymemcache.client.base import Client
        client = Client(("localhost", 11211))
        value = client.get("session:abc")

    assert value == b"user:42"

    bigfoot.memcache_mock.assert_get(command="GET", key="session:abc")
```

For manual use outside pytest, construct `MemcachePlugin` explicitly:

```python
from bigfoot import StrictVerifier
from bigfoot.plugins.memcache_plugin import MemcachePlugin

verifier = StrictVerifier()
memcache_mock = MemcachePlugin(verifier)
```

Each verifier may have at most one `MemcachePlugin`. A second `MemcachePlugin(verifier)` raises `ValueError`.

## Registering mock commands

Use `bigfoot.memcache_mock.mock_command(command, *, returns, ...)` to register a mock before entering the sandbox:

```python
bigfoot.memcache_mock.mock_command("SET", returns=True)
bigfoot.memcache_mock.mock_command("GET", returns=b"cached")
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `command` | `str` | required | Memcache method name, case-insensitive (e.g., `"get"`, `"set"`, `"delete"`) |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### Supported operations

**Single-key reads:** `get`, `gets`, `delete`

**Single-key writes:** `set`, `add`, `replace`, `cas`, `append`, `prepend`

**Counter operations:** `incr`, `decr`

**Multi-key reads:** `get_multi`, `get_many`, `gets_many`

**Multi-key writes:** `set_multi`, `set_many`

**Multi-key deletes:** `delete_multi`, `delete_many`

## Per-command FIFO queues

Each command name has its own independent FIFO queue. Multiple `mock_command("GET", ...)` calls are consumed in registration order when `get()` is executed:

```python
def test_multiple_gets():
    bigfoot.memcache_mock.mock_command("GET", returns=b"first")
    bigfoot.memcache_mock.mock_command("GET", returns=b"second")

    with bigfoot:
        from pymemcache.client.base import Client
        client = Client(("localhost", 11211))
        v1 = client.get("key1")
        v2 = client.get("key2")

    assert v1 == b"first"
    assert v2 == b"second"

    bigfoot.memcache_mock.assert_get(command="GET", key="key1")
    bigfoot.memcache_mock.assert_get(command="GET", key="key2")
```

Command names are case-insensitive: `mock_command("get", ...)` matches a `client.get(...)` call.

## Asserting interactions

Use the typed assertion helpers on `bigfoot.memcache_mock`. Each helper requires all detail fields for its operation type.

### `assert_get(command, key)`

Asserts the next read interaction (GET, GETS, DELETE).

```python
bigfoot.memcache_mock.assert_get(command="GET", key="session:abc")
```

| Parameter | Type | Description |
|---|---|---|
| `command` | `str` | Command name (e.g., `"GET"`, `"GETS"`, `"DELETE"`) |
| `key` | `str` | The key that was looked up |

### `assert_set(command, key, value, expire=0)`

Asserts the next write interaction (SET, ADD, REPLACE, CAS, APPEND, PREPEND).

```python
bigfoot.memcache_mock.assert_set(command="SET", key="session:abc", value=b"user:42", expire=3600)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `command` | `str` | required | Command name (e.g., `"SET"`, `"ADD"`, `"REPLACE"`) |
| `key` | `str` | required | The key being written |
| `value` | `Any` | required | The value being stored |
| `expire` | `int` | `0` | TTL in seconds |

### `assert_delete(command, key)`

Asserts the next delete interaction.

```python
bigfoot.memcache_mock.assert_delete(command="DELETE", key="session:abc")
```

| Parameter | Type | Description |
|---|---|---|
| `command` | `str` | Command name (e.g., `"DELETE"`) |
| `key` | `str` | The key being deleted |

### `assert_incr(command, key, value=1)`

Asserts the next counter interaction (INCR, DECR).

```python
bigfoot.memcache_mock.assert_incr(command="INCR", key="page_views", value=1)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `command` | `str` | required | Command name (`"INCR"` or `"DECR"`) |
| `key` | `str` | required | The counter key |
| `value` | `int` | `1` | The increment/decrement amount |

## Simulating errors

Use the `raises` parameter to simulate memcache errors:

```python
import bigfoot

def test_memcache_connection_error():
    bigfoot.memcache_mock.mock_command(
        "GET",
        returns=None,
        raises=ConnectionError("memcached unreachable"),
    )

    with bigfoot:
        from pymemcache.client.base import Client
        client = Client(("localhost", 11211))
        with pytest.raises(ConnectionError):
            client.get("mykey")

    bigfoot.memcache_mock.assert_get(command="GET", key="mykey")
```

## Full example

```python
from pymemcache.client.base import Client
import bigfoot

def get_user_profile(client, user_id):
    """Fetch user profile from memcache, return None on miss."""
    cached = client.get(f"profile:{user_id}")
    if cached is not None:
        return cached.decode("utf-8")
    return None

def cache_user_profile(client, user_id, profile_json, ttl=300):
    """Store user profile in memcache with TTL."""
    client.set(f"profile:{user_id}", profile_json.encode("utf-8"), expire=ttl)

def test_cache_hit():
    bigfoot.memcache_mock.mock_command("GET", returns=b'{"name": "Alice"}')

    with bigfoot:
        client = Client(("localhost", 11211))
        result = get_user_profile(client, "42")

    assert result == '{"name": "Alice"}'

    bigfoot.memcache_mock.assert_get(command="GET", key="profile:42")

def test_cache_write():
    bigfoot.memcache_mock.mock_command("SET", returns=True)

    with bigfoot:
        client = Client(("localhost", 11211))
        cache_user_profile(client, "42", '{"name": "Alice"}', ttl=600)

    bigfoot.memcache_mock.assert_set(
        command="SET",
        key="profile:42",
        value=b'{"name": "Alice"}',
        expire=600,
    )
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
bigfoot.memcache_mock.mock_command("DELETE", returns=True, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls a memcache method that has no remaining mocks in its queue, bigfoot raises `UnmockedInteractionError`:

```
memcache.GET(...) was called but no mock was registered.
Register a mock with:
    bigfoot.memcache_mock.mock_command('GET', returns=...)
```
