# DnsPlugin Guide

`DnsPlugin` intercepts stdlib DNS resolution functions (`socket.getaddrinfo` and `socket.gethostbyname`) at the module level. When `dnspython` is installed, it also intercepts `dns.resolver.resolve` and `dns.resolver.Resolver.resolve`. Each hostname has its own independent FIFO queue, so mocks for different hosts do not interfere with each other.

## Setup

`DnsPlugin` intercepts stdlib `socket` functions, so no extra installation is needed. If you also want to intercept `dnspython` resolution, install it separately.

In pytest, access `DnsPlugin` through the `bigfoot.dns_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import socket
import bigfoot

def test_hostname_resolution():
    bigfoot.dns_mock.mock_gethostbyname("api.example.com", returns="93.184.216.34")

    with bigfoot:
        ip = socket.gethostbyname("api.example.com")

    assert ip == "93.184.216.34"

    bigfoot.dns_mock.assert_gethostbyname(hostname="api.example.com")
```

For manual use outside pytest, construct `DnsPlugin` explicitly:

```python
from bigfoot import StrictVerifier
from bigfoot.plugins.dns_plugin import DnsPlugin

verifier = StrictVerifier()
dns_mock = DnsPlugin(verifier)
```

Each verifier may have at most one `DnsPlugin`. A second `DnsPlugin(verifier)` raises `ValueError`.

## Registering mock lookups

`DnsPlugin` provides three mock methods, one for each intercepted function.

### `mock_getaddrinfo(hostname, *, returns, ...)`

Register a mock for `socket.getaddrinfo()`:

```python
bigfoot.dns_mock.mock_getaddrinfo(
    "api.example.com",
    returns=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `hostname` | `str` | required | The hostname to match |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_gethostbyname(hostname, *, returns, ...)`

Register a mock for `socket.gethostbyname()`:

```python
bigfoot.dns_mock.mock_gethostbyname("db.internal", returns="10.0.1.5")
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `hostname` | `str` | required | The hostname to match |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_resolve(qname, rdtype, *, returns, ...)`

Register a mock for `dns.resolver.resolve()` (requires dnspython):

```python
bigfoot.dns_mock.mock_resolve("mail.example.com", "MX", returns=mock_mx_answer)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `qname` | `str` | required | The query name (hostname) to match |
| `rdtype` | `str` | required | The DNS record type (e.g., `"A"`, `"MX"`, `"CNAME"`) |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

## Per-hostname FIFO queues

Each hostname (scoped by operation type) has its own independent FIFO queue. Multiple mocks for the same hostname are consumed in registration order:

```python
def test_multiple_resolutions():
    bigfoot.dns_mock.mock_gethostbyname("api.example.com", returns="93.184.216.34")
    bigfoot.dns_mock.mock_gethostbyname("api.example.com", returns="93.184.216.35")

    with bigfoot:
        ip1 = socket.gethostbyname("api.example.com")
        ip2 = socket.gethostbyname("api.example.com")

    assert ip1 == "93.184.216.34"
    assert ip2 == "93.184.216.35"

    bigfoot.dns_mock.assert_gethostbyname(hostname="api.example.com")
    bigfoot.dns_mock.assert_gethostbyname(hostname="api.example.com")
```

## Asserting interactions

Use the typed assertion helpers on `bigfoot.dns_mock`. Each helper requires all detail fields for its operation type.

### `assert_getaddrinfo(host, port, family, type, proto)`

```python
bigfoot.dns_mock.assert_getaddrinfo(
    host="api.example.com",
    port=443,
    family=socket.AF_INET,
    type=socket.SOCK_STREAM,
    proto=0,
)
```

| Parameter | Type | Description |
|---|---|---|
| `host` | `str` | The hostname that was resolved |
| `port` | `Any` | The port passed to `getaddrinfo` |
| `family` | `int` | Address family (e.g., `socket.AF_INET`) |
| `type` | `int` | Socket type (e.g., `socket.SOCK_STREAM`) |
| `proto` | `int` | Protocol number |

### `assert_gethostbyname(hostname)`

```python
bigfoot.dns_mock.assert_gethostbyname(hostname="api.example.com")
```

| Parameter | Type | Description |
|---|---|---|
| `hostname` | `str` | The hostname that was resolved |

### `assert_resolve(qname, rdtype)`

```python
bigfoot.dns_mock.assert_resolve(qname="mail.example.com", rdtype="MX")
```

| Parameter | Type | Description |
|---|---|---|
| `qname` | `str` | The query name that was resolved |
| `rdtype` | `str` | The DNS record type that was queried |

## Simulating errors

Use the `raises` parameter to simulate DNS resolution failures:

```python
import socket
import bigfoot

def test_dns_resolution_failure():
    bigfoot.dns_mock.mock_gethostbyname(
        "nonexistent.example.com",
        returns=None,
        raises=socket.gaierror(8, "nodename nor servname provided, or not known"),
    )

    with bigfoot:
        with pytest.raises(socket.gaierror):
            socket.gethostbyname("nonexistent.example.com")

    bigfoot.dns_mock.assert_gethostbyname(hostname="nonexistent.example.com")
```

## Full example

```python
import socket
import bigfoot

def resolve_service_endpoint(service_name, port=443):
    """Resolve a service hostname and return (ip, port) tuple."""
    results = socket.getaddrinfo(service_name, port, socket.AF_INET, socket.SOCK_STREAM)
    if not results:
        raise RuntimeError(f"Could not resolve {service_name}")
    family, socktype, proto, canonname, sockaddr = results[0]
    return sockaddr

def test_resolve_service_endpoint():
    bigfoot.dns_mock.mock_getaddrinfo(
        "payments.internal",
        returns=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.2.15", 443)),
        ],
    )

    with bigfoot:
        addr = resolve_service_endpoint("payments.internal")

    assert addr == ("10.0.2.15", 443)

    bigfoot.dns_mock.assert_getaddrinfo(
        host="payments.internal",
        port=443,
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
        proto=0,
    )
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
bigfoot.dns_mock.mock_gethostbyname("optional.host", returns="127.0.0.1", required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls a DNS function for a hostname that has no remaining mocks in its queue, bigfoot raises `UnmockedInteractionError`:

```
socket.gethostbyname('unknown.host') was called but no mock was registered.
Register a mock with:
    bigfoot.dns_mock.mock_gethostbyname('unknown.host', returns=...)
```
