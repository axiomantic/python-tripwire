# bigfoot

A pluggable interaction auditor for Python tests. Enforces a closed-loop contract:

- **Bouncer**: Every external interaction must be pre-authorized. Unmocked calls raise `UnmockedInteractionError` immediately.
- **Auditor**: Every recorded interaction must be explicitly asserted. Unasserted interactions raise `UnassertedInteractionsError` at teardown.
- **Accountant**: Every registered mock must be triggered. Unused mocks raise `UnusedMocksError` at teardown.

## Installation

```bash
pip install bigfoot           # Core: MockPlugin
pip install bigfoot[http]     # + HttpPlugin (httpx, requests, urllib)
pip install bigfoot[matchers] # + dirty-equals matchers
pip install bigfoot[dev]      # All of the above + pytest, mypy, ruff
```

## Quick Start

```python
import bigfoot

def test_payment_flow():
    bigfoot.http.mock_response("POST", "https://api.stripe.com/v1/charges",
                               json={"id": "ch_123"}, status=200)

    with bigfoot.sandbox():
        import httpx
        response = httpx.post("https://api.stripe.com/v1/charges",
                              json={"amount": 5000})

    bigfoot.assert_interaction(
        bigfoot.http.request,
        method="POST",
        url="https://api.stripe.com/v1/charges",
        headers=IsMapping(),  # use dirty-equals or ANY for headers
        body=None,
        status=200,
    )
    assert response.json()["id"] == "ch_123"
    # verify_all() called automatically at test teardown
```

## Mock Plugin

```python
import bigfoot

def test_service_calls():
    payment = bigfoot.mock("PaymentService")
    payment.charge.returns({"status": "ok"})
    payment.refund.required(False).returns(None)  # optional mock

    with bigfoot.sandbox():
        result = payment.charge(order_id=42)

    bigfoot.assert_interaction(payment.charge, args=(42,), kwargs={"order_id": 42})
```

### Side Effects

```python
proxy.compute.returns(42)                   # Return a value
proxy.compute.returns(1).returns(2)         # FIFO: first call returns 1, second returns 2
proxy.fetch.raises(IOError("unavailable"))  # Raise an exception
proxy.transform.calls(lambda x: x.upper()) # Delegate to a function
proxy.log.required(False).returns(None)     # Optional: no UnusedMocksError if never called
```

## Async Tests

`sandbox()` and `in_any_order()` both support `async with`:

```python
import bigfoot
import httpx

async def test_async_flow():
    bigfoot.http.mock_response("GET", "https://api.example.com/items", json=[])

    async with bigfoot.sandbox():
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.example.com/items")

    bigfoot.assert_interaction(bigfoot.http.request, method="GET")
```

## Concurrent Assertions

When tests make concurrent HTTP requests (e.g., via `asyncio.TaskGroup`), use `in_any_order()` to relax the FIFO ordering requirement:

```python
import bigfoot
import asyncio, httpx

async def test_concurrent():
    bigfoot.http.mock_response("GET", "https://api.example.com/a", json={"a": 1})
    bigfoot.http.mock_response("GET", "https://api.example.com/b", json={"b": 2})

    async with bigfoot.sandbox():
        async with asyncio.TaskGroup() as tg:
            ta = tg.create_task(httpx.AsyncClient().get("https://api.example.com/a"))
            tb = tg.create_task(httpx.AsyncClient().get("https://api.example.com/b"))

    with bigfoot.in_any_order():
        bigfoot.assert_interaction(bigfoot.http.request, url="https://api.example.com/a")
        bigfoot.assert_interaction(bigfoot.http.request, url="https://api.example.com/b")
```

`in_any_order()` operates globally across all plugin types (mock and HTTP).

## Spy / Pass-Through

### Spy: delegating to a real implementation

`bigfoot.spy(name, real)` creates a `MockProxy` that delegates to `real` when its call queue is empty. Queue entries take priority; the real object is called only when no mock entry remains. The interaction is recorded on the timeline regardless.

```python
import bigfoot

real_service = PaymentService()
payment = bigfoot.spy("PaymentService", real_service)
payment.charge.returns({"id": "mock-123"})  # queue entry: takes priority

with bigfoot.sandbox():
    result1 = payment.charge(100)   # uses queue entry
    result2 = payment.charge(200)   # queue empty: delegates to real_service.charge(200)

bigfoot.assert_interaction(payment.charge, args=(100,), kwargs={})
bigfoot.assert_interaction(payment.charge, args=(200,), kwargs={})
```

