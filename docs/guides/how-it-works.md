# How bigfoot Works

This guide explains bigfoot's architecture: how the sandbox intercepts external calls, how interactions are recorded and asserted, and how the plugin system ties it all together.

## The Three Guarantees

bigfoot enforces three rules that most mocking libraries leave silent:

1. **Every call must be pre-authorized.** If your code makes an external call with no registered mock, bigfoot raises `UnmockedInteractionError` immediately, not at teardown.
2. **Every recorded interaction must be explicitly asserted.** If an interaction is recorded but never asserted, bigfoot raises `UnassertedInteractionsError` at teardown.
3. **Every registered mock must actually be triggered.** If you register a mock that never fires, bigfoot raises `UnusedMocksError` at teardown.

Together, these guarantees mean that when a test passes, you know exactly what happened -- not just that nothing crashed.

## Sandbox Lifecycle

The core entry point is `with bigfoot:`, which creates a **sandbox** -- a controlled environment where all external calls are intercepted. Here is the exact sequence of events:

### Entering the sandbox

1. **ContextVar set.** The `_active_verifier` ContextVar is set to the current `StrictVerifier`. This is how interceptors know which verifier to route calls to.
2. **Plugin activation.** Each registered plugin's `activate()` method is called in registration order. This installs interceptors (monkeypatches) on the target libraries. If any plugin fails to activate, all previously activated plugins are deactivated and the error propagates.

```python
def _enter(self) -> StrictVerifier:
    self._token = _active_verifier.set(self._verifier)
    for plugin in self._verifier._plugins:
        plugin.activate()
    return self._verifier
```

3. **Your code runs.** Inside the `with` block, every external call hits an interceptor instead of the real implementation.

### Exiting the sandbox

4. **Plugin deactivation.** Each plugin's `deactivate()` method is called in reverse order, removing the monkeypatches.
5. **ContextVar reset.** The `_active_verifier` ContextVar is reset to its previous value.
6. **Assertions and verification.** After the sandbox exits, you make your assertions. At test teardown, `verify_all()` checks guarantees 2 and 3 -- any unasserted interactions or unused mocks cause failures.

The `SandboxContext` supports both `with` and `async with`, using the same `_enter()` and `_exit()` methods.

## Interception Model

bigfoot intercepts external calls through class-level monkeypatching. The design uses two key patterns: **module-level capture of originals** and **class-level reference counting**.

### Module-level capture of originals

When a plugin module is first imported, it captures references to the original (unpatched) methods at module scope. For example, the HTTP plugin does this:

```python
# Captured at import time, before any patches
_HTTPX_ORIGINAL_HANDLE = httpx.HTTPTransport.handle_request
_REQUESTS_ORIGINAL_SEND = requests.adapters.HTTPAdapter.send
```

These references serve two purposes: they are used by conflict detection to identify whether another library (like `respx` or `responses`) has already patched the same methods, and they are the values that get restored when the sandbox exits.

### Class-level reference counting

Plugins use class-level `_install_count` and `_install_lock` attributes to handle nested sandboxes correctly. The patches are installed on the first activation and removed only when the last sandbox exits:

```python
class HttpPlugin(BasePlugin):
    _install_count: int = 0
    _install_lock: threading.Lock = threading.Lock()

    def activate(self) -> None:
        with HttpPlugin._install_lock:
            if HttpPlugin._install_count == 0:
                self.check_conflicts()
                self.install_patches()
            HttpPlugin._install_count += 1

    def deactivate(self) -> None:
        with HttpPlugin._install_lock:
            HttpPlugin._install_count = max(0, HttpPlugin._install_count - 1)
            if HttpPlugin._install_count == 0:
                self.restore_patches()
```

This means patches are shared across all verifier instances. The reference count is class-level (not instance-level), so two concurrent sandboxes both using `HttpPlugin` share the same interceptors. The ContextVar routing (described next) ensures each intercepted call reaches the correct verifier.

## ContextVar Routing

The central question for any interceptor is: "which verifier should I report to?" bigfoot answers this with a `ContextVar`:

```python
_active_verifier: contextvars.ContextVar[StrictVerifier | None] = contextvars.ContextVar(
    "bigfoot_active_verifier", default=None
)
```

When an interceptor fires, it calls `get_verifier_or_raise(source_id)`, which reads the ContextVar and returns the active verifier. If no sandbox is active (the ContextVar is `None`), it raises `SandboxNotActiveError`.

