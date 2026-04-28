# HttpPlugin Guide

`HttpPlugin` intercepts HTTP calls made through `httpx` (sync and async), `requests`, `urllib`, and `aiohttp` (if installed). It requires the `python-tripwire[http]` extra. For aiohttp support, also install `python-tripwire[aiohttp]`.

## Installation

```bash
pip install python-tripwire[http]              # httpx, requests, urllib
pip install python-tripwire[aiohttp]           # + aiohttp support
pip install python-tripwire[http,aiohttp]      # both
```

`python-tripwire[http]` installs `httpx>=0.25.0` and `requests>=2.31.0`. `python-tripwire[aiohttp]` installs `aiohttp>=3.9.0`.

## Setup

In pytest, access `HttpPlugin` through the `tripwire.http` proxy. It auto-creates the plugin for the current test on first use — no explicit instantiation needed:

```python
import tripwire

def test_api():
    tripwire.http.mock_response("GET", "https://api.example.com/users", json={"users": []})

    with tripwire:
        import httpx
        response = httpx.get("https://api.example.com/users")

    tripwire.http.assert_request("GET", "https://api.example.com/users",
                                headers=IsMapping(), body="") \
        .assert_response(200, {"content-type": "application/json"}, '{"users": []}')
```

For manual use outside pytest, construct `HttpPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.http import HttpPlugin

verifier = StrictVerifier()
http = HttpPlugin(verifier)
```

Each verifier may have at most one `HttpPlugin`. A second `HttpPlugin(verifier)` raises `ValueError`.

## Registering mock responses

Use `tripwire.http.mock_response(method, url, ...)` to register a response before entering the sandbox:

```python
tripwire.http.mock_response("GET", "https://api.example.com/users", json={"users": []})
```

Parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | HTTP method, case-insensitive (`"GET"`, `"POST"`, etc.) |
| `url` | `str` | required | Full URL to match, including scheme and host |
| `json` | `object` | `None` | Response body serialized as JSON; sets `content-type: application/json` |
| `body` | `str \| bytes \| None` | `None` | Raw response body; mutually exclusive with `json` |
| `status` | `int` | `200` | HTTP status code |
| `headers` | `dict[str, str] \| None` | `None` | Additional response headers |
| `params` | `dict[str, str] \| None` | `None` | Query parameters that must be present in the request URL |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

`json` and `body` are mutually exclusive; providing both raises `ValueError`.

## FIFO ordering

Multiple `mock_response()` calls for the same method+URL are consumed in registration order. The first matching request gets the first registered response, and so on. If a request arrives with no matching mock remaining, `UnmockedInteractionError` is raised.

```python
tripwire.http.mock_response("GET", "https://api.example.com/token", json={"token": "first"})
tripwire.http.mock_response("GET", "https://api.example.com/token", json={"token": "second"})
```

## Optional responses

Mark a mock response as optional with `required=False`:

```python
tripwire.http.mock_response("GET", "https://api.example.com/health", json={"ok": True}, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## URL matching

tripwire matches on scheme, host, path, and (if `params` is provided) query parameters. Query parameters in the actual URL that are not listed in `params` are ignored.

```python
# Matches https://api.example.com/search?q=hello&page=2 if params={"q": "hello"}
tripwire.http.mock_response("GET", "https://api.example.com/search", json={...}, params={"q": "hello"})
```

## Asserting HTTP interactions

Use `tripwire.http.assert_request()` to assert interactions after the sandbox exits. By default, `assert_request()` returns an `HttpAssertionBuilder` that must be completed with `.assert_response()`:

```python
import tripwire, httpx

def test_users():
    tripwire.http.mock_response("GET", "https://api.example.com/users", json=[])

    with tripwire:
        response = httpx.get("https://api.example.com/users")

    tripwire.http.assert_request("GET", "https://api.example.com/users",
                                headers=IsMapping(), body="") \
        .assert_response(200, {"content-type": "application/json"}, '[]')
```

`assert_request()` requires all assertable request fields. Omitting any of `method`, `url`, `headers`, or `body` raises `MissingAssertionFieldsError`. Use `IsMapping()` from `dirty-equals` for headers when you want to assert type without exact matching, or `ANY` from `unittest.mock`.

To assert only request fields without response assertions, pass `require_response=False`:

```python
tripwire.http.assert_request("GET", "https://api.example.com/users",
                            headers=IsMapping(), body="",
                            require_response=False)
```

Parameters for `assert_request()`:

| Parameter | Description |
|---|---|
| `method` | HTTP method, uppercase |
| `url` | Full URL as received |
| `headers` | Request headers dict |
| `body` | Request body decoded as UTF-8 |

## Using with httpx sync

```python
import tripwire, httpx

def test_httpx_sync():
    tripwire.http.mock_response("GET", "https://api.example.com/data", json={"value": 42})

    with tripwire:
        response = httpx.get("https://api.example.com/data")
        assert response.status_code == 200
        assert response.json() == {"value": 42}

    tripwire.http.assert_request("GET", "https://api.example.com/data",
                                headers=IsMapping(), body="") \
        .assert_response(200, {"content-type": "application/json"}, '{"value": 42}')
