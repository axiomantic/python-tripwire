# Writing Plugins

> **Using pytest?** See [pytest integration](pytest-integration.md) for the standard `with tripwire:` pattern. The manual `StrictVerifier()` pattern below is for use outside pytest only.

> **Do not use `tripwire_verifier` fixture in your plugin fixtures.** Use `tripwire.current_verifier()` instead.

tripwire's plugin system allows you to add interception for any type of interaction, not just HTTP or method calls. Custom plugins follow the `BasePlugin` abstract base class.

## BasePlugin contract

All plugins must subclass `BasePlugin` and implement nine abstract methods. The `__init__` method must call `super().__init__(verifier)`, which registers the plugin with the verifier.

```python
from tripwire._base_plugin import BasePlugin
from tripwire._timeline import Interaction
from tripwire._verifier import StrictVerifier
```

## Lifecycle methods

`BasePlugin.activate()` and `BasePlugin.deactivate()` implement reference-counted patching. You do **not** override these methods. Instead, override the two hooks they call:

### install_patches()

```python
def install_patches(self) -> None: ...
```

Called once when the install count transitions from 0 to 1 (first activation). Install your monkeypatches here. Must be public (no underscore prefix).

### restore_patches()

```python
def restore_patches(self) -> None: ...
```

Called once when the install count reaches 0 (last deactivation). Restore original functions here. Must be public (no underscore prefix).

### Common mistakes

tripwire emits runtime warnings for two frequent plugin authoring errors:

1. **Overriding `activate()` or `deactivate()` directly.** These methods own the reference counting logic. Overriding them bypasses ref counting and breaks nested sandboxes. Override `install_patches()` and `restore_patches()` instead.

2. **Using private underscore names** (`_install_patches`, `_restore_patches`). `BasePlugin` calls `self.install_patches()`, not `self._install_patches()`. The private-name variant will never be called and your plugin will silently do nothing.

If `install_patches()` is not overridden when `activate()` runs, tripwire warns that the plugin may be misconfigured.

## Abstract methods

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

Return copy-pasteable code that would assert this specific interaction. This hint appears in `UnassertedInteractionsError` when a test forgets to assert a recorded interaction. The goal is a snippet the developer can copy directly into their test.

**If your plugin provides convenience assertion methods** (e.g., `assert_request`, `assert_command`, `assert_log`), the hint should show the convenience method, not raw `verifier.assert_interaction()`. Every built-in tripwire plugin follows this pattern. Convenience wrappers are easier to read, match the API the developer actually uses, and use the plugin's own parameter names rather than the internal `details` keys.

For example, `HttpPlugin.format_assert_hint()` returns:

```python
http.assert_request(
    "POST",
    "https://api.example.com/items",
    headers={'content-type': 'application/json'},
    body='{"name": "widget"}',
)
```

Not the lower-level `verifier.assert_interaction(http.request, method="POST", ...)` form.

**If your plugin does not provide convenience methods**, show `verifier.assert_interaction()` with the correct sentinel and field names so the developer can still copy and paste.

### assertable_fields()

```python
def assertable_fields(self, interaction: Interaction) -> frozenset[str]: ...
```

Return the set of `interaction.details` keys that callers MUST include in `assert_interaction(**expected)`. Any key returned here that is absent from the caller's `**expected` causes `assert_interaction()` to raise `MissingAssertionFieldsError` before any matching logic runs.

Implement to return only keys that carry meaningful signal. Do not include keys that are redundant with `source_id` (such as `mock_name` or `method_name` when the source already identifies the method). The goal is to prevent silent partial assertions, not to force callers to repeat information already encoded in the source.

For example, `MockPlugin` returns `frozenset({"args", "kwargs"})` because callers should not be able to assert a mock interaction without confirming what it was called with.

## Convenience assertion methods

Every plugin should provide typed assertion helper methods that wrap `verifier.assert_interaction()`. These methods are the primary assertion API for test authors: they use domain-specific parameter names, provide IDE autocompletion, and appear in `format_assert_hint()` error messages.

**Pattern:** Each convenience method calls `verifier.assert_interaction()` internally with the correct sentinel and field mapping:

```python
def assert_query(self, query: str) -> None:
    """Assert the next database query interaction.

    Convenience wrapper around verifier.assert_interaction().
    """
    from tripwire._context import _get_test_verifier_or_raise
    _get_test_verifier_or_raise().assert_interaction(self._sentinel, query=query)
```

**Guidelines:**

- Name methods `assert_<action>` (e.g., `assert_connect`, `assert_send`, `assert_command`)
- Accept the same fields returned by `assertable_fields()`, using domain-specific names
- Import `_get_test_verifier_or_raise` from `tripwire._context` to get the current verifier
- Update `format_assert_hint()` to show the convenience method, not `verifier.assert_interaction()`

