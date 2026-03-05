# Writing Plugins

bigfoot's plugin system allows you to add interception for any type of interaction, not just HTTP or method calls. Custom plugins follow the `BasePlugin` abstract base class.

## BasePlugin contract

All plugins must subclass `BasePlugin` and implement ten abstract methods. The `__init__` method must call `super().__init__(verifier)`, which registers the plugin with the verifier.

```python
from bigfoot._base_plugin import BasePlugin
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
```

## Abstract methods

### activate()

```python
def activate(self) -> None: ...
```

Called when the sandbox is entered. Install your interceptors here. Must be thread-safe. Use class-level reference counting (increment a `_install_count` under a `_install_lock`) so nested sandboxes work correctly. Only install if count transitions from 0 to 1.

Check for conflicts before installing. If another library has already patched your target, raise `ConflictError`.

### deactivate()

```python
def deactivate(self) -> None: ...
```

Called when the sandbox exits. Remove interceptors and decrement the count. Only restore originals if the count reaches 0. Must not raise; collect errors for the caller to raise after ContextVar reset.

### matches()

```python
def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool: ...
```

Called by `assert_interaction()` to check whether an interaction matches the expected fields. Return `True` if all expected key-value pairs are satisfied. Must never raise; catch exceptions and return `False`.

### format_interaction()

```python
def format_interaction(self, interaction: Interaction) -> str: ...
```

Return a one-line human-readable description of the interaction for error messages and the remaining-timeline display. Example: `"[HttpPlugin] POST https://api.example.com/v1"`.

### format_mock_hint()

```python
def format_mock_hint(self, interaction: Interaction) -> str: ...
```

Return copy-pasteable code that would configure a mock for this interaction. Used in `UnassertedInteractionsError` hints to guide the developer.

### format_unmocked_hint()

```python
def format_unmocked_hint(self, source_id: str, args: tuple, kwargs: dict) -> str: ...
```

Return copy-pasteable code for mocking a call that fired before reaching the timeline (i.e., the queue was empty or no mock matched). Used in `UnmockedInteractionError` hints.

### format_assert_hint()

```python
def format_assert_hint(self, interaction: Interaction) -> str: ...
```

Return copy-pasteable code that would assert this specific interaction. Used in `UnassertedInteractionsError` hints.

### assertable_fields()

```python
def assertable_fields(self, interaction: Interaction) -> frozenset[str]: ...
```

Return the set of `interaction.details` keys that callers MUST include in `assert_interaction(**expected)`. Any key returned here that is absent from the caller's `**expected` causes `assert_interaction()` to raise `MissingAssertionFieldsError` before any matching logic runs.

Implement to return only keys that carry meaningful signal. Do not include keys that are redundant with `source_id` (such as `mock_name` or `method_name` when the source already identifies the method). The goal is to prevent silent partial assertions, not to force callers to repeat information already encoded in the source.

For example, `MockPlugin` returns `frozenset({"args", "kwargs"})` because callers should not be able to assert a mock interaction without confirming what it was called with.

### get_unused_mocks()

```python
def get_unused_mocks(self) -> list[Any]: ...
```

Return all mock configuration objects that are `required=True` and were never consumed. `verify_all()` iterates plugins and calls this method.

### format_unused_mock_hint()

```python
def format_unused_mock_hint(self, mock_config: object) -> str: ...
```

Return a hint string for one unused mock. Typically includes the registration traceback and instructions to either remove the mock or mark it `required=False`.

## The record() method (concrete)

`BasePlugin` provides a concrete `record()` method that appends an `Interaction` to the verifier's shared timeline:

```python
def record(self, interaction: Interaction) -> None:
    self.verifier._timeline.append(interaction)
```

Call `self.record(interaction)` from your interceptor after a call fires.

## Interaction dataclass fields