```

## Using with httpx async

```python
import tripwire, httpx

async def test_httpx_async():
    tripwire.http.mock_response("POST", "https://api.example.com/items", json={"id": 1}, status=201)

    async with tripwire:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://api.example.com/items", json={"name": "widget"})
        assert response.status_code == 201

    tripwire.http.assert_request("POST", "https://api.example.com/items",
                                headers=IsMapping(), body="") \
        .assert_response(201, {"content-type": "application/json"}, '{"id": 1}')
```

## Using with requests

```python
import tripwire, requests

def test_requests():
    tripwire.http.mock_response("DELETE", "https://api.example.com/items/99", status=204)

    with tripwire:
        response = requests.delete("https://api.example.com/items/99")
        assert response.status_code == 204

    tripwire.http.assert_request("DELETE", "https://api.example.com/items/99",
                                headers=IsMapping(), body="") \
        .assert_response(204, IsMapping(), "")
```

## Mocking errors

Use `tripwire.http.mock_error(method, url, raises=...)` to register a mock that raises an exception instead of returning a response. This simulates connection failures, timeouts, and other transport-level errors:

```python
import tripwire, httpx

def test_connection_failure():
    tripwire.http.mock_error("GET", "https://api.example.com/data",
                            raises=httpx.ConnectError("Connection refused"))

    with tripwire:
        try:
            httpx.get("https://api.example.com/data")
        except httpx.ConnectError:
            pass  # expected

    tripwire.http.assert_request("GET", "https://api.example.com/data",
                                headers={}, body="",
                                raised=IsInstance(httpx.ConnectError))
```

Parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | HTTP method, case-insensitive |
| `url` | `str` | required | Full URL to match |
| `raises` | `BaseException` | required | The exception instance to raise |
| `params` | `dict[str, str] \| None` | `None` | Query parameters that must be present |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` |

Error mocks participate in the same FIFO queue as `mock_response()` calls. They are consumed in registration order alongside normal response mocks:

```python
# First call succeeds, second fails
tripwire.http.mock_response("GET", "https://api.example.com/data", json={"ok": True})
tripwire.http.mock_error("GET", "https://api.example.com/data",
                        raises=httpx.ReadTimeout("timeout"))
```

## Asserting error interactions

When an error mock fires, the interaction is recorded with request fields plus a `raised` field instead of response fields. Use the `raised` parameter on `assert_request()` to assert these interactions:

```python
tripwire.http.assert_request("GET", "https://api.example.com/data",
                            headers={}, body="",
                            raised=IsInstance(httpx.ConnectError))
```

When `raised` is provided, `assert_request()` is always terminal (error interactions have no response to chain). It returns `None` regardless of the `require_response` setting.

The assertable fields for error interactions are: `method`, `url`, `request_headers`, `request_body`, and `raised`. Response fields (`response_status`, `response_headers`, `response_body`) are not present and must not be asserted.

## UnmockedInteractionError for HTTP

When HTTP code fires a request with no matching mock, tripwire raises `UnmockedInteractionError` with a hint:

```
Unexpected HTTP request: GET https://api.example.com/data

  To mock this request, add before your sandbox:
    tripwire.http.mock_response("GET", "https://api.example.com/data", json={...})

  Or to mark it optional:
    tripwire.http.mock_response("GET", "https://api.example.com/data", json={...}, required=False)
```

## ConflictError

At sandbox entry, `HttpPlugin` checks whether `httpx.HTTPTransport.handle_request`, `httpx.AsyncHTTPTransport.handle_async_request`, and `requests.adapters.HTTPAdapter.send` have already been patched by another library. If any of these have been modified by a third party (respx, responses, httpretty, or an unknown library), tripwire raises `ConflictError`:

```
ConflictError: target='httpx.HTTPTransport.handle_request', patcher='respx'
```

Nested tripwire sandboxes use reference counting and do not conflict with each other.

## Pass-Through: Real HTTP Calls

