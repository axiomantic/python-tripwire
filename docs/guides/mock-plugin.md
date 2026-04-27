# MockPlugin Guide

`MockPlugin` intercepts method calls on mock objects. It is the core mocking mechanism in tripwire, created automatically when you call `tripwire.mock()` or `tripwire.spy()`.

## Creating mocks

### Import-site mock: `tripwire.mock("mod:attr")`

Patches a module-level attribute at its import location. The path uses colon-separated `"module.path:attribute"` syntax:

```python
import tripwire

cache = tripwire.mock("myapp.services:cache")
cache.get.returns("cached_value")
cache.set.returns(None)
```

When the sandbox activates, tripwire resolves the path, saves the original value of `myapp.services.cache`, and replaces it with a dispatch proxy. When the sandbox exits, the original is restored.

The colon separates the importable module path from the attribute path. Nested attributes work with dots after the colon: `"myapp.services:registry.cache"`.

### Object mock: `tripwire.mock.object(target, "attr")`

Patches an attribute on a specific object instance:

```python
import tripwire

service = EmailService()
mock = tripwire.mock.object(service, "send")
mock.returns(True)
```

This is useful when you have direct access to the object being tested and do not need import-site patching.

### Individual activation (context manager)

Mocks can be activated individually using the context manager protocol, outside a tripwire sandbox. In this mode, interactions are recorded but not enforced at teardown:

```python
cache = tripwire.mock("myapp.services:cache")
cache.get.returns("setup_value")

with cache:
    # cache is active here, calls are intercepted
    setup_code()
# cache is deactivated, original restored
```

This is useful for setup code that should not be subject to tripwire's strict verification.

### Sandbox activation (standard)

When you use `with tripwire:`, all registered mocks are activated together and enforcement is enabled. Interactions must be asserted and mocks must be consumed:

```python
cache = tripwire.mock("myapp.services:cache")
cache.get.returns("value")

with tripwire:
    result = get_from_cache("key")

cache.get.assert_call(args=("key",), kwargs={})
```

## Configuring return values

Use `.returns(value)` to append a return value to the method's FIFO queue:

```python
cache.get.returns("first")
cache.get.returns("second")
```

Multiple `.returns()` calls build a queue. Each call to the mock consumes one entry. If the queue is exhausted and the mock is called again, tripwire raises `UnmockedInteractionError`.

For single-callable targets (functions, not objects with methods), use `.returns()` directly on the mock:

```python
mock_fn = tripwire.mock("myapp.utils:calculate_tax")
mock_fn.returns(42.0)
```

## Raising exceptions

Use `.raises(exc)` to append an exception side effect:

```python
cache.get.raises(ConnectionError("Redis unreachable"))
```

You may pass either an exception instance or an exception class:

```python
cache.get.raises(ValueError)          # class: raises ValueError()
cache.get.raises(ValueError("msg"))   # instance: raises with message
```

When `.raises()` is used, the interaction is recorded with a `raised` field in its details. This must be asserted using the `raised` parameter:

```python
cache.get.assert_call(args=("key",), kwargs={}, raised=IsInstance(ConnectionError))
```

## Custom side effects

Use `.calls(fn)` to append a callable side effect. The function receives the same `*args` and `**kwargs` as the mock call:

```python
captured = []

def capture_call(*args, **kwargs):
    captured.append(kwargs)
    return True

cache.set.calls(capture_call)
```

## Chaining

All configuration methods return the proxy, so calls may be chained:

```python
cache.get.returns("first").returns("second").raises(IOError("down"))
```

## Optional mocks

By default, every registered side effect is `required=True`. If a required mock is never consumed by the time `verify_all()` runs, tripwire raises `UnusedMocksError`.

Mark a side effect as optional with `.required(False)`:

```python
cache.get.required(False).returns("fallback")
```

The `required` flag is sticky: once set, it applies to all subsequent `.returns()`, `.raises()`, and `.calls()` calls on that `MethodProxy` until changed again:

```python
cache.get.required(False).returns("a").returns("b")  # both optional
cache.get.required(True).returns("c")                 # back to required
```

## Spy: Delegating to Real Implementations

A spy wraps a real implementation. When the spy's call queue has an entry, that entry takes priority. When the queue is empty, the call is forwarded to the real implementation. The interaction is recorded on the timeline in either case, even if the real implementation raises.

### Creating a spy

Use `tripwire.spy("mod:attr")` for import-site spies or `tripwire.spy.object(target, "attr")` for object spies:

```python
import tripwire

# Import-site spy: wraps the real myapp.services.cache
spy = tripwire.spy("myapp.services:cache")
spy.get.returns("override")  # queue entry: takes priority on first call

with tripwire:
    result1 = get_from_cache("key1")  # returns "override" (queue entry)
    result2 = get_from_cache("key2")  # delegates to real cache.get("key2")

spy.get.assert_call(args=("key1",), kwargs={})
spy.get.assert_call(args=("key2",), kwargs={}, returned="real_value")
```

Object spy:

```python
real_service = PaymentService()
spy = tripwire.spy.object(real_service, "charge")
```

### Spy return value and exception recording

When a spy delegates to the real implementation, the return value or raised exception is captured in the interaction details:

