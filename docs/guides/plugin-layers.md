# Plugin Layers

tripwire plugins intercept I/O at different abstraction levels. High-level plugins (boto3, gRPC, elasticsearch) sit above low-level plugins (HTTP, socket). By default, all available plugins are active simultaneously, which means a single operation can be intercepted at multiple layers.

## The layered interception model

When your code calls `boto3.client("s3").get_object(...)`, two things happen under the hood:

1. **Boto3Plugin** intercepts the `_make_api_call` invocation and records the service, operation, and parameters.
2. **HttpPlugin** intercepts the underlying HTTP request that botocore sends to the AWS endpoint.

Both plugins fire independently. Neither knows about the other. Each records its own interaction on the timeline, and each demands that you mock and assert at its level.

The same pattern applies wherever a high-level protocol rides on a low-level transport:

- **SshPlugin** intercepts `paramiko.SSHClient` operations, while **SocketPlugin** intercepts the TCP connections paramiko opens.
- **ElasticsearchPlugin** intercepts Elasticsearch client calls, while **HttpPlugin** intercepts the HTTP requests the client sends.
- **GrpcPlugin** intercepts gRPC stubs, while **HttpPlugin** or **SocketPlugin** intercepts the underlying network I/O.

## What happens with overlapping plugins

When both layers are active, you must mock and assert at both levels. Failing to do so triggers `UnmockedInteractionError` (for the missing mock) or `UnassertedInteractionError` (for the unasserted interaction) at teardown.

For example, with both `boto3` and `http` enabled:

```python
def test_s3_both_layers():
    # Must mock at BOTH levels
    tripwire.boto3_mock.mock_call("s3", "GetObject", returns={"Body": b"data", "ContentLength": 4})
    tripwire.http.mock_request("PUT", "https://s3.amazonaws.com/...", returns=httpx.Response(200))

    with tripwire:
        client = boto3.client("s3")
        client.get_object(Bucket="my-bucket", Key="file.txt")

    # Must assert at BOTH levels
    tripwire.boto3_mock.assert_boto3_call(service="s3", operation="GetObject", params={...})
    tripwire.http.assert_request("PUT", "https://s3.amazonaws.com/...")
```

This is almost never what you want. Testing at two layers simultaneously adds noise without adding confidence.

## Choosing your interception granularity

Use `disabled_plugins` to pick the layer that matches your test's intent:

### Test at the high level (recommended for most cases)

Disable the low-level plugin. You test the logical operation without caring about HTTP details.

```toml
[tool.tripwire]
disabled_plugins = ["http", "socket"]
```

```python
def test_s3_get(tripwire):
    tripwire.boto3_mock.mock_call("s3", "GetObject", returns={"Body": b"data", "ContentLength": 4})

    with tripwire:
        response = boto3.client("s3").get_object(Bucket="b", Key="k")

    tripwire.boto3_mock.assert_boto3_call(
        service="s3", operation="GetObject",
        params={"Bucket": "b", "Key": "k"},
    )
```

### Test at the low level

Disable the high-level plugin. Useful when you need to verify exact HTTP behavior, headers, or retry logic.

```toml
[tool.tripwire]
disabled_plugins = ["boto3"]
```

### Leave both active

Requires mocking and asserting at both layers. Only do this if you genuinely need to verify both the logical operation and its transport behavior in the same test.

## Common layer pairs

| High-level | Low-level | Recommendation |
|---|---|---|
| boto3 | http | Disable http |
| elasticsearch | http | Disable http |
| grpc | http / socket | Disable http |
| ssh | socket | Disable socket |
| pika | socket | Disable socket |
| mongo | socket | Disable socket |

For projects that use multiple high-level plugins (e.g., both boto3 and elasticsearch), disabling the shared low-level layer once covers all of them:

```toml
[tool.tripwire]
disabled_plugins = ["http", "socket"]
```

## Configuration

Add `disabled_plugins` to `[tool.tripwire]` in your `pyproject.toml`:

```toml
[tool.tripwire]
disabled_plugins = ["http", "socket"]
```

Alternatively, use `enabled_plugins` to allowlist only the plugins you need. The two options are mutually exclusive:

```toml
[tool.tripwire]
# Only these plugins will be active -- everything else is off.
enabled_plugins = ["boto3", "subprocess", "logging"]
```

See the [Configuration Guide](configuration.md) for full details on config file discovery and format.
