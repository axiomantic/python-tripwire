# MockPlugin Guide

`MockPlugin` intercepts method calls on named proxy objects. It is the core mocking mechanism in bigfoot, created automatically when you call `bigfoot.mock()`.

## MockProxy and MethodProxy

`bigfoot.mock("Name")` returns a `MockProxy`. Attribute access on a `MockProxy` returns a `MethodProxy` for that method name:

```python
import bigfoot

email = bigfoot.mock("EmailService")  # MockProxy
email.send                            # MethodProxy for "send"
email.send is email.send              # True — proxies are cached
```

`MockProxy` instances are also cached: calling `bigfoot.mock("EmailService")` twice within a test returns the same object.

Private attributes (names starting with `_`) raise `AttributeError`. All other attribute names are valid method names.

## Configuring return values

Use `.returns(value)` to append a return value to the method's FIFO queue:

```python
email.send.returns(True)
```

Multiple `.returns()` calls build a queue. Each call to the mock consumes one entry:

```python
import bigfoot

email = bigfoot.mock("Counter")
email.next_id.returns(1)
email.next_id.returns(2)
email.next_id.returns(3)

with bigfoot:
    assert email.next_id() == 1
    assert email.next_id() == 2
    assert email.next_id() == 3
```

If the queue is exhausted and the mock is called again, bigfoot raises `UnmockedInteractionError`.

## Raising exceptions

Use `.raises(exc)` to append an exception side effect:

```python
email.send.raises(ConnectionError("SMTP unreachable"))
```

You may pass either an exception instance or an exception class:

```python
email.send.raises(ValueError)        # class — raises ValueError()
email.send.raises(ValueError("msg")) # instance — raises with message
```

## Custom side effects

Use `.calls(fn)` to append a callable side effect. The function receives the same `*args` and `**kwargs` as the mock call:

```python
captured = []

def capture_call(*args, **kwargs):
    captured.append(kwargs)
    return True

email.send.calls(capture_call)
```

## Chaining

All configuration methods return the `MethodProxy`, so calls may be chained:

```python
email.send.returns(True).returns(False).raises(IOError("down"))
```

## Optional mocks

By default, every registered side effect is `required=True`. If a required mock is never consumed by the time `verify_all()` runs, bigfoot raises `UnusedMocksError`.

Mark a side effect as optional with `.required(False)`:

```python
email.send.required(False).returns(True)
```

The `required` flag is sticky: once set, it applies to all subsequent `.returns()`, `.raises()`, and `.calls()` calls on that `MethodProxy` until changed again:

```python
email.send.required(False).returns("a").returns("b")  # both optional
email.send.required(True).returns("c")                # back to required
```

## Spy: Delegating to Real Implementations

A spy wraps a real object. When the spy's call queue has an entry, that entry takes priority. When the queue is empty, the call is forwarded to the real object. The interaction is recorded on the timeline in either case, even if the real implementation raises.

### Creating a spy

Use `bigfoot.spy(name, real)` (positional form) or `bigfoot.mock(name, wraps=real)` (keyword form). Both are equivalent.

```python
import bigfoot

real_service = PaymentService()
payment = bigfoot.spy("PaymentService", real_service)
payment.charge.returns({"id": "mock-123"})  # queue entry: takes priority

with bigfoot:
    result1 = payment.charge(100)   # uses queue entry {"id": "mock-123"}
    result2 = payment.charge(200)   # queue empty: delegates to real_service.charge(200)

payment.charge.assert_call(args=(100,), kwargs={})
payment.charge.assert_call(args=(200,), kwargs={})
```

The keyword form:

```python
payment = bigfoot.mock("PaymentService", wraps=real_service)
```

### Behavior summary

| Condition | Result |
|---|---|
| Queue has an entry | Queue entry is consumed and returned (or raised) |
| Queue is empty, `wraps` set | Real object's method is called with the same args |
| Queue is empty, no `wraps` | `UnmockedInteractionError` raised immediately |

