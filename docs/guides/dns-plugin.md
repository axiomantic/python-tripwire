# DnsPlugin Guide

`DnsPlugin` intercepts stdlib DNS resolution functions (`socket.getaddrinfo` and `socket.gethostbyname`) at the module level. When `dnspython` is installed, it also intercepts `dns.resolver.resolve` and `dns.resolver.Resolver.resolve`. Each hostname has its own independent FIFO queue, so mocks for different hosts do not interfere with each other.

## Setup

`DnsPlugin` intercepts stdlib `socket` functions, so no extra installation is needed. If you also want to intercept `dnspython` resolution, install it separately.

In pytest, access `DnsPlugin` through the `tripwire.dns_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import socket
import tripwire

def test_hostname_resolution():
    tripwire.dns_mock.mock_gethostbyname("api.example.com", returns="93.184.216.34")

    with tripwire:
        ip = socket.gethostbyname("api.example.com")

    assert ip == "93.184.216.34"

    tripwire.dns_mock.assert_gethostbyname(hostname="api.example.com")
```

For manual use outside pytest, construct `DnsPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.dns_plugin import DnsPlugin

verifier = StrictVerifier()
dns_mock = DnsPlugin(verifier)
```

Each verifier may have at most one `DnsPlugin`. A second `DnsPlugin(verifier)` raises `ValueError`.

## Registering mock lookups

`DnsPlugin` provides three mock methods, one for each intercepted function.

### `mock_getaddrinfo(hostname, *, returns, ...)`

Register a mock for `socket.getaddrinfo()`:

```python
tripwire.dns_mock.mock_getaddrinfo(
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
tripwire.dns_mock.mock_gethostbyname("db.internal", returns="10.0.1.5")
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
tripwire.dns_mock.mock_resolve("mail.example.com", "MX", returns=mock_mx_answer)
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
    tripwire.dns_mock.mock_gethostbyname("api.example.com", returns="93.184.216.34")
    tripwire.dns_mock.mock_gethostbyname("api.example.com", returns="93.184.216.35")

    with tripwire:
        ip1 = socket.gethostbyname("api.example.com")
        ip2 = socket.gethostbyname("api.example.com")

    assert ip1 == "93.184.216.34"
    assert ip2 == "93.184.216.35"

    tripwire.dns_mock.assert_gethostbyname(hostname="api.example.com")
    tripwire.dns_mock.assert_gethostbyname(hostname="api.example.com")
```

## Asserting interactions

Use the typed assertion helpers on `tripwire.dns_mock`. Each helper requires all detail fields for its operation type.

### `assert_getaddrinfo(host, port, family, type, proto)`

```python
tripwire.dns_mock.assert_getaddrinfo(
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
tripwire.dns_mock.assert_gethostbyname(hostname="api.example.com")
```

| Parameter | Type | Description |
|---|---|---|
| `hostname` | `str` | The hostname that was resolved |

### `assert_resolve(qname, rdtype)`

```python
tripwire.dns_mock.assert_resolve(qname="mail.example.com", rdtype="MX")
```

| Parameter | Type | Description |
|---|---|---|
| `qname` | `str` | The query name that was resolved |
| `rdtype` | `str` | The DNS record type that was queried |

## Simulating errors

Use the `raises` parameter to simulate DNS resolution failures:

```python
import socket
import tripwire

def test_dns_resolution_failure():
    tripwire.dns_mock.mock_gethostbyname(
        "nonexistent.example.com",
        returns=None,
        raises=socket.gaierror(8, "nodename nor servname provided, or not known"),
    )

    with tripwire:
        with pytest.raises(socket.gaierror):
            socket.gethostbyname("nonexistent.example.com")

    tripwire.dns_mock.assert_gethostbyname(hostname="nonexistent.example.com")
```

## Full example

**Production code** (`examples/dns_lookup/app.py`):

```python
--8<-- "examples/dns_lookup/app.py"
```

**Test** (`examples/dns_lookup/test_app.py`):

```python
--8<-- "examples/dns_lookup/test_app.py"
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.dns_mock.mock_gethostbyname("optional.host", returns="127.0.0.1", required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls a DNS function for a hostname that has no remaining mocks in its queue, tripwire raises `UnmockedInteractionError`:

```
socket.gethostbyname('unknown.host') was called but no mock was registered.
Register a mock with:
    tripwire.dns_mock.mock_gethostbyname('unknown.host', returns=...)
```
