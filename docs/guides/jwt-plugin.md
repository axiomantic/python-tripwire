# JwtPlugin Guide

`JwtPlugin` intercepts `jwt.encode` and `jwt.decode` at the module level (the PyJWT library). It uses a per-operation FIFO queue so you can mock multiple sequential encode or decode calls independently. For security, the `key` parameter is intentionally excluded from interaction details to prevent secret keys from appearing in test assertion output.

## Installation

```bash
pip install python-tripwire[jwt]
```

This installs `PyJWT`.

## Setup

In pytest, access `JwtPlugin` through the `tripwire.jwt` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_token_generation():
    tripwire.jwt.mock_encode(returns="eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.test")

    with tripwire:
        import jwt
        token = jwt.encode({"user_id": "42", "exp": 1700000000}, "secret", algorithm="HS256")

    assert token == "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.test"

    tripwire.jwt.assert_encode(
        payload={"user_id": "42", "exp": 1700000000},
        algorithm="HS256",
        extra_kwargs={},
    )
```

For manual use outside pytest, construct `JwtPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.jwt_plugin import JwtPlugin

verifier = StrictVerifier()
jwt = JwtPlugin(verifier)
```

Each verifier may have at most one `JwtPlugin`. A second `JwtPlugin(verifier)` raises `ValueError`.

## Registering mock operations

`JwtPlugin` provides two mock methods, one for each intercepted function.

### `mock_encode(*, returns, ...)`

Register a mock for `jwt.encode()`:

```python
tripwire.jwt.mock_encode(returns="mocked.jwt.token")
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `returns` | `Any` | required | Token string to return |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_decode(*, returns, ...)`

Register a mock for `jwt.decode()`:

```python
tripwire.jwt.mock_decode(returns={"user_id": "42", "role": "admin"})
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `returns` | `Any` | required | Decoded payload dict to return |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

## Per-operation FIFO queues

Each operation (`encode`, `decode`) has its own independent FIFO queue. Multiple mocks are consumed in registration order:

```python
def test_multiple_decodes():
    tripwire.jwt.mock_decode(returns={"user_id": "1", "role": "admin"})
    tripwire.jwt.mock_decode(returns={"user_id": "2", "role": "viewer"})

    with tripwire:
        import jwt
        claims1 = jwt.decode("token1", "secret", algorithms=["HS256"])
        claims2 = jwt.decode("token2", "secret", algorithms=["HS256"])

    assert claims1["role"] == "admin"
    assert claims2["role"] == "viewer"

    tripwire.jwt.assert_decode(token="token1", algorithms=["HS256"], options=None)
    tripwire.jwt.assert_decode(token="token2", algorithms=["HS256"], options=None)
```

## Asserting interactions

Use the typed assertion helpers on `tripwire.jwt`.

### `assert_encode(*, payload, algorithm, extra_kwargs=None)`

Asserts the next `jwt.encode()` interaction.

```python
tripwire.jwt.assert_encode(
    payload={"user_id": "42", "exp": 1700000000},
    algorithm="HS256",
    extra_kwargs={},
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `payload` | `dict[str, Any]` | required | The JWT payload that was encoded |
| `algorithm` | `str \| None` | required | The algorithm used (e.g., `"HS256"`, `"RS256"`) |
| `extra_kwargs` | `dict[str, Any] \| None` | `None` | Any additional keyword arguments passed to `jwt.encode()` (defaults to `{}`) |

### `assert_decode(*, token, algorithms, options=None)`

Asserts the next `jwt.decode()` interaction.

```python
tripwire.jwt.assert_decode(
    token="eyJ0eXAiOiJKV1Qi...",
    algorithms=["HS256"],
    options=None,
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `token` | `str \| bytes` | required | The JWT token string that was decoded |
| `algorithms` | `Any` | required | The algorithms list passed to `jwt.decode()` |
| `options` | `Any` | `None` | Options dict passed to `jwt.decode()` |

## Security note

The `key` parameter passed to `jwt.encode()` and `jwt.decode()` is intentionally excluded from interaction details. This prevents secret keys from appearing in test assertion output or error messages. You do not need to assert the key value.

## Simulating errors

Use the `raises` parameter to simulate JWT errors:

```python
import jwt
import tripwire

def test_expired_token():
    tripwire.jwt.mock_decode(
        returns=None,
        raises=jwt.ExpiredSignatureError("Signature has expired"),
    )

    with tripwire:
        with pytest.raises(jwt.ExpiredSignatureError):
            jwt.decode("expired.token", "secret", algorithms=["HS256"])

    tripwire.jwt.assert_decode(
        token="expired.token",
        algorithms=["HS256"],
        options=None,
    )
```

## Full example

**Production code** (`examples/jwt_auth/app.py`):

```python
--8<-- "examples/jwt_auth/app.py"
```

**Test** (`examples/jwt_auth/test_app.py`):

```python
--8<-- "examples/jwt_auth/test_app.py"
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.jwt.mock_decode(returns={"sub": "test"}, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls `jwt.encode()` or `jwt.decode()` with no remaining mocks in the queue, tripwire raises `UnmockedInteractionError`:

```
jwt.encode(...) was called but no mock was registered.
Register a mock with:
    tripwire.jwt.mock_encode(returns=...)
```
