# Writing Plugins

bigfoot's plugin system allows you to add interception for any type of interaction, not just HTTP or method calls. Custom plugins follow the `BasePlugin` abstract base class.

## BasePlugin contract

All plugins must subclass `BasePlugin` and implement eight abstract methods. The `__init__` method must call `super().__init__(verifier)`, which registers the plugin with the verifier.

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