All 14 built-in plugins follow this pattern. The raw `verifier.assert_interaction()` call still works and is documented as the low-level equivalent, but convenience methods are the recommended API.

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
from tripwire._base_plugin import BasePlugin
from tripwire._errors import UnmockedInteractionError
from tripwire._timeline import Interaction
from tripwire._verifier import StrictVerifier


class DbMockConfig:
    def __init__(self, query: str, result: Any, required: bool = True):
        self.query = query
        self.result = result
        self.required = required


class DbExecuteSentinel:
    """Opaque handle used as source filter in assert_interaction for db execute."""

    source_id = "db:execute"

    def __init__(self, plugin: "DatabasePlugin") -> None:
        self._plugin = plugin


class DatabasePlugin(BasePlugin):
    _install_count: int = 0
    _install_lock: threading.Lock = threading.Lock()
    _original_execute: Any = None

    def __init__(self, verifier: StrictVerifier, connection: Any) -> None:
        super().__init__(verifier)
        self._connection = connection
        self._mock_queue: list[DbMockConfig] = []
        self._sentinel = DbExecuteSentinel(self)

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

    def assert_query(self, query: str) -> None:
        """Assert the next database query interaction.

        Convenience wrapper around verifier.assert_interaction().
        """
        from tripwire._context import _get_test_verifier_or_raise
        _get_test_verifier_or_raise().assert_interaction(self._sentinel, query=query)

    def format_assert_hint(self, interaction: Interaction) -> str:
        query = interaction.details.get("query", "?")
        return f'db.assert_query(query={query!r})'

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

In pytest, use `tripwire.current_verifier()` to register the plugin against the autouse verifier:

```python
import tripwire

def test_db_query():
    db = DatabasePlugin(tripwire.current_verifier(), my_connection)
    db.mock_query("SELECT * FROM users", result=[{"id": 1}])

    with tripwire:
        rows = my_connection.execute("SELECT * FROM users")
        assert rows == [{"id": 1}]

    # Convenience wrapper -- recommended:
    db.assert_query(query="SELECT * FROM users")

    # Equivalent low-level call:
    # tripwire.assert_interaction(db_sentinel, query="SELECT * FROM users")

    # verify_all() called automatically at teardown
```

For manual use outside pytest:

```python
from tripwire import StrictVerifier

verifier = StrictVerifier()
db = DatabasePlugin(verifier, my_connection)
db.mock_query("SELECT * FROM users", result=[{"id": 1}])

with verifier.sandbox():
    rows = my_connection.execute("SELECT * FROM users")
    assert rows == [{"id": 1}]

# Convenience wrapper -- recommended:
db.assert_query(query="SELECT * FROM users")

# Equivalent low-level call:
# verifier.assert_interaction(db_sentinel, query="SELECT * FROM users")

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
handle = tripwire.socket.new_session()
handle.expect("connect", returns=None)
handle.expect("recv",    returns=b"pong")
handle.expect("close",   returns=None)
```

`new_session()` returns a `SessionHandle`. `expect()` appends one `ScriptStep` to the handle's FIFO script and returns the handle, so calls chain naturally:

```python
(tripwire.socket
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

State machine plugins require explicit assertion like all other plugins. Each scripted step records an `Interaction` on the timeline, and you must call the appropriate convenience assertion method (e.g., `assert_connect`, `assert_execute`) or `verifier.assert_interaction()` after the sandbox to verify every recorded interaction. `verify_all()` runs at teardown and will report any unasserted interactions as well as any `required=True` steps that were configured but never consumed.

### Minimal implementation example

```python
import threading
from typing import Any, ClassVar

