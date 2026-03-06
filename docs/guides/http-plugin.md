# HttpPlugin Guide

`HttpPlugin` intercepts HTTP calls made through `httpx` (sync and async), `requests`, and `urllib`. It requires the `bigfoot[http]` extra.

## Installation

```bash
pip install bigfoot[http]
```

This installs `httpx>=0.25.0` and `requests>=2.31.0`.

## Setup

In pytest, access `HttpPlugin` through the `bigfoot.http` proxy. It auto-creates the plugin for the current test on first use — no explicit instantiation needed:

```python
import bigfoot

def test_api():
    bigfoot.http.mock_response("GET", "https://api.example.com/users", json={"users": []})

    with bigfoot:
        import httpx
        response = httpx.get("https://api.example.com/users")

    bigfoot.assert_interaction(bigfoot.http.request, method="GET", url="https://api.example.com/users",
                               headers=IsMapping(), body=None, status=200)
```

For manual use outside pytest, construct `HttpPlugin` explicitly:

```python
from bigfoot import StrictVerifier
from bigfoot.plugins.http import HttpPlugin

verifier = StrictVerifier()
http = HttpPlugin(verifier)
```

Each verifier may have at most one `HttpPlugin`. A second `HttpPlugin(verifier)` raises `ValueError`.

## Registering mock responses

Use `bigfoot.http.mock_response(method, url, ...)` to register a response before entering the sandbox:

```python
bigfoot.http.mock_response("GET", "https://api.example.com/users", json={"users": []})
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
bigfoot.http.mock_response("GET", "https://api.example.com/token", json={"token": "first"})
bigfoot.http.mock_response("GET", "https://api.example.com/token", json={"token": "second"})
```

## Optional responses

Mark a mock response as optional with `required=False`:

```python
bigfoot.http.mock_response("GET", "https://api.example.com/health", json={"ok": True}, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## URL matching

bigfoot matches on scheme, host, path, and (if `params` is provided) query parameters. Query parameters in the actual URL that are not listed in `params` are ignored.

```python
# Matches https://api.example.com/search?q=hello&page=2 if params={"q": "hello"}
bigfoot.http.mock_response("GET", "https://api.example.com/search", json={...}, params={"q": "hello"})
```

## Asserting HTTP interactions

Use `bigfoot.http.request` as the source in `assert_interaction()`. Assertions must happen after the sandbox exits:

```python
import bigfoot, httpx

def test_users():
    bigfoot.http.mock_response("GET", "https://api.example.com/users", json=[])

    with bigfoot:
        response = httpx.get("https://api.example.com/users")

    bigfoot.assert_interaction(bigfoot.http.request, method="GET", url="https://api.example.com/users",
                               headers=IsMapping(), body=None, status=200)
```

`assert_interaction()` requires ALL five assertable fields for HTTP interactions. Omitting any of `method`, `url`, `headers`, `body`, or `status` raises `MissingAssertionFieldsError`. Use `IsMapping()` from `dirty-equals` for headers when you want to assert type without exact matching, or `ANY` from `unittest.mock`.

Fields available in `assert_interaction()` keyword arguments:

| Field | Description |
|---|---|
| `method` | HTTP method, uppercase |
| `url` | Full URL as received |
| `headers` | Request headers dict |
| `body` | Request body decoded as UTF-8 |
| `status` | Response status code |

## Using with httpx sync

```python
import bigfoot, httpx

def test_httpx_sync():
    bigfoot.http.mock_response("GET", "https://api.example.com/data", json={"value": 42})

    with bigfoot:
        response = httpx.get("https://api.example.com/data")
        assert response.status_code == 200
        assert response.json() == {"value": 42}

    bigfoot.assert_interaction(bigfoot.http.request, method="GET", url="https://api.example.com/data",
                               headers=IsMapping(), body=None, status=200)
```

## Using with httpx async

```python
import bigfoot, httpx