The timeline records the interaction in all cases. Assertions with `args=` and `kwargs=` apply to spy calls exactly as they do to plain mocks.

## Assertions

Assertions happen after the sandbox exits. Use `.assert_call()` on the `MethodProxy`:

```python
import bigfoot

def test_email():
    email = bigfoot.mock("EmailService")
    email.send.returns(True)

    with bigfoot:
        email.send(to="user@example.com", subject="Welcome")

    email.send.assert_call(args=(), kwargs={"to": "user@example.com", "subject": "Welcome"})
```

`assert_call()` requires both `args` and `kwargs`. Omitting either raises `MissingAssertionFieldsError`. Use dirty-equals values (e.g., `Anything()`) when you want to assert a field without exact matching.

`assert_call()` is a convenience wrapper around the lower-level `assert_interaction()` call:

```python
# Convenience (recommended):
email.send.assert_call(args=(), kwargs={"to": "user@example.com", "subject": "Welcome"})

# Equivalent low-level call:
bigfoot.assert_interaction(email.send, args=(), kwargs={"to": "user@example.com", "subject": "Welcome"})
```

## In-any-order assertions

By default, `assert_interaction()` checks the next unasserted interaction in timeline order. If multiple mocks fire and order does not matter, wrap assertions in `bigfoot.in_any_order()`:

```python
import bigfoot

def test_notifications():
    email = bigfoot.mock("EmailService")
    email.send.returns(True).returns(True)

    with bigfoot:
        email.send(to="alice@example.com")
        email.send(to="bob@example.com")

    with bigfoot.in_any_order():
        email.send.assert_call(args=(), kwargs={"to": "bob@example.com"})
        email.send.assert_call(args=(), kwargs={"to": "alice@example.com"})
```

`in_any_order()` is a context manager that relaxes ordering globally across all plugins. It is not possible to relax ordering for only one plugin type within a single block.

## Error messages

### UnmockedInteractionError

When a mock method is called inside the sandbox but its queue is empty, bigfoot raises `UnmockedInteractionError` immediately. The error message includes a copy-pasteable hint:

```
Unexpected call to EmailService.send

  Called with: args=(), kwargs={'to': 'user@example.com'}

  To mock this interaction, add before your sandbox:
    bigfoot.mock("EmailService").send.returns(<value>)

  Or to mark it optional:
    bigfoot.mock("EmailService").send.required(False).returns(<value>)
```

### InteractionMismatchError

When `assert_interaction()` is called and the expected source or fields do not match the next recorded interaction, bigfoot raises `InteractionMismatchError`. The error includes the full remaining timeline and a hint:

```
Next interaction did not match assertion

  Expected source: mock:EmailService.send
  Expected fields: kwargs={'to': 'wrong@example.com'}

  Actual next interaction (sequence=0):
    [MockPlugin] EmailService.send

  Remaining timeline (1 interaction(s)):
    [0] [MockPlugin] EmailService.send

  Hint: Did you forget an in_any_order() block?
```

### UnusedMocksError

When `verify_all()` finds required mocks that were never consumed, the error message includes the full Python traceback from where each mock was registered:

```
1 mock(s) were registered but never triggered

  mock:EmailService.send
    Mock registered at:
      File "tests/test_email.py", line 8, in test_welcome
        email.send.returns(True)
    Options:
      - Remove this mock if it's not needed
      - Mark it optional: bigfoot.mock("EmailService").send.required(False).returns(...)
```

## Interaction details

Each mock call is recorded with these fields in `interaction.details`:

| Field | Description |
|---|---|
| `mock_name` | The name passed to `bigfoot.mock()` |
| `method_name` | The attribute name accessed on the proxy |
| `args` | `repr()` of the positional arguments tuple |
| `kwargs` | `repr()` of the keyword arguments dict |

The `args` and `kwargs` fields are assertable and must always be included in `assert_call()`:

```python
email.send.assert_call(args=(), kwargs={"subject": "Welcome"})
```