`bigfoot.mock("PaymentService", wraps=real_service)` is the keyword-argument form and is equivalent.

### HTTP pass-through: real HTTP calls

`bigfoot.http.pass_through(method, url)` registers a permanent routing rule. When a request matches the rule and no mock matches first, the real HTTP call is made through the original transport. The interaction is still recorded on the timeline and must be asserted.

```python
import bigfoot, httpx

def test_mixed():
    bigfoot.http.mock_response("GET", "https://api.example.com/cached", json={"data": "cached"})
    bigfoot.http.pass_through("GET", "https://api.example.com/live")

    with bigfoot.sandbox():
        mocked = httpx.get("https://api.example.com/cached")   # returns mock
        real   = httpx.get("https://api.example.com/live")     # makes real HTTP call

    bigfoot.assert_interaction(bigfoot.http.request,
                               method="GET", url="https://api.example.com/cached",
                               headers=IsMapping(), body=None, status=200)
    bigfoot.assert_interaction(bigfoot.http.request,
                               method="GET", url="https://api.example.com/live",
                               headers=IsMapping(), body=None, status=200)
```

Pass-through rules are routing hints, not assertions. Unused pass-through rules do not raise `UnusedMocksError`.

## pytest Integration

No fixture injection required. Install bigfoot and `import bigfoot` in any test:

```python
import bigfoot

def test_something():
    svc = bigfoot.mock("MyService")
    svc.call.returns("ok")

    with bigfoot.sandbox():
        result = svc.call()

    bigfoot.assert_interaction(svc.call)
    # verify_all() runs at teardown automatically
```

An explicit `bigfoot_verifier` fixture is available as an escape hatch when you need direct access to the `StrictVerifier` object.

## HTTP Interception Scope

`HttpPlugin` intercepts at the transport/adapter level:

- `httpx.Client` and `httpx.AsyncClient` (class-level transport patch)
- `requests.get()`, `requests.Session`, etc. (class-level adapter patch)
- `urllib.request.urlopen()` (via `install_opener`)
- `asyncio.BaseEventLoop.run_in_executor` (propagates context to thread pool executors)

Not intercepted: `httpx.ASGITransport`, `httpx.WSGITransport`, `aiohttp`.

## Error Messages

bigfoot errors include copy-pasteable remediation hints:

```
UnmockedInteractionError: source_id='mock:PaymentService.charge', args=('order_42',), kwargs={},
hint='Unexpected call to PaymentService.charge

  Called with: args=('order_42',), kwargs={}

  To mock this interaction, add before your sandbox:
    bigfoot.mock("PaymentService").charge.returns(<value>)

  Or to mark it optional:
    bigfoot.mock("PaymentService").charge.required(False).returns(<value>)'
```

## Public API

```python
import bigfoot

# Module-level (preferred in pytest)
bigfoot.mock("Name")                    # create/retrieve a named MockProxy
bigfoot.mock("Name", wraps=real)        # spy: delegate to real when queue empty
bigfoot.spy("Name", real)              # positional form of wraps=
bigfoot.sandbox()                       # context manager: activate all plugins
bigfoot.assert_interaction(source, **fields)  # assert next interaction; ALL assertable fields required
bigfoot.in_any_order()                  # relax FIFO ordering for assertions
bigfoot.verify_all()                    # explicit verification (automatic in pytest)
bigfoot.current_verifier()              # access the StrictVerifier directly
bigfoot.http                            # proxy to the HttpPlugin for this test

# Classes (for manual use or custom plugins)
from bigfoot import (
    StrictVerifier,
    SandboxContext,
    InAnyOrderContext,
    MockPlugin,
    BigfootError,
    AssertionInsideSandboxError,
    NoActiveVerifierError,
    UnmockedInteractionError,
    UnassertedInteractionsError,
    UnusedMocksError,
    VerificationError,
    InteractionMismatchError,
    MissingAssertionFieldsError,
    SandboxNotActiveError,
    ConflictError,
)
from bigfoot.plugins.http import HttpPlugin  # requires bigfoot[http]
```

## Requirements

- Python 3.11+
- pytest (for automatic per-test verifier and `verify_all()` at teardown)

## License

MIT