```python
# Assert the real method returned a specific value
spy.get.assert_call(args=("key",), kwargs={}, returned="cached_value")

# Assert the real method raised an exception
spy.get.assert_call(args=("missing",), kwargs={}, raised=IsInstance(KeyError))
```

The `returned` and `raised` fields are only present when the spy delegates to the real implementation (queue empty) or when `.raises()` is used for the `raised` field. They must be included in assertions when present.

### Behavior summary

| Condition | Result |
|---|---|
| Queue has an entry (`.returns()`) | Queue entry is consumed and returned |
| Queue has an entry (`.raises()`) | Queue entry is consumed and exception raised; `raised` recorded |
| Queue is empty, spy mode | Real implementation called; `returned` or `raised` recorded |
| Queue is empty, not spy | `UnmockedInteractionError` raised immediately |

## Assertions

Assertions happen after the sandbox exits. Use `.assert_call()` on the `MethodProxy`:

```python
import tripwire

def test_cache_lookup():
    cache = tripwire.mock("myapp.services:cache")
    cache.get.returns("value")

    with tripwire:
        result = get_from_cache("my_key")

    cache.get.assert_call(args=("my_key",), kwargs={})
```

`assert_call()` requires both `args` and `kwargs`. Omitting either raises `MissingAssertionFieldsError`. Use dirty-equals values (e.g., `Anything()`) when you want to assert a field without exact matching.

For single-callable targets, use `.assert_call()` directly on the mock:

```python
mock_fn = tripwire.mock("myapp.utils:calculate_tax")
mock_fn.returns(42.0)

with tripwire:
    result = calculate_tax(100.0)

mock_fn.assert_call(args=(100.0,), kwargs={})
```

`assert_call()` is a convenience wrapper around the lower-level `assert_interaction()` call:

```python
# Convenience (recommended):
cache.get.assert_call(args=("key",), kwargs={})

# Equivalent low-level call:
tripwire.assert_interaction(cache.get, args=("key",), kwargs={})
```

### Asserting raised exceptions

When a mock uses `.raises()`, include `raised` in the assertion:

```python
cache.get.raises(ConnectionError("down"))

with tripwire:
    try:
        get_from_cache("key")
    except ConnectionError:
        pass

cache.get.assert_call(
    args=("key",),
    kwargs={},
    raised=IsInstance(ConnectionError),
)
```

### Asserting spy return values

When a spy delegates to the real implementation, include `returned` in the assertion:

```python
spy = tripwire.spy("myapp.services:cache")

with tripwire:
    result = get_from_cache("key")

spy.get.assert_call(args=("key",), kwargs={}, returned="cached_value")
```

## In-any-order assertions

By default, `assert_call()` checks the next unasserted interaction in timeline order. If multiple mocks fire and order does not matter, wrap assertions in `tripwire.in_any_order()`:

```python
import tripwire

def test_parallel_lookups():
    cache = tripwire.mock("myapp.services:cache")
    cache.get.returns("a").returns("b")

    with tripwire:
        get_from_cache("key1")
        get_from_cache("key2")

    with tripwire.in_any_order():
        cache.get.assert_call(args=("key2",), kwargs={})
        cache.get.assert_call(args=("key1",), kwargs={})
```

`in_any_order()` is a context manager that relaxes ordering globally across all plugins.

## Error messages

### UnmockedInteractionError

When a mock method is called inside the sandbox but its queue is empty, tripwire raises `UnmockedInteractionError` immediately. The error message includes a copy-pasteable hint:

```
Unexpected call to myapp.services:cache.get

  Called with: args=('missing_key',), kwargs={}

  To mock this interaction, add before your sandbox:
    verifier.mock("myapp.services:cache").get.returns(<value>)

  Or to mark it optional:
    verifier.mock("myapp.services:cache").get.required(False).returns(<value>)
```

### InteractionMismatchError

When `assert_call()` is called and the expected source or fields do not match the next recorded interaction, tripwire raises `InteractionMismatchError`. The error includes the full remaining timeline and a hint.

### UnusedMocksError

When `verify_all()` finds required mocks that were never consumed, the error message includes the full Python traceback from where each mock was registered:

```
1 mock(s) were registered but never triggered

  mock:myapp.services:cache.get
    Mock registered at:
      File "tests/test_cache.py", line 8, in test_lookup
        cache.get.returns("value")
    Options:
      - Remove this mock if it's not needed
      - Mark it optional: verifier.mock("myapp.services:cache").get.required(False).returns(...)
```

## Interaction details

Each mock call is recorded with these fields in `interaction.details`:

| Field | Type | Always present | Description |
|---|---|---|---|
| `mock_name` | `str` | yes | The mock path or display name |
| `method_name` | `str` | yes | The method name (`"__call__"` for direct calls) |
| `args` | `tuple` | yes | The positional arguments |
| `kwargs` | `dict` | yes | The keyword arguments |
| `raised` | `BaseException` | no | Present when `.raises()` fired or spy raised |
| `returned` | `Any` | no | Present when spy delegated and real method returned |

The `args` and `kwargs` fields are always assertable. The `raised` and `returned` fields are assertable when present and must be included in assertions.
