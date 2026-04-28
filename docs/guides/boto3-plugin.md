# Boto3Plugin Guide

`Boto3Plugin` intercepts `botocore.client.BaseClient._make_api_call` at the class level. Each AWS service:operation pair has its own independent FIFO queue, so you can mock multiple calls to different (or the same) API operations and they are consumed in registration order.

## Installation

```bash
pip install python-tripwire[boto3]
```

This installs `botocore`.

## Setup

In pytest, access `Boto3Plugin` through the `tripwire.boto3` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_s3_get_object():
    tripwire.boto3.mock_call(
        "s3", "GetObject",
        returns={"Body": b"file-contents", "ContentLength": 13},
    )

    with tripwire:
        import boto3
        client = boto3.client("s3")
        response = client.get_object(Bucket="my-bucket", Key="data.csv")

    assert response["ContentLength"] == 13

    tripwire.boto3.assert_boto3_call(
        service="s3",
        operation="GetObject",
        params={"Bucket": "my-bucket", "Key": "data.csv"},
    )
```

For manual use outside pytest, construct `Boto3Plugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.boto3_plugin import Boto3Plugin

verifier = StrictVerifier()
boto3 = Boto3Plugin(verifier)
```

Each verifier may have at most one `Boto3Plugin`. A second `Boto3Plugin(verifier)` raises `ValueError`.

## Registering mocks

Use `tripwire.boto3.mock_call(service, operation, *, returns, ...)` to register a mock before entering the sandbox:

```python
tripwire.boto3.mock_call("sqs", "SendMessage", returns={"MessageId": "abc123"})
tripwire.boto3.mock_call("dynamodb", "PutItem", returns={})
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `service` | `str` | required | AWS service name (e.g., `"s3"`, `"sqs"`, `"dynamodb"`) |
| `operation` | `str` | required | API operation name in PascalCase (e.g., `"GetObject"`, `"SendMessage"`) |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

## FIFO queues

Each service:operation pair has its own independent FIFO queue. Multiple `mock_call("s3", "GetObject", ...)` calls are consumed in registration order:

```python
def test_multiple_s3_gets():
    tripwire.boto3.mock_call(
        "s3", "GetObject",
        returns={"Body": b"first", "ContentLength": 5},
    )
    tripwire.boto3.mock_call(
        "s3", "GetObject",
        returns={"Body": b"second", "ContentLength": 6},
    )

    with tripwire:
        import boto3
        client = boto3.client("s3")
        r1 = client.get_object(Bucket="bucket", Key="a.txt")
        r2 = client.get_object(Bucket="bucket", Key="b.txt")

    assert r1["Body"] == b"first"
    assert r2["Body"] == b"second"

    tripwire.boto3.assert_boto3_call(
        service="s3", operation="GetObject",
        params={"Bucket": "bucket", "Key": "a.txt"},
    )
    tripwire.boto3.assert_boto3_call(
        service="s3", operation="GetObject",
        params={"Bucket": "bucket", "Key": "b.txt"},
    )
```

## Asserting interactions

Use the `assert_boto3_call` helper on `tripwire.boto3`. All three fields (`service`, `operation`, `params`) are required:

### `assert_boto3_call(service, operation, *, params)`

```python
tripwire.boto3.assert_boto3_call(
    service="sqs",
    operation="SendMessage",
    params={"QueueUrl": "https://sqs.us-east-1.amazonaws.com/123/my-queue", "MessageBody": "hello"},
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `service` | `str` | required | AWS service name |
| `operation` | `str` | required | API operation name in PascalCase |
| `params` | `dict[str, Any]` | required | The API parameters passed to the call |

## Simulating errors

Use the `raises` parameter to simulate AWS service errors:

```python
from botocore.exceptions import ClientError
import tripwire

def test_s3_not_found():
    error_response = {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}}
    tripwire.boto3.mock_call(
        "s3", "GetObject",
        returns=None,
        raises=ClientError(error_response, "GetObject"),
    )

    with tripwire:
        import boto3
        client = boto3.client("s3")
        with pytest.raises(ClientError) as exc_info:
            client.get_object(Bucket="my-bucket", Key="missing.csv")

    assert exc_info.value.response["Error"]["Code"] == "NoSuchKey"

    tripwire.boto3.assert_boto3_call(
        service="s3", operation="GetObject",
        params={"Bucket": "my-bucket", "Key": "missing.csv"},
    )
```

## Full example

**Production code** (`examples/boto3_service/app.py`):

```python
--8<-- "examples/boto3_service/app.py"
```

**Test** (`examples/boto3_service/test_app.py`):

```python
--8<-- "examples/boto3_service/test_app.py"
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.boto3.mock_call("cloudwatch", "PutMetricData", returns={}, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls a boto3 API operation that has no remaining mocks in its queue, tripwire raises `UnmockedInteractionError`:

```
s3.GetObject(...) was called but no mock was registered.
Register a mock with:
    tripwire.boto3.mock_call('s3', 'GetObject', returns=...)
```