from tripwire._state_machine_plugin import StateMachinePlugin
from tripwire._timeline import Interaction


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
        return f"    tripwire.ftp.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(self, source_id: str, args: tuple, kwargs: dict) -> str:
        method = source_id.split(":")[-1] if ":" in source_id else source_id
        return (
            f"ftp.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    tripwire.ftp.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"    ftp.assert_{method}(...)"

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

---

## Firewall mode support

Plugins declare whether they participate in firewall mode (formerly guard mode) via the `supports_guard` class variable.

### Default: `supports_guard = True`

The default value in `BasePlugin` is `True`. This means tripwire will activate the plugin at session startup during firewall mode, and any intercepted call outside a sandbox will be checked against the firewall rules (allow/deny/restrict with `M()` patterns). This is correct for any plugin that intercepts external I/O (HTTP, database, socket, etc.).

### Setting `supports_guard = False`

Set `supports_guard = False` for plugins that do not perform external I/O. Examples include `LoggingPlugin` (intercepts the `logging` module), `JwtPlugin` (JWT encode/decode), and `CryptoPlugin` (cryptographic operations). These plugins should only intercept when an explicit sandbox is active.

```python
from typing import ClassVar

class MyComputePlugin(BasePlugin):
    supports_guard: ClassVar[bool] = False
    # ...
```

### Constructing FirewallRequest objects

When a firewall-eligible plugin intercepts a call outside a sandbox, it should construct a protocol-specific `FirewallRequest` dataclass and pass it to the firewall for matching against `M()` patterns. Each protocol defines its own request type with fields relevant to that protocol:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class MyProtocolFirewallRequest:
    protocol: str = "myprotocol"
    host: str = ""
    port: int = 0
    operation: str = ""
```

Populate all available fields so that user-defined `M()` patterns can match precisely. The firewall checks these fields against the rules defined in TOML config, `@pytest.mark.allow`/`deny`, and `allow()`/`deny()`/`restrict()` context managers.

### Handling `GuardPassThrough` in interceptors

When firewall mode is active and a call is allowed (via `allow()`, `@pytest.mark.allow`, or an `M()` pattern match), `get_verifier_or_raise()` raises `GuardPassThrough` instead of returning a verifier. The allowlist can also be narrowed with `deny()`, `@pytest.mark.deny`, or `restrict()`. Firewall-eligible interceptors must catch `GuardPassThrough` and delegate to the original function:

```python
from tripwire import get_verifier_or_raise, GuardPassThrough

def _my_interceptor(original_self, *args, **kwargs):
    try:
        verifier = get_verifier_or_raise("myplugin:call")
    except GuardPassThrough:
        return MyPlugin._original_function(original_self, *args, **kwargs)
    # Normal interception logic follows...
    plugin = _find_my_plugin(verifier)
    return plugin._handle_call(original_self, *args, **kwargs)
```

`GuardPassThrough` inherits from `BaseException` (not `Exception`) so that generic `except Exception` clauses in user code do not accidentally swallow it. Only interceptors should catch it.

If your plugin has `supports_guard = False`, you do not need `GuardPassThrough` handling because firewall mode will never activate your plugin's interceptors.

---

## Using a coding assistant

If you use Claude Code or another AI coding assistant, tripwire includes a project skill at `.claude/skills/adding-plugins/SKILL.md` that automates the full plugin creation lifecycle: scaffolding, implementation, tests, documentation, and example creation. Invoke it with "add a plugin for X" or similar phrasing.

---

## 1st party vs 3rd party plugins

tripwire plugins don't depend on the libraries they intercept at install time. All library dependencies are optional extras (`pip install python-tripwire[http]`, `pip install python-tripwire[redis]`, etc.), so a 1st party plugin for any library costs nothing to users who don't install that extra. This means the usual "heavy dependencies" argument for splitting into a separate package doesn't apply.

### When to contribute a 1st party plugin

Most plugins should be 1st party (in-tree). Contribute directly to tripwire when:

- **The library is widely used.** If a meaningful percentage of Python projects use it, tripwire should support it out of the box. Examples: HTTP clients, database drivers, cloud SDKs, message queues, caching libraries.
- **Interception is complex.** Plugins that need ContextVar routing, class-level ref counting, reentrancy guards, or factory replacement patterns benefit from living alongside tripwire's internals where they can evolve together.
- **Layer coexistence matters.** If the plugin participates in tripwire's interception matrix (e.g., boto3 sits above HTTP, SSH sits above socket), coordinating `disabled_plugins` behavior is much easier in-tree.
- **You want the core team to maintain it.** 1st party plugins are tested in CI against every tripwire release.

### When to create a 3rd party plugin

Create a separate package when:

- **Independent release cycles are needed.** The target library changes its internals frequently and the plugin needs to release on its own schedule, decoupled from tripwire releases.
- **The plugin is domain-specific.** It targets an internal company SDK, a proprietary protocol, or a library used by a very small community.
- **The maintainer is outside the core team** and prefers to own the release process.

### Packaging a 3rd party plugin

A 3rd party plugin is a standard Python package that depends on `python-tripwire`. Users install it alongside tripwire:

```bash
pip install python-tripwire tripwire-myservice
```

**Project structure:**

```
tripwire-myservice/
├── pyproject.toml
├── src/
│   └── tripwire_myservice/
│       ├── __init__.py      # exports proxy, plugin class
│       └── plugin.py        # MyServicePlugin(BasePlugin)
└── tests/
    └── test_plugin.py
```

**Entry point for auto-discovery:**

Register your plugin using the `tripwire.plugins` entry point group so tripwire discovers and activates it automatically when installed:

```toml
# In your package's pyproject.toml
[project.entry-points."tripwire.plugins"]
myservice = "tripwire_myservice.plugin:MyServicePlugin"
```

With this entry point, users don't need to manually register the plugin. Installing the package is enough -- tripwire's `StrictVerifier` discovers entry-point plugins alongside built-in ones.

**Key points:**

- Subclass `BasePlugin` or `StateMachinePlugin` from `tripwire`
- Follow the same conventions as built-in plugins (sentinel proxies, FIFO queues, `assertable_fields` returning `frozenset(interaction.details.keys())`)
- Test against tripwire's public API only; don't depend on private internals that may change
