# LoggingPlugin Guide

`LoggingPlugin` intercepts Python's `logging` module globally during a sandbox. It is included in core tripwire -- no extra required.

## Setup

In pytest, access `LoggingPlugin` through the `tripwire.log_mock` proxy. It auto-creates the plugin for the current test on first use -- no explicit instantiation needed:

```python
import tripwire
import logging

def test_audit_trail():
    logger = logging.getLogger("myapp.auth")

    with tripwire:
        logger.info("User logged in")

    tripwire.log_mock.assert_info("User logged in", "myapp.auth")
```

For manual use outside pytest, construct `LoggingPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.logging_plugin import LoggingPlugin

verifier = StrictVerifier()
lp = LoggingPlugin(verifier)
```

Each verifier may have at most one `LoggingPlugin`. A second `LoggingPlugin(verifier)` is silently ignored (same instance reused).

## Fire-and-forget behavior

All log calls inside a sandbox are **swallowed** (not actually emitted to handlers) and **recorded on the timeline**. This means:

1. No log output is produced during the sandbox (no console spam in tests).
2. Every log call must be explicitly asserted at teardown, or `UnassertedInteractionsError` is raised.

This is the same pattern used by `shutil.which` in `SubprocessPlugin`: unmocked calls are silently recorded rather than raising `UnmockedInteractionError`.

## Registering log mocks

Use `tripwire.log_mock.mock_log(level, message, logger_name=None)` to register expected log calls before entering the sandbox:

```python
tripwire.log_mock.mock_log("INFO", "User logged in", logger_name="myapp.auth")
```

Parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `level` | `str` | required | Log level name: `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"` |
| `message` | `str` | required | The formatted log message to expect |
| `logger_name` | `str \| None` | `None` | Logger name to match; `None` matches any logger |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

Mocks are consumed in FIFO order. If a log call matches the next mock in the queue (level, message, and optionally logger_name), the mock is consumed. Unmatched calls are still recorded on the timeline.

## Asserting log interactions

Use `tripwire.log_mock.log` as the source in `assert_interaction()`, or use the typed assertion helpers.

### Using assert_interaction directly

```python
tripwire.assert_interaction(
    tripwire.log_mock.log,
    level="INFO",
    message="User logged in",
    logger_name="myapp.auth",
)
```

All three fields (`level`, `message`, `logger_name`) are required.

### Using assertion helpers

```python
tripwire.log_mock.assert_log("INFO", "User logged in", "myapp.auth")
tripwire.log_mock.assert_info("User logged in", "myapp.auth")
tripwire.log_mock.assert_warning("Rate limit approaching", "myapp.auth")
```

Available helpers:

| Method | Description |
|---|---|
| `assert_log(level, message, logger_name)` | Assert the next log interaction (all 3 fields) |
| `assert_debug(message, logger_name)` | Convenience for `assert_log("DEBUG", ...)` |
| `assert_info(message, logger_name)` | Convenience for `assert_log("INFO", ...)` |
| `assert_warning(message, logger_name)` | Convenience for `assert_log("WARNING", ...)` |
| `assert_error(message, logger_name)` | Convenience for `assert_log("ERROR", ...)` |
| `assert_critical(message, logger_name)` | Convenience for `assert_log("CRITICAL", ...)` |

## Message formatting

Log messages with `%`-style arguments are formatted before recording:

```python
logger.info("User %s logged in from %s", "alice", "192.168.1.1")
# Recorded as: message="User alice logged in from 192.168.1.1"
```

Assert against the fully formatted message, not the template.

## Multiple loggers

Different logger names are recorded as-is. You can assert interactions from multiple loggers:

```python
import tripwire
import logging

def test_multi_service():
    auth_logger = logging.getLogger("service.auth")
    payment_logger = logging.getLogger("service.payment")

    with tripwire:
        auth_logger.info("authenticated")
        payment_logger.warning("rate limited")

    tripwire.log_mock.assert_info("authenticated", "service.auth")
    tripwire.log_mock.assert_warning("rate limited", "service.payment")
```

## ConflictError

At sandbox entry, `LoggingPlugin` checks whether `logging.Logger._log` has already been patched by another library. If it has been modified by a third party, tripwire raises `ConflictError`:

```
ConflictError: target='logging.Logger._log', patcher='unknown'
```

Nested tripwire sandboxes use reference counting and do not conflict with each other.

## Full example

**Production code** (`examples/logging_example/app.py`):

```python
--8<-- "examples/logging_example/app.py"
```

**Test** (`examples/logging_example/test_app.py`):

```python
--8<-- "examples/logging_example/test_app.py"
```