`tripwire.http.pass_through(method, url)` registers a permanent routing rule. When an incoming request matches the rule and no mock response matches first, the real HTTP call is made through the original transport (bypassing tripwire's interception layer). The interaction is still recorded on the timeline and must be asserted like any other interaction.

Pass-through rules are routing hints, not assertions. An unused pass-through rule does not raise `UnusedMocksError` at teardown.

```python
import tripwire, httpx

def test_mixed():
    tripwire.http.mock_response("GET", "https://api.example.com/cached", json={"data": "cached"})
    tripwire.http.pass_through("GET", "https://api.example.com/live")

    with tripwire:
        mocked = httpx.get("https://api.example.com/cached")   # returns mock response
        real   = httpx.get("https://api.example.com/live")     # makes real HTTP call

    tripwire.http.assert_request("GET", "https://api.example.com/cached",
                                headers=IsMapping(), body="") \
        .assert_response(200, IsMapping(), '{"data": "cached"}')
    tripwire.http.assert_request("GET", "https://api.example.com/live",
                                headers=IsMapping(), body="") \
        .assert_response(IsInstance(int), IsMapping(), IsInstance(str))
```

Mock responses are checked before pass-through rules. If a mock matches, the pass-through rule is not evaluated for that request. If no mock matches and a pass-through rule matches, the real call is made. If neither matches, `UnmockedInteractionError` is raised.

## Requiring response assertions

By default, `assert_request()` returns an `HttpAssertionBuilder` that must be completed with a chained `.assert_response()` call. This ensures all seven fields (four request + three response) are always asserted. To opt out and assert only request fields, pass `require_response=False` on the call or set it in configuration.

### Configuration

The default is `require_response = true`. To disable it project-wide, add to your `pyproject.toml`:

```toml
[tool.tripwire.http]
require_response = false
```

With the default setting (or explicit `require_response = true`), every `assert_request()` call returns an `HttpAssertionBuilder`:

```python
import tripwire, httpx

def test_api_with_response():
    tripwire.http.mock_response("GET", "https://api.example.com/users", json={"users": []})

    with tripwire:
        response = httpx.get("https://api.example.com/users")

    tripwire.http.assert_request("GET", "https://api.example.com/users") \
        .assert_response(200, {"content-type": "application/json"}, '{"users": []}')
```

### Enabling via constructor

Pass `require_response=True` when constructing the plugin manually:

```python
from tripwire import StrictVerifier
from tripwire.plugins.http import HttpPlugin

verifier = StrictVerifier()
http = HttpPlugin(verifier, require_response=True)
```

### Per-call override

The `require_response` parameter on `assert_request()` overrides both the constructor default and the project-level config:

```python
# Force response assertion for this call (this is the default):
tripwire.http.assert_request("GET", "https://api.example.com/data", require_response=True) \
    .assert_response(200, {}, '{"value": 42}')

# Disable response assertion for this call (opt out of the default):
tripwire.http.assert_request("GET", "https://api.example.com/health", require_response=False)
```

### HttpAssertionBuilder

When `require_response` is active, `assert_request()` returns an `HttpAssertionBuilder`. This builder is lazy: it records the expected request fields but does not touch the timeline until `assert_response()` is called.

`assert_response(status, headers, body)` finalizes the assertion by calling `verifier.assert_interaction()` with all seven fields:

```python
builder = tripwire.http.assert_request("POST", "https://api.example.com/items",
                                       headers={"content-type": "application/json"},
                                       body='{"name": "widget"}',
                                       require_response=True)
builder.assert_response(201, {"content-type": "application/json"}, '{"id": 1}')
```

### Configuration via pyproject.toml

See the [Configuration Guide](configuration.md) for full details on `[tool.tripwire.http]`.

## Using with aiohttp

Requires `python-tripwire[aiohttp]`. If aiohttp is not installed, `HttpPlugin` works normally for other transports.

```python
import tripwire, aiohttp

async def test_aiohttp_get():
    tripwire.http.mock_response("GET", "https://api.example.com/data", json={"value": 42})

    async with tripwire:
        async with aiohttp.ClientSession() as session:
            response = await session.get("https://api.example.com/data")
            assert response.status == 200
            body = await response.json()
            assert body == {"value": 42}

    tripwire.http.assert_request("GET", "https://api.example.com/data",
                                headers={}, body="",
                                require_response=True) \
        .assert_response(200, {"content-type": "application/json"}, '{"value": 42}')
```

aiohttp POST with JSON body:

```python
async def test_aiohttp_post():
    tripwire.http.mock_response("POST", "https://api.example.com/items",
                               json={"id": 1}, status=201)

    async with tripwire:
        async with aiohttp.ClientSession() as session:
            response = await session.post("https://api.example.com/items",
                                          json={"name": "widget"})
            assert response.status == 201

    tripwire.http.assert_request("POST", "https://api.example.com/items",
                                headers={}, body='{"name": "widget"}',
                                require_response=True) \
        .assert_response(201, {"content-type": "application/json"}, '{"id": 1}')
```

The fake aiohttp response supports `response.status`, `await response.json()`, `await response.text()`, `await response.read()`, `response.headers`, and `async with session.get(...) as response:` context manager usage.

## What HttpPlugin patches

When the sandbox activates, `HttpPlugin` installs class-level patches on:

- `httpx.HTTPTransport.handle_request` (sync httpx)
- `httpx.AsyncHTTPTransport.handle_async_request` (async httpx)
- `requests.adapters.HTTPAdapter.send` (requests library)
- `urllib.request` opener (urllib)
- `aiohttp.ClientSession._request` (aiohttp, if installed)
- `asyncio.BaseEventLoop.run_in_executor` (propagates ContextVar to thread pool executors)

All patches are reference-counted. Nested sandboxes increment/decrement the count; the actual method replacement only happens at count transitions from 0 to 1 and from 1 to 0.

The `run_in_executor` patch ensures the active-verifier `ContextVar` is copied into threads spawned by `asyncio.run_in_executor`, so HTTP calls made from thread pools are intercepted correctly.
