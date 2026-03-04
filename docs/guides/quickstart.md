# Quick Start

This guide walks through a complete bigfoot test from setup to teardown and shows what each of the three error types looks like when violated.

## Step 1: Import bigfoot

```python
import bigfoot
```

bigfoot registers an autouse pytest fixture behind the scenes. Every test automatically gets a fresh `StrictVerifier`. No fixture injection or `conftest.py` changes are needed.

## Step 2: Create a mock

```python
email = bigfoot.mock("EmailService")
```

`bigfoot.mock("EmailService")` returns a `MockProxy` named `"EmailService"`. The name is used in error messages and `assert_interaction()` calls. Calling `bigfoot.mock()` with the same name twice within a test returns the same proxy.

## Step 3: Configure return values

```python
email.send.returns(True)
```

Attribute access on a `MockProxy` returns a `MethodProxy`. `.returns(True)` appends a return-value entry to the method's FIFO queue. The first call to `email.send(...)` will return `True`, the second will use the next entry in the queue (or raise `UnmockedInteractionError` if the queue is empty).

## Step 4: Enter the sandbox

```python
with bigfoot.sandbox():
    result = email.send(to="user@example.com", subject="Welcome")
    assert result is True
```

`bigfoot.sandbox()` activates all plugins for the current test. Any mock call is intercepted, recorded to the timeline, and dispatched to the configured side effect. Outside the sandbox, calling the mock raises `SandboxNotActiveError`.

## Step 5: Assert interactions

```python
bigfoot.assert_interaction(email.send, kwargs="{'to': 'user@example.com', 'subject': 'Welcome'}")
```

Assertions must happen **after** the sandbox exits. `assert_interaction()` takes a source object (a `MethodProxy` or `bigfoot.http.request`) and keyword arguments that must match the recorded interaction's `details` dict. By default it checks the next unasserted interaction in sequence order. Use `bigfoot.in_any_order()` to relax ordering.

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
UnmockedInteractionError: source_id='mock:EmailService.send', args=(), kwargs={'to': 'user@example.com'},
hint='Unexpected call to EmailService.send

  Called with: args=(), kwargs={'to': 'user@example.com'}

  To mock this interaction, add before your sandbox:
    bigfoot.mock("EmailService").send.returns(<value>)

  Or to mark it optional:
    bigfoot.mock("EmailService").send.required(False).returns(<value>)'
```

### UnassertedInteractionsError

Raised at teardown when at least one recorded interaction was never matched by `assert_interaction()`.

```
UnassertedInteractionsError: 1 unasserted interaction(s), hint='1 interaction(s) were not asserted

  [sequence=0] [MockPlugin] EmailService.send
    To assert this interaction:
      bigfoot.assert_interaction(bigfoot.mock("EmailService").send)
'
```

### UnusedMocksError

Raised at teardown when a `required=True` mock was registered but never called.

```
UnusedMocksError: 1 unused mock(s), hint='1 mock(s) were registered but never triggered

  mock:EmailService.send
    Mock registered at:
      File "test_email.py", line 5, in test_welcome_email
        email.send.returns(True)
    Options:
      - Remove this mock if it's not needed
      - Mark it optional: bigfoot.mock("EmailService").send.required(False).returns(...)
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
    email = bigfoot.mock("EmailService")
    email.send.returns(True)

    with bigfoot.sandbox():
        result = email.send(to="user@example.com", subject="Welcome")
        assert result is True

    bigfoot.assert_interaction(email.send)
    # verify_all() called automatically at teardown
```
