# Quick Start

This guide walks through a complete bigfoot test from setup to teardown and shows what each of the three error types looks like when violated.

## Step 1: Import bigfoot

```python
import bigfoot
```

bigfoot registers an autouse pytest fixture behind the scenes. Every test automatically gets a fresh `StrictVerifier`. No fixture injection or `conftest.py` changes are needed.

## Step 2: Create a mock

bigfoot offers two ways to create mocks:

**Import-site mock** patches a module attribute at its import location. Use `"module.path:attribute"` colon-separated syntax:

```python
cache = bigfoot.mock("myapp.services:cache")
```

**Object mock** patches an attribute on a specific object instance:

```python
email_service = EmailService()
email = bigfoot.mock.object(email_service, "send")
```

Both return a mock object. Calling `bigfoot.mock()` or `bigfoot.mock.object()` registers the mock with the current test's verifier automatically.

## Step 3: Configure return values

```python
email.returns(True)
```

`.returns(True)` appends a return-value entry to the method's FIFO queue. The first call to `email(...)` will return `True`, the second will use the next entry in the queue (or raise `UnmockedInteractionError` if the queue is empty).

For mocks with multiple methods, access methods by attribute:

```python
cache = bigfoot.mock("myapp.services:cache")
cache.get.returns("cached_value")
cache.set.returns(None)
```

## Step 4: Enter the sandbox

```python
with bigfoot:
    result = email_service.send(to="user@example.com", subject="Welcome")
    assert result is True
```

`with bigfoot:` is the preferred sandbox syntax. It is shorthand for `with bigfoot.sandbox():`. Both forms activate all plugins and all registered mocks for the current test. Any mock call is intercepted, recorded to the timeline, and dispatched to the configured side effect. Outside the sandbox, calling a mocked target raises `SandboxNotActiveError`.

`with bigfoot:` returns the active `StrictVerifier` from `__enter__`, so you can capture it if needed:

```python
with bigfoot as v:
    result = email_service.send(to="user@example.com", subject="Welcome")
    # v is the StrictVerifier for this test
```

This is equivalent to `with bigfoot.sandbox() as v:`. Most tests use the module-level API (`bigfoot.mock()`, `bigfoot.assert_interaction()`, etc.) and never need `v` directly. The main case where you need it is registering custom plugins manually:

```python
import bigfoot
from myapp.plugins import DatabasePlugin

def test_with_custom_plugin():
    with bigfoot as v:
        db = DatabasePlugin(v)  # register plugin on this verifier
        db.mock_query("SELECT 1", result=[1])
        ...
```

## Step 5: Assert interactions

```python
email.assert_call(args=(), kwargs={"to": "user@example.com", "subject": "Welcome"})
```

Assertions must happen **after** the sandbox exits. `assert_call()` takes `args` (positional arguments tuple) and `kwargs` (keyword arguments dict) that must match the recorded interaction's details. Both `args` and `kwargs` are required. By default it checks the next unasserted interaction in sequence order. Use `bigfoot.in_any_order()` to relax ordering.

For import-site mocks with methods, assert on the method proxy:

```python
cache.get.assert_call(args=("my_key",), kwargs={})
```

## Step 6: Verify all (automatic in pytest)

In pytest, `verify_all()` is called automatically at teardown. It checks that:

1. Every interaction in the timeline has been asserted (no `UnassertedInteractionsError`)
2. Every required mock is consumed (no `UnusedMocksError`)

When constructing `StrictVerifier` manually (outside pytest), call `verify_all()` yourself.

---

## What each error looks like

### UnmockedInteractionError

Raised immediately when a mock method is called with an empty queue.

```
UnmockedInteractionError: source_id='mock:myapp.services:cache.get', args=('missing_key',), kwargs={},
hint='Unexpected call to myapp.services:cache.get

  Called with: args=('missing_key',), kwargs={}

  To mock this interaction, add before your sandbox:
    verifier.mock("myapp.services:cache").get.returns(<value>)

  Or to mark it optional:
    verifier.mock("myapp.services:cache").get.required(False).returns(<value>)'
```

### UnassertedInteractionsError

Raised at teardown when at least one recorded interaction was never matched by `assert_call()` or `assert_interaction()`.

```
UnassertedInteractionsError: 1 unasserted interaction(s), hint='1 interaction(s) were not asserted

  [sequence=0] [MockPlugin] myapp.services:cache.get
    To assert this interaction:
      verifier.mock("myapp.services:cache").get.assert_call(
          args=("my_key",),
          kwargs={},
      )
'
```

### UnusedMocksError

Raised at teardown when a `required=True` mock was registered but never called.

```
UnusedMocksError: 1 unused mock(s), hint='1 mock(s) were registered but never triggered

  mock:myapp.services:cache.get
    Mock registered at:
      File "test_cache.py", line 5, in test_lookup
        cache.get.returns("value")
    Options:
      - Remove this mock if it's not needed
      - Mark it optional: verifier.mock("myapp.services:cache").get.required(False).returns(...)
'
```

### VerificationError

Raised at teardown when both `UnassertedInteractionsError` and `UnusedMocksError` apply simultaneously. The error contains both sub-errors as `.unasserted` and `.unused` attributes.

### AssertionInsideSandboxError

Raised when `assert_interaction()`, `in_any_order()`, or `verify_all()` is called while a sandbox is still active. Assertions must happen after the sandbox exits.

---

## Complete example

```python
import bigfoot

def test_welcome_email():
    # Create a mock that patches myapp.email:service at the import site
    email = bigfoot.mock("myapp.email:service")
    email.send.returns(True)

    with bigfoot:
        # Code under test calls myapp.email.service.send(...)
        from myapp.email import service
        result = service.send(to="user@example.com", subject="Welcome")
        assert result is True

    email.send.assert_call(
        args=(),
        kwargs={"to": "user@example.com", "subject": "Welcome"},
    )
    # verify_all() called automatically at teardown
```

### Object mock example

For simpler cases where you have direct access to the object being tested:

```python
import bigfoot

class EmailService:
    def send(self, to: str, subject: str) -> bool:
        raise NotImplementedError("real implementation")

def test_welcome_email_object_mock():
    service = EmailService()
    mock = bigfoot.mock.object(service, "send")
    mock.returns(True)

    with bigfoot:
        result = service.send(to="user@example.com", subject="Welcome")
        assert result is True

    mock.assert_call(
        args=(),
        kwargs={"to": "user@example.com", "subject": "Welcome"},
    )
```