async def test_httpx_async():
    bigfoot.http.mock_response("POST", "https://api.example.com/items", json={"id": 1}, status=201)

    async with bigfoot:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://api.example.com/items", json={"name": "widget"})
        assert response.status_code == 201

    bigfoot.assert_interaction(bigfoot.http.request, method="POST", url="https://api.example.com/items",
                               headers=IsMapping(), body=None, status=201)
```

## Using with requests

```python
import bigfoot, requests

def test_requests():
    bigfoot.http.mock_response("DELETE", "https://api.example.com/items/99", status=204)

    with bigfoot:
        response = requests.delete("https://api.example.com/items/99")
        assert response.status_code == 204

    bigfoot.assert_interaction(bigfoot.http.request, method="DELETE", url="https://api.example.com/items/99",
                               headers=IsMapping(), body=None, status=204)
```

## UnmockedInteractionError for HTTP

When HTTP code fires a request with no matching mock, bigfoot raises `UnmockedInteractionError` with a hint:

```
Unexpected HTTP request: GET https://api.example.com/data

  To mock this request, add before your sandbox:
    bigfoot.http.mock_response("GET", "https://api.example.com/data", json={...})

  Or to mark it optional:
    bigfoot.http.mock_response("GET", "https://api.example.com/data", json={...}, required=False)
```

## ConflictError

At sandbox entry, `HttpPlugin` checks whether `httpx.HTTPTransport.handle_request`, `httpx.AsyncHTTPTransport.handle_async_request`, and `requests.adapters.HTTPAdapter.send` have already been patched by another library. If any of these have been modified by a third party (respx, responses, httpretty, or an unknown library), bigfoot raises `ConflictError`:

```
ConflictError: target='httpx.HTTPTransport.handle_request', patcher='respx'
```

Nested bigfoot sandboxes use reference counting and do not conflict with each other.

## Pass-Through: Real HTTP Calls

`bigfoot.http.pass_through(method, url)` registers a permanent routing rule. When an incoming request matches the rule and no mock response matches first, the real HTTP call is made through the original transport (bypassing bigfoot's interception layer). The interaction is still recorded on the timeline and must be asserted like any other interaction.

Pass-through rules are routing hints, not assertions. An unused pass-through rule does not raise `UnusedMocksError` at teardown.

```python
import bigfoot, httpx

def test_mixed():
    bigfoot.http.mock_response("GET", "https://api.example.com/cached", json={"data": "cached"})
    bigfoot.http.pass_through("GET", "https://api.example.com/live")

    with bigfoot:
        mocked = httpx.get("https://api.example.com/cached")   # returns mock response
        real   = httpx.get("https://api.example.com/live")     # makes real HTTP call

    bigfoot.assert_interaction(bigfoot.http.request,
                               method="GET", url="https://api.example.com/cached",
                               headers=IsMapping(), body=None, status=200)
    bigfoot.assert_interaction(bigfoot.http.request,
                               method="GET", url="https://api.example.com/live",
                               headers=IsMapping(), body=None, status=200)
```

Mock responses are checked before pass-through rules. If a mock matches, the pass-through rule is not evaluated for that request. If no mock matches and a pass-through rule matches, the real call is made. If neither matches, `UnmockedInteractionError` is raised.

## What HttpPlugin patches

When the sandbox activates, `HttpPlugin` installs class-level patches on:

- `httpx.HTTPTransport.handle_request` (sync httpx)
- `httpx.AsyncHTTPTransport.handle_async_request` (async httpx)
- `requests.adapters.HTTPAdapter.send` (requests library)
- `urllib.request` opener (urllib)
- `asyncio.BaseEventLoop.run_in_executor` (propagates ContextVar to thread pool executors)

All patches are reference-counted. Nested sandboxes increment/decrement the count; the actual method replacement only happens at count transitions from 0 to 1 and from 1 to 0.

The `run_in_executor` patch ensures the active-verifier `ContextVar` is copied into threads spawned by `asyncio.run_in_executor`, so HTTP calls made from thread pools are intercepted correctly.
