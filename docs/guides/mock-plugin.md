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

with bigfoot.sandbox():
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

## Assertions

Assertions happen after the sandbox exits. Use `bigfoot.assert_interaction()` with the `MethodProxy` as the source:

```python
import bigfoot

def test_email():
    email = bigfoot.mock("EmailService")
    email.send.returns(True)

    with bigfoot.sandbox():
        email.send(to="user@example.com", subject="Welcome")

    bigfoot.assert_interaction(email.send, kwargs="{'to': 'user@example.com', 'subject': 'Welcome'}")
```

## In-any-order assertions

By default, `assert_interaction()` checks the next unasserted interaction in timeline order. If multiple mocks fire and order does not matter, wrap assertions in `bigfoot.in_any_order()`:

```python
import bigfoot

def test_notifications():
    email = bigfoot.mock("EmailService")
    email.send.returns(True).returns(True)

    with bigfoot.sandbox():
        email.send(to="alice@example.com")
        email.send(to="bob@example.com")

    with bigfoot.in_any_order():
        bigfoot.assert_interaction(email.send, kwargs="{'to': 'bob@example.com'}")
        bigfoot.assert_interaction(email.send, kwargs="{'to': 'alice@example.com'}")
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

Pass any of these as keyword arguments to `assert_interaction()` to filter on specific values:

```python
bigfoot.assert_interaction(email.send, mock_name="EmailService", method_name="send")
```