### Why ContextVar?

Python's `contextvars.ContextVar` is both **thread-safe** and **async-safe**. Each thread gets its own value, and each `asyncio.Task` gets an independent copy. This means:

- Multiple threads can run separate sandboxes concurrently without interference.
- Multiple async tasks can run separate sandboxes concurrently without interference.
- No global mutable state, no locks needed for routing.

bigfoot uses three ContextVars:

| ContextVar | Purpose |
|---|---|
| `_active_verifier` | Points interceptors to the current verifier. Set on sandbox enter, reset on exit. |
| `_current_test_verifier` | Points module-level API functions (`bigfoot.mock()`, `bigfoot.assert_interaction()`) to the per-test verifier. Managed by the pytest fixture. |
| `_any_order_depth` | Tracks nesting depth of `in_any_order()` blocks. When > 0, assertions match in any order. |

## Timeline and Interactions

Every intercepted call is recorded as an `Interaction` on a shared `Timeline` owned by the `StrictVerifier`.

### The Interaction dataclass

```python
@dataclass
class Interaction:
    source_id: str           # e.g., "http:request" or "mock:db.query"
    sequence: int            # assigned atomically by Timeline.append()
    details: dict[str, Any]  # plugin-specific fields (method, url, args, etc.)
    plugin: BasePlugin       # the plugin that recorded this interaction
    _asserted: bool = False  # flipped to True by mark_asserted()
```

The `source_id` identifies which plugin and source produced the interaction (e.g., `"http:request"` for HTTP calls, `"mock:db.query"` for a mock named `db` with method `query`). The `details` dict holds the plugin-specific data that test authors assert against.

### Thread-safe sequence numbering

The `Timeline` uses a `threading.Lock` to assign monotonically increasing sequence numbers:

```python
class Timeline:
    def append(self, interaction: Interaction) -> None:
        with self._lock:
            interaction.sequence = self._sequence
            self._sequence += 1
            self._interactions.append(interaction)
```

Sequence numbers establish a total ordering of all interactions across all plugins. This ordering is what `assert_interaction()` checks by default -- assertions must match in the order interactions were recorded.

### Recording guard

The `BasePlugin.record()` method sets a `_recording_in_progress` ContextVar before appending to the timeline. If any code calls `Timeline.mark_asserted()` while recording is in progress, bigfoot raises `AutoAssertError`. This is a runtime guard against the auto-assert anti-pattern -- plugins must never mark interactions as asserted during recording.

## Mock Queues

Plugins use a FIFO queue pattern for mock configurations. When you register a mock, the configuration is appended to a queue. When an intercepted call matches, the first matching configuration is popped from the front of the queue.

For `MockPlugin`, each `MethodProxy` owns its own `deque[MockConfig]`:

```python
class MethodProxy:
    def __init__(self, ...):
        self._config_queue: deque[MockConfig] = deque()

    def returns(self, value: Any) -> MethodProxy:
        self._config_queue.append(MockConfig(..., side_effect=_ReturnValue(value)))
        return self
```

When the mock is called, the first config is consumed:

```python
config = self._config_queue.popleft()
```

If the queue is empty and no `wraps` object is configured, `UnmockedInteractionError` is raised. This enforces guarantee 1: every call must be pre-authorized.

The FIFO pattern means you can chain multiple configurations to handle sequential calls:

```python
db = bigfoot.mock("db")
db.query.returns(["row1"]).returns(["row2"])
# First call returns ["row1"], second returns ["row2"]
```

Side effects come in three flavors: `_ReturnValue` (return a value), `_RaiseException` (raise an exception), and `_CallFn` (call a function with the intercepted arguments).

## Assertion Model

Assertions happen in two phases: **inline assertions** during the test, and **teardown verification** at the end.

### Inline assertions: `assert_interaction()`

When you call `assert_interaction()` (or a plugin helper like `http.assert_request()`), bigfoot:

1. **Finds the next unasserted interaction** by peeking at the timeline. In normal mode, this is strictly the next unasserted interaction in sequence order. Inside an `in_any_order()` block, it searches all unasserted interactions for a match.

2. **Checks source_id.** The interaction's `source_id` must match the source argument's `source_id`.

3. **Enforces completeness.** The plugin's `assertable_fields()` method returns the set of fields that must appear in the assertion. Any missing field raises `MissingAssertionFieldsError`. By default, every key in `interaction.details` is assertable -- you cannot silently skip fields.

