# CeleryPlugin Guide

`CeleryPlugin` intercepts `celery.app.task.Task.delay` and `celery.app.task.Task.apply_async` at the class level. Each task name and dispatch method combination has its own independent FIFO queue, so you can mock multiple dispatches of the same or different tasks and they are consumed in registration order.

## Installation

```bash
pip install pytest-tripwire[celery]
```

This installs `celery`.

## Setup

In pytest, access `CeleryPlugin` through the `tripwire.celery` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_send_welcome_email():
    tripwire.celery.mock_delay(
        "myapp.tasks.send_email",
        returns=None,
    )

    with tripwire:
        from myapp.tasks import send_email
        send_email.delay("user@example.com", "Welcome!")

    tripwire.celery.assert_delay(
        task_name="myapp.tasks.send_email",
        args=("user@example.com", "Welcome!"),
        kwargs={},
        options={},
    )
```

For manual use outside pytest, construct `CeleryPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.celery_plugin import CeleryPlugin

verifier = StrictVerifier()
celery = CeleryPlugin(verifier)
```

Each verifier may have at most one `CeleryPlugin`. A second `CeleryPlugin(verifier)` raises `ValueError`.

## Registering mocks

CeleryPlugin provides two mock registration methods, one for each dispatch method:

### `mock_delay(task_name, *, returns, ...)`

```python
tripwire.celery.mock_delay("myapp.tasks.process_order", returns=None)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `task_name` | `str` | required | Celery task name (e.g., `"myapp.tasks.process_order"`) |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_apply_async(task_name, *, returns, ...)`

```python
tripwire.celery.mock_apply_async("myapp.tasks.generate_report", returns=None)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `task_name` | `str` | required | Celery task name (e.g., `"myapp.tasks.generate_report"`) |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

## FIFO queues

Each task_name:dispatch_method pair has its own independent FIFO queue. Multiple `mock_delay("myapp.tasks.send_email", ...)` calls are consumed in registration order:

```python
def test_multiple_email_dispatches():
    tripwire.celery.mock_delay("myapp.tasks.send_email", returns=None)
    tripwire.celery.mock_delay("myapp.tasks.send_email", returns=None)

    with tripwire:
        from myapp.tasks import send_email
        send_email.delay("alice@example.com", "Hello Alice")
        send_email.delay("bob@example.com", "Hello Bob")

    tripwire.celery.assert_delay(
        task_name="myapp.tasks.send_email",
        args=("alice@example.com", "Hello Alice"),
        kwargs={},
        options={},
    )
    tripwire.celery.assert_delay(
        task_name="myapp.tasks.send_email",
        args=("bob@example.com", "Hello Bob"),
        kwargs={},
        options={},
    )
```

## Asserting interactions

Use the typed assertion helpers on `tripwire.celery`. All four fields (`task_name`, `args`, `kwargs`, `options`) are required:

### `assert_delay(task_name, args, kwargs, options)`

```python
tripwire.celery.assert_delay(
    task_name="myapp.tasks.send_email",
    args=("user@example.com", "Welcome!"),
    kwargs={},
    options={},
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `task_name` | `str` | required | Celery task name |
| `args` | `tuple` | required | Positional arguments passed to `delay()` |
| `kwargs` | `dict` | required | Keyword arguments passed to `delay()` |
| `options` | `dict` | required | Dispatch options (always `{}` for `delay`) |

### `assert_apply_async(task_name, args, kwargs, options)`

```python
tripwire.celery.assert_apply_async(
    task_name="myapp.tasks.generate_report",
    args=("q1", 2024),
    kwargs={"format": "pdf"},
    options={"queue": "reports", "countdown": 60},
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `task_name` | `str` | required | Celery task name |
| `args` | `tuple` | required | Positional arguments passed via `args=` |
| `kwargs` | `dict` | required | Keyword arguments passed via `kwargs=` |
| `options` | `dict` | required | Dispatch options (`task_id`, `queue`, `countdown`, `link`, etc.) |

## Simulating errors

Use the `raises` parameter to simulate Celery dispatch failures:

```python
import tripwire

def test_celery_dispatch_error():
    tripwire.celery.mock_delay(
        "myapp.tasks.send_email",
        returns=None,
        raises=ConnectionError("Broker unavailable"),
    )

    with tripwire:
        from myapp.tasks import send_email
        with pytest.raises(ConnectionError):
            send_email.delay("user@example.com", "Hello")

    tripwire.celery.assert_delay(
        task_name="myapp.tasks.send_email",
        args=("user@example.com", "Hello"),
        kwargs={},
        options={},
    )
```

## Full example

**Production code** (`examples/celery_tasks/app.py`):

```python
--8<-- "examples/celery_tasks/app.py"
```

**Test** (`examples/celery_tasks/test_app.py`):

```python
--8<-- "examples/celery_tasks/test_app.py"
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.celery.mock_delay("myapp.tasks.update_metrics", returns=None, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls `delay()` or `apply_async()` on a task that has no remaining mocks in its queue, tripwire raises `UnmockedInteractionError`:

```
celery.delay('myapp.tasks.send_email', ...) was called but no mock was registered.
Register a mock with:
    tripwire.celery.mock_delay('myapp.tasks.send_email', returns=...)
```