| Field | Type | Description |
|---|---|---|
| `source_id` | `str` | Unique identifier for this interceptor (e.g., `"mock:Name.method"`, `"http:request"`) |
| `sequence` | `int` | Assigned atomically by `Timeline.append()`. Set to `0` before recording. |
| `details` | `dict[str, Any]` | Plugin-specific data; queried by `matches()` and `format_*()` methods |
| `plugin` | `BasePlugin` | Reference to the plugin that recorded this interaction |

## Minimal example: a database plugin

```python
import threading
from typing import Any
from bigfoot._base_plugin import BasePlugin
from bigfoot._errors import UnmockedInteractionError
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier


class DbMockConfig:
    def __init__(self, query: str, result: Any, required: bool = True):
        self.query = query
        self.result = result
        self.required = required


class DatabasePlugin(BasePlugin):
    _install_count: int = 0
    _install_lock: threading.Lock = threading.Lock()
    _original_execute: Any = None

    def __init__(self, verifier: StrictVerifier, connection: Any) -> None:
        super().__init__(verifier)
        self._connection = connection
        self._mock_queue: list[DbMockConfig] = []

    def mock_query(self, query: str, result: Any, required: bool = True) -> None:
        self._mock_queue.append(DbMockConfig(query=query, result=result, required=required))

    def activate(self) -> None:
        with DatabasePlugin._install_lock:
            if DatabasePlugin._install_count == 0:
                DatabasePlugin._original_execute = self._connection.__class__.execute
                plugin_ref = self

                def _interceptor(conn_self: Any, query: str, *args: Any, **kwargs: Any) -> Any:
                    config = next(
                        (c for c in plugin_ref._mock_queue if c.query == query), None
                    )
                    if config is None:
                        hint = plugin_ref.format_unmocked_hint("db:execute", (query,), {})
                        raise UnmockedInteractionError(
                            source_id="db:execute", args=(query,), kwargs={}, hint=hint
                        )
                    plugin_ref._mock_queue.remove(config)
                    interaction = Interaction(
                        source_id="db:execute",
                        sequence=0,
                        details={"query": query},
                        plugin=plugin_ref,
                    )
                    plugin_ref.record(interaction)
                    return config.result

                self._connection.__class__.execute = _interceptor
            DatabasePlugin._install_count += 1

    def deactivate(self) -> None:
        with DatabasePlugin._install_lock:
            DatabasePlugin._install_count = max(0, DatabasePlugin._install_count - 1)
            if DatabasePlugin._install_count == 0 and DatabasePlugin._original_execute is not None:
                self._connection.__class__.execute = DatabasePlugin._original_execute
                DatabasePlugin._original_execute = None

    def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
        try:
            return all(interaction.details.get(k) == v for k, v in expected.items())
        except Exception:
            return False

    def format_interaction(self, interaction: Interaction) -> str:
        return f"[DatabasePlugin] execute: {interaction.details.get('query', '?')}"

    def format_mock_hint(self, interaction: Interaction) -> str:
        query = interaction.details.get("query", "SELECT ...")
        return f'db.mock_query("{query}", result=[...])'

    def format_unmocked_hint(self, source_id: str, args: tuple, kwargs: dict) -> str:
        query = args[0] if args else "SELECT ..."
        return (
            f"Unexpected DB query: {query}\n\n"
            f"  To mock this query, add before your sandbox:\n"
            f'    db.mock_query("{query}", result=[...])'
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        query = interaction.details.get("query", "?")
        return f'verifier.assert_interaction(db_sentinel, query="{query}")'

    def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
        return frozenset({"query"})

    def get_unused_mocks(self) -> list[DbMockConfig]:
        return [c for c in self._mock_queue if c.required]

    def format_unused_mock_hint(self, mock_config: object) -> str:
        assert isinstance(mock_config, DbMockConfig)
        return (
            f"db:execute query={mock_config.query!r} was registered but never called.\n"
            f"  - Remove this mock if it's not needed\n"
            f'  - Mark it optional: db.mock_query("{mock_config.query}", ..., required=False)'
        )
```

