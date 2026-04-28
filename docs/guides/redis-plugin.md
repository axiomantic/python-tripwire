# RedisPlugin Guide

`RedisPlugin` intercepts `redis.Redis.execute_command` at the class level. Unlike other stateful plugins, Redis commands carry no inherent ordering constraint, so `RedisPlugin` extends `BasePlugin` directly and uses a per-command FIFO queue rather than a session handle with state transitions.

## Installation

```bash
pip install python-tripwire[redis]
```

This installs `redis>=4.0.0`.

## Setup

In pytest, access `RedisPlugin` through the `tripwire.redis` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_cache_lookup():
    tripwire.redis.mock_command("GET", returns="cached_value")

    with tripwire:
        import redis
        r = redis.Redis()
        value = r.execute_command("GET", "mykey")

    assert value == "cached_value"

    tripwire.redis.assert_command("GET", args=("mykey",), kwargs={})
```

For manual use outside pytest, construct `RedisPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.redis_plugin import RedisPlugin

verifier = StrictVerifier()
redis = RedisPlugin(verifier)
```

Each verifier may have at most one `RedisPlugin`. A second `RedisPlugin(verifier)` raises `ValueError`.

## Registering mock commands

Use `tripwire.redis.mock_command(command, *, returns, ...)` to register a mock before entering the sandbox:

```python
tripwire.redis.mock_command("SET", returns=True)
tripwire.redis.mock_command("GET", returns="hello")
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `command` | `str` | required | Redis command name, case-insensitive |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

## Per-command FIFO queues

Each command name has its own independent FIFO queue. Multiple `mock_command("GET", ...)` calls are consumed in registration order when `GET` is executed:

```python
def test_multiple_gets():
    tripwire.redis.mock_command("GET", returns="first")
    tripwire.redis.mock_command("GET", returns="second")

    with tripwire:
        r = redis.Redis()
        v1 = r.execute_command("GET", "key1")
        v2 = r.execute_command("GET", "key2")

    assert v1 == "first"
    assert v2 == "second"

    tripwire.redis.assert_command("GET", args=("key1",), kwargs={})
    tripwire.redis.assert_command("GET", args=("key2",), kwargs={})
```

Command names are case-insensitive: `mock_command("get", ...)` matches `execute_command("GET", ...)`.

## Asserting interactions

Use the `assert_command` helper on `tripwire.redis`. All three fields (`command`, `args`, `kwargs`) are required:

### `assert_command(command, args, kwargs)`

```python
tripwire.redis.assert_command("SET", args=("mykey", "myvalue"), kwargs={})
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `command` | `str` | required | Redis command name (automatically uppercased) |
| `args` | `tuple` | `()` | Positional arguments passed to `execute_command` after the command name |
| `kwargs` | `dict \| None` | `None` | Keyword arguments passed to `execute_command` (defaults to `{}`) |

## Simulating errors

Use the `raises` parameter to simulate Redis errors:

```python
import redis as redis_lib
import tripwire

def test_redis_error():
    tripwire.redis.mock_command(
        "GET",
        returns=None,
        raises=redis_lib.exceptions.ResponseError("WRONGTYPE"),
    )

    with tripwire:
        r = redis.Redis()
        with pytest.raises(redis_lib.exceptions.ResponseError):
            r.execute_command("GET", "badkey")

    tripwire.redis.assert_command("GET", args=("badkey",), kwargs={})
```

## Full example

**Production code** (`examples/redis_cache/app.py`):

```python
--8<-- "examples/redis_cache/app.py"
```

**Test** (`examples/redis_cache/test_app.py`):

```python
--8<-- "examples/redis_cache/test_app.py"
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.redis.mock_command("PING", returns="PONG", required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls `execute_command` with a command that has no remaining mocks in its queue, tripwire raises `UnmockedInteractionError`:

```
redis.GET(...) was called but no mock was registered.
Register a mock with:
    tripwire.redis.mock_command('GET', returns=...)
```
