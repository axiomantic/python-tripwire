# CeleryPlugin Guide

`CeleryPlugin` intercepts `celery.app.task.Task.delay` and `celery.app.task.Task.apply_async` at the class level. Each task name and dispatch method combination has its own independent FIFO queue, so you can mock multiple dispatches of the same or different tasks and they are consumed in registration order.

## Installation

```bash
pip install bigfoot[celery]
```

This installs `celery`.

## Setup

In pytest, access `CeleryPlugin` through the `bigfoot.celery_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import bigfoot

def test_send_welcome_email():
    bigfoot.celery_mock.mock_delay(
        "myapp.tasks.send_email",
        returns=None,
    )

    with bigfoot:
        from myapp.tasks import send_email
        send_email.delay("user@example.com", "Welcome!")

    bigfoot.celery_mock.assert_delay(
        task_name="myapp.tasks.send_email",
        args=("user@example.com", "Welcome!"),
        kwargs={},
        options={},
    )
```

For manual use outside pytest, construct `CeleryPlugin` explicitly:

```python
from bigfoot import StrictVerifier
from bigfoot.plugins.celery_plugin import CeleryPlugin

verifier = StrictVerifier()
celery_mock = CeleryPlugin(verifier)
```

Each verifier may have at most one `CeleryPlugin`. A second `CeleryPlugin(verifier)` raises `ValueError`.

## Registering mocks

CeleryPlugin provides two mock registration methods, one for each dispatch method:

### `mock_delay(task_name, *, returns, ...)`

```python
bigfoot.celery_mock.mock_delay("myapp.tasks.process_order", returns=None)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `task_name` | `str` | required | Celery task name (e.g., `"myapp.tasks.process_order"`) |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### `mock_apply_async(task_name, *, returns, ...)`

```python
bigfoot.celery_mock.mock_apply_async("myapp.tasks.generate_report", returns=None)
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
    bigfoot.celery_mock.mock_delay("myapp.tasks.send_email", returns=None)
    bigfoot.celery_mock.mock_delay("myapp.tasks.send_email", returns=None)

    with bigfoot:
        from myapp.tasks import send_email
        send_email.delay("alice@example.com", "Hello Alice")
        send_email.delay("bob@example.com", "Hello Bob")

    bigfoot.celery_mock.assert_delay(
        task_name="myapp.tasks.send_email",
        args=("alice@example.com", "Hello Alice"),
        kwargs={},
        options={},
    )
    bigfoot.celery_mock.assert_delay(
        task_name="myapp.tasks.send_email",
        args=("bob@example.com", "Hello Bob"),
        kwargs={},
        options={},
    )
```

## Asserting interactions

Use the typed assertion helpers on `bigfoot.celery_mock`. All four fields (`task_name`, `args`, `kwargs`, `options`) are required:

### `assert_delay(task_name, args, kwargs, options)`

```python
bigfoot.celery_mock.assert_delay(
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
bigfoot.celery_mock.assert_apply_async(
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
import bigfoot

def test_celery_dispatch_error():
    bigfoot.celery_mock.mock_delay(
        "myapp.tasks.send_email",
        returns=None,
        raises=ConnectionError("Broker unavailable"),
    )

    with bigfoot:
        from myapp.tasks import send_email
        with pytest.raises(ConnectionError):
            send_email.delay("user@example.com", "Hello")

    bigfoot.celery_mock.assert_delay(
        task_name="myapp.tasks.send_email",
        args=("user@example.com", "Hello"),
        kwargs={},
        options={},
    )
```

## Full example

```python
import bigfoot

def enqueue_order_pipeline(order_id, user_email):
    from myapp.tasks import validate_order, charge_payment, send_confirmation
    validate_order.delay(order_id)
    charge_payment.apply_async(args=(order_id,), kwargs={"currency": "USD"}, countdown=5)
    send_confirmation.delay(user_email, order_id)

def test_enqueue_order_pipeline():
    bigfoot.celery_mock.mock_delay("myapp.tasks.validate_order", returns=None)
    bigfoot.celery_mock.mock_apply_async("myapp.tasks.charge_payment", returns=None)
    bigfoot.celery_mock.mock_delay("myapp.tasks.send_confirmation", returns=None)

    with bigfoot:
        enqueue_order_pipeline("order-42", "buyer@example.com")

    bigfoot.celery_mock.assert_delay(
        task_name="myapp.tasks.validate_order",
        args=("order-42",),
        kwargs={},
        options={},
    )
    bigfoot.celery_mock.assert_apply_async(
        task_name="myapp.tasks.charge_payment",
        args=("order-42",),
        kwargs={"currency": "USD"},
        options={"countdown": 5},
    )
    bigfoot.celery_mock.assert_delay(
        task_name="myapp.tasks.send_confirmation",
        args=("buyer@example.com", "order-42"),
        kwargs={},
        options={},
    )
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
bigfoot.celery_mock.mock_delay("myapp.tasks.update_metrics", returns=None, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls `delay()` or `apply_async()` on a task that has no remaining mocks in its queue, bigfoot raises `UnmockedInteractionError`:

```
celery.delay('myapp.tasks.send_email', ...) was called but no mock was registered.
Register a mock with:
    bigfoot.celery_mock.mock_delay('myapp.tasks.send_email', returns=...)
```