## Registering and using the plugin

In pytest, use `bigfoot.current_verifier()` to register the plugin against the autouse verifier:

```python
import bigfoot

def test_db_query():
    db = DatabasePlugin(bigfoot.current_verifier(), my_connection)
    db.mock_query("SELECT * FROM users", result=[{"id": 1}])

    with bigfoot.sandbox():
        rows = my_connection.execute("SELECT * FROM users")
        assert rows == [{"id": 1}]

    # query= is the sole assertable field for DatabasePlugin
    bigfoot.assert_interaction(db_sentinel, query="SELECT * FROM users")
    # verify_all() called automatically at teardown
```

For manual use outside pytest:

```python
from bigfoot import StrictVerifier

verifier = StrictVerifier()
db = DatabasePlugin(verifier, my_connection)
db.mock_query("SELECT * FROM users", result=[{"id": 1}])

with verifier.sandbox():
    rows = my_connection.execute("SELECT * FROM users")
    assert rows == [{"id": 1}]

verifier.assert_interaction(db_sentinel, query="SELECT * FROM users")
verifier.verify_all()
```

---

## StateMachinePlugin

Use `StateMachinePlugin` when the protocol your plugin models has a defined sequence of states — a connection that must be established before messages can flow, and closed before the object is discarded. Use `BasePlugin` directly when calls are stateless (HTTP requests, Redis GET/SET, arbitrary method mocks that carry no ordering constraint).

### When to choose StateMachinePlugin

| Situation | Base class |
|---|---|
| Socket (connect → send/recv → close) | `StateMachinePlugin` |
| Database (connect → execute → commit → close) | `StateMachinePlugin` |
| WebSocket (open → send/recv → close) | `StateMachinePlugin` |
| SMTP (connect → ehlo → login → sendmail → quit) | `StateMachinePlugin` |
| HTTP request/response cycle | `BasePlugin` |
| Redis commands (GET, SET, DEL — stateless) | `BasePlugin` |
| Generic method mock | `BasePlugin` via `MockPlugin` |

`StateMachinePlugin` enforces that method calls happen from the correct state. Calling `recv` before `connect` raises `InvalidStateError` immediately, making bugs visible at the call site rather than as mysterious data corruption later.

### Abstract methods

`StateMachinePlugin` requires seven abstract methods from `BasePlugin` (`activate`, `deactivate`, `format_interaction`, `format_mock_hint`, `format_unmocked_hint`, `format_assert_hint`, and `format_unused_mock_hint`) plus three of its own:

#### `_initial_state(self) -> str`

Return the name of the state a fresh connection starts in.

```python
def _initial_state(self) -> str:
    return "disconnected"
```

#### `_transitions(self) -> dict[str, dict[str, str]]`

Return the full transition table as a nested dict:

```python
{method_name: {from_state: to_state, ...}, ...}
```

A method may appear in multiple from-states (for example, a `close` that is valid from either `connected` or `in_transaction`). A method that stays in the same state (like `send` while connected) uses `{current: current}` as the from/to pair.

```python
def _transitions(self) -> dict[str, dict[str, str]]:
    return {
        "connect":  {"disconnected": "connected"},
        "send":     {"connected": "connected"},
        "recv":     {"connected": "connected"},
        "close":    {"connected": "closed"},
    }
```

#### `_unmocked_source_id(self) -> str`

Return the source ID string reported in `UnmockedInteractionError` when `new_session()` has not been called before a connection attempt. Conventionally matches the "entry point" interceptor.

```python
def _unmocked_source_id(self) -> str:
    return "myprotocol:connect"
```

### Session scripting API

Before the sandbox runs, register one session per expected connection:

```python
handle = bigfoot.socket_mock.new_session()
handle.expect("connect", returns=None)
handle.expect("recv",    returns=b"pong")
handle.expect("close",   returns=None)
```

`new_session()` returns a `SessionHandle`. `expect()` appends one `ScriptStep` to the handle's FIFO script and returns the handle, so calls chain naturally:

