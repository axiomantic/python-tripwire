# GrpcPlugin Guide

`GrpcPlugin` intercepts `grpc.insecure_channel` and `grpc.secure_channel` at the module level, replacing them with fake channel factories that return proxy objects. Each (call_type, method) pair has its own independent FIFO queue. The plugin supports all four gRPC call types: unary-unary, unary-stream (server streaming), stream-unary (client streaming), and stream-stream (bidirectional streaming).

## Installation

```bash
pip install pytest-tripwire[grpc]
```

This installs `grpcio`.

## Setup

In pytest, access `GrpcPlugin` through the `tripwire.grpc` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_grpc_unary_call():
    tripwire.grpc.mock_unary_unary(
        "/mypackage.UserService/GetUser",
        returns={"id": 1, "name": "Alice"},
    )

    with tripwire:
        import grpc
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/mypackage.UserService/GetUser")
        response = stub({"id": 1})

    assert response["name"] == "Alice"

    tripwire.grpc.assert_unary_unary(
        "/mypackage.UserService/GetUser",
        request={"id": 1},
        metadata=None,
    )
```

For manual use outside pytest, construct `GrpcPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.grpc_plugin import GrpcPlugin

verifier = StrictVerifier()
grpc = GrpcPlugin(verifier)
```

Each verifier may have at most one `GrpcPlugin`. A second `GrpcPlugin(verifier)` raises `ValueError`.

## Registering mocks

GrpcPlugin provides four mock registration methods, one for each call type:

### `mock_unary_unary(method, *, returns, ...)`

```python
tripwire.grpc.mock_unary_unary("/pkg.Svc/DoThing", returns={"status": "ok"})
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | gRPC service method path (e.g., `"/package.Service/Method"`) |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_unary_stream(method, *, returns, ...)`

For server streaming RPCs, `returns` is a list of responses that are yielded to the caller:

```python
tripwire.grpc.mock_unary_stream(
    "/pkg.Svc/ListItems",
    returns=[{"id": 1}, {"id": 2}, {"id": 3}],
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | gRPC service method path |
| `returns` | `list` | required | List of response values yielded to the caller |
| `raises` | `BaseException \| None` | `None` | Exception raised after yielding all responses |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_stream_unary(method, *, returns, ...)`

For client streaming RPCs, the client sends a stream of requests and receives a single response:

```python
tripwire.grpc.mock_stream_unary(
    "/pkg.Svc/UploadChunks",
    returns={"bytes_received": 1024},
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | gRPC service method path |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_stream_stream(method, *, returns, ...)`

For bidirectional streaming RPCs, `returns` is a list of responses yielded to the caller:

```python
tripwire.grpc.mock_stream_stream(
    "/pkg.Svc/Chat",
    returns=[{"text": "Hello"}, {"text": "How can I help?"}],
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | gRPC service method path |
| `returns` | `list` | required | List of response values yielded to the caller |
| `raises` | `BaseException \| None` | `None` | Exception raised after yielding all responses |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

## FIFO queues

Each (call_type, method) pair has its own independent FIFO queue. Multiple mocks for the same method and call type are consumed in registration order:

```python
def test_multiple_unary_calls():
    tripwire.grpc.mock_unary_unary("/pkg.Svc/GetUser", returns={"id": 1, "name": "Alice"})
    tripwire.grpc.mock_unary_unary("/pkg.Svc/GetUser", returns={"id": 2, "name": "Bob"})

    with tripwire:
        import grpc
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/GetUser")
        r1 = stub({"id": 1})
        r2 = stub({"id": 2})

    assert r1["name"] == "Alice"
    assert r2["name"] == "Bob"

    tripwire.grpc.assert_unary_unary("/pkg.Svc/GetUser", request={"id": 1})
    tripwire.grpc.assert_unary_unary("/pkg.Svc/GetUser", request={"id": 2})
```

## Asserting interactions

Use the typed assertion helpers on `tripwire.grpc`. All fields (`method`, `request`, `metadata`) are required:

### `assert_unary_unary(method, request, metadata=None)`

```python
tripwire.grpc.assert_unary_unary(
    "/mypackage.UserService/GetUser",
    request={"id": 1},
    metadata=None,
)
```

### `assert_unary_stream(method, request, metadata=None)`

```python
tripwire.grpc.assert_unary_stream(
    "/mypackage.ItemService/ListItems",
    request={"category": "electronics"},
    metadata=None,
)
```

### `assert_stream_unary(method, request, metadata=None)`

For client streaming RPCs, `request` is a list (the iterator is eagerly consumed and stored):

```python
tripwire.grpc.assert_stream_unary(
    "/mypackage.UploadService/UploadChunks",
    request=[b"chunk1", b"chunk2", b"chunk3"],
    metadata=None,
)
```

### `assert_stream_stream(method, request, metadata=None)`

For bidirectional streaming RPCs, `request` is a list:

```python
tripwire.grpc.assert_stream_stream(
    "/mypackage.ChatService/Chat",
    request=[{"text": "Hi"}, {"text": "Help me"}],
    metadata=None,
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | gRPC service method path |
| `request` | `Any` | required | The request object (or list for streaming client) |
| `metadata` | `Any` | `None` | gRPC metadata passed with the call |

## Simulating errors

Use the `raises` parameter to simulate gRPC errors:

```python
import grpc as grpc_lib
import tripwire

def test_grpc_unavailable():
    tripwire.grpc.mock_unary_unary(
        "/pkg.Svc/GetUser",
        returns=None,
        raises=grpc_lib.RpcError(),
    )

    with tripwire:
        import grpc
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/GetUser")
        with pytest.raises(grpc_lib.RpcError):
            stub({"id": 1})

    tripwire.grpc.assert_unary_unary("/pkg.Svc/GetUser", request={"id": 1})
```

For streaming responses, the `raises` parameter causes the exception to be raised after all responses have been yielded:

```python
def test_stream_partial_failure():
    tripwire.grpc.mock_unary_stream(
        "/pkg.Svc/ListItems",
        returns=[{"id": 1}, {"id": 2}],
        raises=grpc_lib.RpcError(),
    )

    with tripwire:
        import grpc
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_stream("/pkg.Svc/ListItems")
        results = []
        with pytest.raises(grpc_lib.RpcError):
            for item in stub({"category": "all"}):
                results.append(item)

    assert len(results) == 2

    tripwire.grpc.assert_unary_stream(
        "/pkg.Svc/ListItems", request={"category": "all"},
    )
```

## Full example

**Production code** (`examples/grpc_service/app.py`):

```python
--8<-- "examples/grpc_service/app.py"
```

**Test** (`examples/grpc_service/test_app.py`):

```python
--8<-- "examples/grpc_service/test_app.py"
```

## Secure channels

`GrpcPlugin` also intercepts `grpc.secure_channel`. Tests using secure channels work identically:

```python
def test_secure_channel():
    tripwire.grpc.mock_unary_unary("/pkg.Svc/GetSecret", returns={"value": "s3cr3t"})

    with tripwire:
        import grpc
        creds = grpc.ssl_channel_credentials()
        channel = grpc.secure_channel("secure.example.com:443", creds)
        stub = channel.unary_unary("/pkg.Svc/GetSecret")
        response = stub({"key": "api_token"})

    assert response["value"] == "s3cr3t"

    tripwire.grpc.assert_unary_unary("/pkg.Svc/GetSecret", request={"key": "api_token"})
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.grpc.mock_unary_unary("/pkg.Svc/Ping", returns={"status": "ok"}, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code makes a gRPC call that has no remaining mocks in its queue, tripwire raises `UnmockedInteractionError`:

```
grpc.unary_unary('/pkg.Svc/GetUser') was called but no mock was registered.
Register a mock with:
    tripwire.grpc.mock_unary_unary('/pkg.Svc/GetUser', returns=...)
```