4. **Checks field values.** The plugin's `matches()` method compares expected values against actual values. If they do not match, `InteractionMismatchError` is raised with a detailed hint.

5. **Marks asserted.** If everything matches, the interaction is marked as asserted on the timeline.

Assertions are blocked inside the sandbox. Calling `assert_interaction()` while the sandbox is active raises `AssertionInsideSandboxError`. This enforces a clean separation: record inside the sandbox, assert outside.

### Teardown verification: `verify_all()`

At test teardown, `verify_all()` enforces guarantees 2 and 3:

- **Unasserted interactions**: any interaction still marked `_asserted=False` raises `UnassertedInteractionsError`.
- **Unused mocks**: each plugin's `get_unused_mocks()` is called. Any mock configuration that was never consumed raises `UnusedMocksError`.

If both violations exist, they are combined into a single `VerificationError` so you see all problems at once.

## Plugin Registry

bigfoot uses a registry to manage its built-in plugins and supports entry points for third-party plugins.

### Built-in registry

The `PLUGIN_REGISTRY` tuple in `_registry.py` lists every built-in plugin with its metadata:

```python
@dataclass(frozen=True)
class PluginEntry:
    name: str                # e.g., "http"
    import_path: str         # e.g., "bigfoot.plugins.http"
    class_name: str          # e.g., "HttpPlugin"
    availability_check: str  # dependency check strategy
    default_enabled: bool    # False for opt-in plugins
```

### Auto-discovery and availability

When a `StrictVerifier` is created, it calls `resolve_enabled_plugins()` to determine which plugins to instantiate. The resolution logic:

1. If `enabled_plugins` is set in config, only those plugins are loaded (allowlist).
2. If `disabled_plugins` is set, all default-enabled plugins are loaded except those (blocklist).
3. If neither is set, all default-enabled plugins whose dependencies are available are loaded.

Availability is checked via the `availability_check` field:

| Value | Meaning |
|---|---|
| `"always"` | No optional dependencies; always available |
| `"httpx+requests"` | Multiple modules; all must be importable |
| `"redis"` | Single module; must be importable |
| `"flag:module:attr"` | Read a boolean flag from a plugin module |

Plugins whose dependencies are not installed are silently skipped -- unless they were explicitly listed in `enabled_plugins`, which raises `BigfootConfigError`.

### Third-party plugins via entry points

After built-in plugins are loaded, bigfoot discovers third-party plugins registered under the `bigfoot.plugins` entry point group:

```python
for ep in entry_points(group="bigfoot.plugins"):
    plugin_cls = ep.load()
    plugin_cls(verifier)
```

This allows library authors to ship bigfoot plugins that activate automatically when installed.

### Deduplication

The `_register_plugin()` method on `StrictVerifier` silently skips duplicate plugin types. If a plugin class is registered both by the built-in registry and by an entry point, only the first instance is kept.

## pytest Integration

bigfoot ships as a pytest plugin, registered via the `bigfoot` entry point. It provides two fixtures:

### `_bigfoot_auto_verifier` (autouse)

This fixture runs automatically for every test. It:

1. Creates a fresh `StrictVerifier` (which auto-instantiates all enabled plugins).
2. Sets the `_current_test_verifier` ContextVar so module-level functions like `bigfoot.mock()` and `bigfoot.http.mock_response()` can find the verifier.
3. Yields the verifier to the test.
4. On teardown, resets the ContextVar and calls `verify_all()`.

```python
@pytest.fixture(autouse=True)
def _bigfoot_auto_verifier() -> Generator[StrictVerifier, None, None]:
    verifier = StrictVerifier()
    token = _current_test_verifier.set(verifier)
    yield verifier
    _current_test_verifier.reset(token)
    verifier.verify_all()
```

Because it is autouse, test authors do not need to request it. Every test gets a verifier, and every test gets `verify_all()` at teardown. If a test does not use bigfoot at all, `verify_all()` is a no-op (no interactions, no mocks, nothing to verify).

### `bigfoot_verifier` (explicit)

For tests that need direct access to the verifier instance (e.g., to manually construct plugins), the `bigfoot_verifier` fixture exposes the same verifier created by the autouse fixture.

### The sandbox is not automatic

The pytest plugin creates the verifier and runs verification, but it does **not** activate the sandbox automatically. The test author controls sandbox lifetime with `with bigfoot:` or `async with bigfoot:`. This is intentional: mock registration and assertions happen outside the sandbox, and only the code under test runs inside it.