```python
(bigfoot.socket_mock
    .new_session()
    .expect("connect", returns=None)
    .expect("send",    returns=4)
    .expect("recv",    returns=b"pong")
    .expect("close",   returns=None))
```

`expect` parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | Method name, must match a key in `_transitions()` |
| `returns` | `Any` | required | Value returned when this step executes |
| `raises` | `BaseException \| None` | `None` | Exception raised instead of returning |
| `required` | `bool` | `True` | When `True`, teardown reports the step as unused if never consumed |

Sessions are consumed in FIFO order. The first call to the connection entry point (e.g., `socket.connect()`) pops the first queued `SessionHandle` and binds it to that connection object. All subsequent method calls on the same connection object consume steps from that handle in order.

### Interaction recording and assertions

State machine plugins auto-assert every interaction at the time it is recorded. You do not call `bigfoot.assert_interaction()` for stateful plugins — there is nothing to assert after the sandbox. `verify_all()` still runs at teardown and will report any `required=True` steps that were configured but never consumed.

### Minimal implementation example

```python
import threading
from typing import Any, ClassVar

from bigfoot._state_machine_plugin import StateMachinePlugin
from bigfoot._timeline import Interaction


class FtpPlugin(StateMachinePlugin):
    """Mock plugin for a simple two-state FTP-like protocol."""

    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()
    _original_connect: ClassVar[Any] = None

    # -- StateMachinePlugin abstract methods --------------------------------

    def _initial_state(self) -> str:
        return "disconnected"

    def _transitions(self) -> dict[str, dict[str, str]]:
        return {
            "connect":  {"disconnected": "connected"},
            "list":     {"connected": "connected"},
            "get":      {"connected": "connected"},
            "put":      {"connected": "connected"},
            "quit":     {"connected": "closed"},
        }

    def _unmocked_source_id(self) -> str:
        return "ftp:connect"

    # -- BasePlugin lifecycle -----------------------------------------------

    def activate(self) -> None:
        with FtpPlugin._install_lock:
            if FtpPlugin._install_count == 0:
                import ftplib
                FtpPlugin._original_connect = ftplib.FTP.connect
                # ... install interceptors ...
            FtpPlugin._install_count += 1

    def deactivate(self) -> None:
        with FtpPlugin._install_lock:
            FtpPlugin._install_count = max(0, FtpPlugin._install_count - 1)
            if FtpPlugin._install_count == 0 and FtpPlugin._original_connect is not None:
                import ftplib
                ftplib.FTP.connect = FtpPlugin._original_connect
                FtpPlugin._original_connect = None

    # -- BasePlugin format methods ------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"[FtpPlugin] ftp.{method}(...)"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"    bigfoot.ftp_mock.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(self, source_id: str, args: tuple, kwargs: dict) -> str:
        method = source_id.split(":")[-1] if ":" in source_id else source_id
        return (
            f"ftp.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.ftp_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"    # ftp_mock: session step '{method}' recorded (state-machine, auto-asserted)"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        method = getattr(mock_config, "method", "?")
        tb = getattr(mock_config, "registration_traceback", "")
        return (
            f"ftp.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )
```

Key implementation rules:

- Capture originals at **import time** (module-level constants) or store them in class variables only after the first `activate()`. Never look up originals inside an interceptor function.
- Increment `_install_count` **after** installing patches; decrement **before** restoring them (or in a pattern where the decrement and restore are atomic under the lock).
- Call `_bind_connection(conn_obj)` at the connection entry point (e.g., inside the patched `connect()` method) to pop the next queued session.
- Call `_lookup_session(conn_obj)` inside each subsequent method interceptor to retrieve the bound session.
- Call `_release_session(conn_obj)` at the close/quit/disconnect point to free the session slot.
- Call `_execute_step(handle, method, args, kwargs, source_id)` to validate the state transition, pop the next script step, advance the state, record the interaction, and return the configured value.
