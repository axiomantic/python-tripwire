# Adding Plugins to tripwire

Use when: the user wants to create a new tripwire plugin, says "add a plugin", "write a plugin", "I want a [X] plugin", or describes a library/service they want tripwire to intercept.

## Overview

This skill guides the complete lifecycle of adding a new plugin to tripwire: discovery, architecture classification, TDD implementation, integration (registry, proxy, `__init__.py`), documentation, examples, and README updates. It works standalone or integrates with the `develop` skill if available.

---

## Phase 1: Plugin Discovery

Gather the information needed to design the plugin. Ask these questions interactively (or extract answers from the user's initial prompt if they provided enough detail).

### 1.1 Library Identity

Ask:
- **What Python library does this plugin intercept?** (e.g., `redis`, `pymongo`, `pika`)
- **What's the pip install name vs the import name?** (e.g., `grpcio` installs but imports as `grpc`; `pymongo` installs and imports as `pymongo`)
- **Is it a stdlib module or an optional dependency?** Stdlib modules use `availability_check="always"`. Optional deps use `availability_check="<import_name>"`.
- **Is it a C extension or pure Python?** C extensions may resist monkeypatching. If uncertain, run a patchability investigation (see Phase 2.5).

### 1.2 Plugin Architecture Classification

Ask:
- **Does the library have a connection lifecycle?** (connect -> use -> close pattern)
  - **Yes -> StateMachinePlugin** (like pika, paramiko, SMTP)
  - **No -> BasePlugin** (like Redis, MongoDB, memcache, DNS)
- **What operations need interception?** List every method/function the plugin should capture. For each one:
  - What arguments are meaningful for test authors to assert? (These become `interaction.details` fields)
  - What arguments are infrastructure noise? (timeouts, retry config -- exclude from details)
  - What arguments contain sensitive data? (passwords, tokens -- MUST NOT go in details)
  - What is the return type? (Determines `returns` field on MockConfig)

### 1.3 Interception Strategy

Determine based on library type:
- **Direct monkeypatching** (most common): Replace class methods at class level. Works for pure Python libraries.
- **Factory replacement**: Replace factory functions that return objects (like `grpc.insecure_channel`). Use when instances are C objects that resist instance-level patching.
- **Proxy objects**: Replace loaded libraries/modules with proxy objects (like ctypes `CdllProxy`). Use when attribute access itself needs interception.

If the library might be a C extension, add a **patchability investigation step** before implementation (see Phase 2.5).

### 1.4 State Machine Design (StateMachinePlugin only)

If StateMachinePlugin was chosen:
- **What are the states?** (e.g., `disconnected`, `connected`, `channel_open`, `closed`)
- **What are the valid transitions?** Map each (state, step) -> next_state
- **Which steps are state-transition-only?** (e.g., `close`, `channel`) These return `frozenset()` from `assertable_fields()` with documented reason: "state-transition-only step with no meaningful parameters"
- **What fake classes are needed?** (e.g., `_FakeBlockingConnection`, `_FakeChannel`)

### 1.5 Integration Context

Ask:
- **Does this library make calls that another tripwire plugin already intercepts?** (e.g., boto3 uses HTTP, elasticsearch uses HTTP, gRPC uses HTTP)
  - If yes, document the layering. Users typically disable the lower-level plugin.
- **Should this plugin be default-enabled?** Most are. Set `default_enabled=False` only for plugins that are too broad (file I/O) or too specialized (ctypes/cffi).
- **What pyproject.toml extra name should it use?** Usually matches the registry name. Check existing extras in `pyproject.toml` for conventions.

### 1.6 Test Planning

Ask:
- **What are the library-specific edge cases?** (streaming, callbacks, connection pooling, retries, etc.)
- **Are there multiple modes of operation?** (sync vs async, ABI vs API mode, blocking vs non-blocking)
- **What error types does the real library raise?** (for exception propagation tests)
- **Does the library have global state that needs cleanup between tests?**

### 1.7 Documentation Context

Ask:
- **What's a realistic 5-10 line production code example using this library?** This becomes the basis for the documentation guide.
- **What are the most common use cases a developer would want to test?**
- **Are there common gotchas when testing code that uses this library?**

---

## Phase 2: Architecture Decision Record

Before writing code, produce a concise summary of the design decisions. This serves as the implementation spec.

```
Plugin Name: [name]
Registry Name: [e.g., "redis", "mongo", "pika"]
Plugin Class: [e.g., "RedisPlugin", "MongoPlugin"]
Base Class: [BasePlugin | StateMachinePlugin]
File: src/tripwire/plugins/[name]_plugin.py
Test File: tests/unit/test_[name]_plugin.py

Import Name: [Python import, e.g., "redis", "pymongo"]
Pip Name: [pip package, e.g., "redis", "pymongo"]
Availability Check: [e.g., "always", "redis", "pymongo", "grpc"]
Default Enabled: [True | False]

Intercepted Methods:
  - [class.method]: details=[field1, field2, ...], returns=[type]
  - [class.method]: details=[field1, field2, ...], returns=[type]

MockConfig Fields: [operation/command/method], returns, raises, required, registration_traceback
Sentinel: _[Name]Sentinel with source_id = "[prefix]:[operation]"
FIFO Queue Key: [e.g., operation name, f"{library}:{function}"]

Typed Assertion Helpers:
  - assert_[operation](field1, field2, ...)

Proxy Singleton: [name]_mock = _[Name]Proxy()
__all__ Additions: "[ClassName]", "[name]_mock"

State Machine (if applicable):
  States: [list]
  Transitions: [state] + [step] -> [state]
  State-transition-only steps: [list, with documented reason]
  Fake classes: [list]

Layering: [conflicts with other plugins, if any]
```

### Phase 2.5: Patchability Investigation (if needed)

If the library might be a C extension:

1. Check if methods can be patched at the instance level
2. Check if methods can be patched at the class level
3. If neither works, determine factory replacement or proxy strategy
4. Document findings before proceeding to implementation

```python
# Investigation template
import [library]
obj = [library].SomeClass(...)
print(type(obj))
print(type(obj).__mro__)

# Try instance-level patching
try:
    original = obj.some_method
    obj.some_method = lambda *a, **kw: None
    print("Instance patching: WORKS")
except (AttributeError, TypeError) as e:
    print(f"Instance patching: FAILS ({e})")

# Try class-level patching
try:
    cls = type(obj)
    original_cls = cls.some_method
    cls.some_method = lambda self, *a, **kw: None
    print("Class-level patching: WORKS")
    cls.some_method = original_cls
except (AttributeError, TypeError) as e:
    print(f"Class-level patching: FAILS ({e})")
```

---

## Phase 3: Implementation (TDD)

Follow strict TDD: write tests first, verify they fail, implement, verify they pass.

If the `develop` skill and `test-driven-development` skill are available, dispatch a subagent to invoke `test-driven-development`. Otherwise, follow the TDD loop directly.

### 3.1 Write Tests

Create `tests/unit/test_[name]_plugin.py`.

**Required test categories (ALL plugins must have these):**

1. **Import guard**: Verify `_[LIB]_AVAILABLE` flag is True
2. **Graceful degradation**: Monkeypatch the flag to False, verify `activate()` raises `ImportError` with exact message
3. **Basic interception**: Mock an operation, call it in sandbox, verify return value matches
4. **Full assertion certainty**: Every field in `interaction.details` must be asserted. Verify `MissingAssertionFieldsError` is raised when fields are omitted
5. **Unmocked error**: Call operation with no mock registered, verify `UnmockedInteractionError` with hint message
6. **Unused mock**: Register a mock, never trigger it, verify `UnusedMocksError` at `verify_all()`
7. **Typed assertion helpers**: Test each `assert_[operation]()` helper with correct and incorrect values
8. **Exception propagation**: Mock with `raises=SomeError`, verify the error is raised during the call
9. **Conflict detection**: Verify `ConflictError` if another instance already has patches installed
10. **Format methods**: Test `format_interaction()`, `format_mock_hint()`, `format_unmocked_hint()`, `format_assert_hint()`, `format_unused_mock_hint()` produce exact expected strings

**Additional categories for StateMachinePlugin:**

11. **State transition validation**: Invalid transitions raise `InvalidStateError`
12. **Session lifecycle**: new_session, expect, bind, execute, release
13. **Multiple sessions**: Sequential sessions on same plugin

**Additional categories for specific architectures:**

- Streaming: server streaming, client streaming, bidi, empty streams, mid-stream errors
- Proxy objects: attribute access creates proxies, closed state raises, type serialization
- Reentrancy: guards prevent self-interference (file I/O)

**Test file structure:**

```python
"""Unit tests for [Name]Plugin."""

from __future__ import annotations

import pytest

from tripwire._context import _current_test_verifier
from tripwire._errors import InteractionMismatchError, UnmockedInteractionError
from tripwire._verifier import StrictVerifier

# Import the library directly -- all optional deps are in pytest-tripwire[dev].
# Never use pytest.importorskip (green mirage).
import [lib]

from tripwire.plugins.[name]_plugin import (
    _[LIB]_AVAILABLE,
    [Name]MockConfig,
    [Name]Plugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_verifier_with_plugin() -> tuple[StrictVerifier, [Name]Plugin]:
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, [Name]Plugin):
            return v, p
    p = [Name]Plugin(v)
    return v, p

def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0."""
    # Reset _install_count and restore any patched methods
    ...

@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    _reset_plugin_count()
    yield
    _reset_plugin_count()
```

**Test assertion rules (MANDATORY):**

- Every assertion MUST use exact equality (`==`), never `in`, `assert len() > 0`, or `is not None`
- Every test that exercises production code in a sandbox MUST call `assert_interaction()` afterward
- Every test MUST fail if `assert_interaction()` calls are removed (green mirage prevention)
- Use ESCAPE analysis comments on each test explaining: CLAIM, PATH, CHECK, MUTATION, ESCAPE

### 3.2 Verify Tests Fail

Run the test file and confirm all tests fail (no implementation yet):

```bash
cd [PROJECT_ROOT] && uv run pytest tests/unit/test_[name]_plugin.py -v
```

### 3.3 Write Plugin Implementation

Create `src/tripwire/plugins/[name]_plugin.py`.

**BasePlugin implementation structure:**

```python
"""[Name]Plugin: intercepts [library].[class].[methods] with per-[key] FIFO queues."""

from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from tripwire._base_plugin import BasePlugin
from tripwire._context import _get_verifier_or_raise
from tripwire._errors import UnmockedInteractionError
from tripwire._timeline import Interaction

if TYPE_CHECKING:
    from tripwire._verifier import StrictVerifier

# Optional dependency guard
try:
    import [lib]
    _[LIB]_AVAILABLE = True
except ImportError:  # pragma: no cover
    _[LIB]_AVAILABLE = False

@dataclass
class [Name]MockConfig:
    operation: str
    returns: Any
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(
        default_factory=lambda: "".join(traceback.format_stack())
    )

# Module-level helper
def _get_[name]_plugin() -> [Name]Plugin:
    verifier = _get_verifier_or_raise("[prefix]:[operation]")
    for plugin in verifier._plugins:
        if isinstance(plugin, [Name]Plugin):
            return plugin
    raise RuntimeError(...)

# Sentinel
class _[Name]Sentinel:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

# Patched method factory
def _make_patched_method(operation: str) -> Any:
    def _patched(self, *args, **kwargs):
        plugin = _get_[name]_plugin()
        source_id = f"[prefix]:{operation}"
        with plugin._registry_lock:
            queue = plugin._queues.get(operation)
            if not queue:
                hint = plugin.format_unmocked_hint(source_id, args, kwargs)
                raise UnmockedInteractionError(
                    source_id=source_id, args=args, kwargs=kwargs, hint=hint,
                )
            config = queue.popleft()
        details = _extract_details(operation, self, args, kwargs)
        interaction = Interaction(
            source_id=source_id, sequence=0, details=details, plugin=plugin,
        )
        plugin.record(interaction)
        if config.raises is not None:
            raise config.raises
        return config.returns
    return _patched

class [Name]Plugin(BasePlugin):
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()
    _original_methods: ClassVar[dict[str, Any] | None] = None
    _INTERCEPTED_OPERATIONS: ClassVar[tuple[str, ...]] = (...)

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[[Name]MockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    def mock_operation(self, operation, *, returns, raises=None, required=True):
        ...

    def activate(self) -> None:
        if not _[LIB]_AVAILABLE:
            raise ImportError(...)
        # Reference-counted class-level patch installation
        ...

    def deactivate(self) -> None:
        # Reference-counted restoration
        ...

    def matches(self, interaction, expected) -> bool:
        # Field-by-field comparison
        ...

    def get_unused_mocks(self) -> list:
        ...

    def format_interaction(self, interaction) -> str:
        ...

    def format_mock_hint(self, interaction) -> str:
        ...

    def format_unmocked_hint(self, source_id, args, kwargs) -> str:
        ...

    def format_assert_hint(self, interaction) -> str:
        ...

    def format_unused_mock_hint(self, mock_config) -> str:
        ...

    # Typed assertion helpers
    def assert_[operation](self, ...):
        ...
```

**Key rules:**
- `assertable_fields()` MUST return `frozenset(interaction.details.keys())` unless there's a specific documented reason (state-transition-only steps)
- Auto-assert is PROHIBITED: never call `mark_asserted()` from `record()` or intercept hooks
- Module-level capture, class-level ref counting, per-verifier FIFO queues
- `matches()` MUST do field-by-field comparison (never `return True`)

### 3.4 Integration

Update these files to register the new plugin:

**`src/tripwire/_registry.py`** -- Add a `PluginEntry`:
```python
PluginEntry("[name]", "tripwire.plugins.[name]_plugin", "[Name]Plugin", "[availability_check]"),
```

**`src/tripwire/__init__.py`** -- Add:
1. Import (with try/except for optional deps)
2. Proxy class (`_[Name]Proxy`)
3. Proxy singleton (`[name]_mock = _[Name]Proxy()`)
4. `__all__` entries: `"[Name]Plugin"`, `"[name]_mock"`

**`tests/unit/test_init.py`** -- Update expected `__all__` set

**`tests/unit/test_registry.py`** -- Update registry count and expected names (if test exists)

**`pyproject.toml`** -- Add optional dependency extra:
```toml
[project.optional-dependencies]
[name] = ["[pip_package]>=X.Y"]
```
And add to the `all` extra.

### 3.5 Run Tests

```bash
cd [PROJECT_ROOT] && uv run pytest tests/unit/test_[name]_plugin.py tests/unit/test_init.py -v
```

Then full suite:
```bash
cd [PROJECT_ROOT] && uv run pytest tests/ -x
```

---

## Phase 4: Quality Gates

Run these after implementation. If the `develop` skill is available, dispatch subagents for each gate. Otherwise, run them directly.

### Gate 1: Implementation Completeness

For each item in the Architecture Decision Record (Phase 2), verify it exists in code:
- All intercepted methods are patched
- All detail fields are captured
- All typed assertion helpers exist
- Registry entry added
- Proxy singleton added
- `__all__` updated
- `pyproject.toml` extra added

### Gate 2: Code Review

If `requesting-code-review` skill is available, invoke it. Otherwise, manually review for:
- `assertable_fields()` returns `frozenset(interaction.details.keys())`
- No `mark_asserted()` in record/intercept hooks
- Module-level capture, class-level ref counting
- Sentinel proxy pattern followed
- No sensitive data in `interaction.details`
- Thread safety (locks around queue access)

### Gate 3: Fact-Checking

If `fact-checking` skill is available, invoke it. Otherwise, verify:
- All docstrings match actual behavior
- All comments are accurate
- All type hints are correct
- Error messages reference correct method/class names

### Gate 4: Green Mirage Audit

If `auditing-green-mirage` skill is available, invoke it. Otherwise, verify:
- No tests use `"substring" in result` (BANNED)
- No tests use `assert len() > 0` or `assert result is not None` (BANNED)
- Every test that calls production code in sandbox also calls `assert_interaction()`
- Removing `assert_interaction()` from any test would cause it to fail

### Gate 5: Full Test Suite

```bash
cd [PROJECT_ROOT] && uv run pytest tests/ -x
```

ALL tests must pass.

---

## Phase 5: Documentation

Create documentation for the new plugin. All documentation uses mkdocs-material with mkdocstrings.

### 5.1 Plugin Guide

Create `docs/guides/[name]-plugin.md`:

```markdown
# [Name]Plugin Guide

The [Name]Plugin intercepts [library description] calls in your code, letting you
mock responses and assert exactly what your code sent.

## Installation

```bash
pip install pytest-tripwire[[name]]
```

## Quick Start

```python
import tripwire

def my_function():
    """Production code that uses [library]."""
    import [lib]
    # ... realistic example ...

def test_my_function():
    # 1. Register mocks
    tripwire.[name]_mock.mock_[operation]([args], returns=[value])

    # 2. Run production code in sandbox
    with tripwire:
        result = my_function()

    # 3. Assert what happened
    tripwire.[name]_mock.assert_[operation]([expected_fields])
    assert result == [expected]
```

## Mocking Operations

### [operation_name]

```python
tripwire.[name]_mock.mock_[operation](
    [params],
    returns=[value],
    raises=None,       # optional: raise this exception instead
    required=True,     # optional: if False, ok if never triggered
)
```

[Repeat for each operation]

## Asserting Interactions

### Typed Helpers

```python
tripwire.[name]_mock.assert_[operation]([params])
```

### Generic Assert

```python
tripwire.assert_interaction(
    tripwire.[name]_mock.sentinel.[operation],
    [field]=[value],
)
```

## Exception Simulation

```python
tripwire.[name]_mock.mock_[operation](
    [params],
    returns=None,
    raises=[LibraryError]("simulated failure"),
)
```

## Common Patterns

### [Pattern 1 title]

```python
# [Realistic example]
```

[Repeat for common patterns]
```

### 5.2 API Reference

Create `docs/reference/[name]-plugin.md`:

```markdown
# [Name]Plugin API Reference

::: tripwire.plugins.[name]_plugin.[Name]Plugin
    options:
      show_source: false
      members:
        - mock_[operation]
        - assert_[operation]
        [... list all public methods]

::: tripwire.plugins.[name]_plugin.[Name]MockConfig
    options:
      show_source: false
```

### 5.3 Update mkdocs.yml

Add the new guide and reference pages to the `nav` section in `mkdocs.yml`:

```yaml
nav:
  - Guides:
    - ...[existing]...
    - [Name]Plugin: guides/[name]-plugin.md
  - Reference:
    - ...[existing]...
    - [Name]Plugin: reference/[name]-plugin.md
```

### 5.4 Update README

Update `README.md`:
- Update plugin count in the intro paragraph
- Add the new plugin to the plugin table/list
- Add any new extras to the installation section

### 5.5 Create Working Example

Create a working example in `examples/` or `docs/examples/`:

1. Create `examples/[name]_example.py` with a complete, runnable example
2. The example should demonstrate:
   - Realistic production function that uses the library
   - Test function that mocks the interaction
   - Assertion of all captured fields
3. Create `tests/examples/test_[name]_example.py` that imports and runs the example to verify it works

---

## Phase 6: Completion Checklist

Before marking the plugin as complete, verify ALL items:

- [ ] Plugin discovery complete (all questions answered)
- [ ] Architecture decision record produced
- [ ] Tests written first (TDD)
- [ ] Tests cover all required categories (9+ base, additional for specific architectures)
- [ ] Plugin implementation complete
- [ ] `src/tripwire/_registry.py` updated with `PluginEntry`
- [ ] `src/tripwire/__init__.py` updated (import, proxy class, singleton, `__all__`)
- [ ] `tests/unit/test_init.py` updated
- [ ] `pyproject.toml` updated (optional dependency extra, added to `all` extra)
- [ ] All quality gates passed (completeness, code review, fact-check, green mirage, tests)
- [ ] Documentation guide created (`docs/guides/[name]-plugin.md`)
- [ ] API reference created (`docs/reference/[name]-plugin.md`)
- [ ] `mkdocs.yml` nav updated
- [ ] `README.md` updated (plugin count, plugin list)
- [ ] Working example created with test
- [ ] Full test suite passes
- [ ] Changes committed

---

## Reference: Closest Existing Plugin by Category

When building a new plugin, use the closest existing plugin as a template:

| New Plugin Category | Reference Plugin | File |
|---|---|---|
| Database/cache (stateless ops) | RedisPlugin | `redis_plugin.py` |
| Database (document operations) | MongoPlugin | `mongo_plugin.py` |
| Message queue (connection lifecycle) | PikaPlugin | `pika_plugin.py` |
| Network service (SSH/connection) | SshPlugin | `ssh_plugin.py` |
| Cloud SDK (service + operation) | Boto3Plugin | `boto3_plugin.py` |
| Streaming/bidirectional | GrpcPlugin | `grpc_plugin.py` |
| Stdlib (always available) | DnsPlugin | `dns_plugin.py` |
| C extension/proxy objects | NativePlugin | `native_plugin.py` |
| Mail (SMTP lifecycle) | SmtpPlugin | `smtp_plugin.py` |
| Task queue | CeleryPlugin | `celery_plugin.py` |
| Search engine | ElasticsearchPlugin | `elasticsearch_plugin.py` |
| Crypto/token operations | JwtPlugin / CryptoPlugin | `jwt_plugin.py` / `crypto_plugin.py` |
| File system | FileIoPlugin | `file_io_plugin.py` |
| Socket-level | SocketPlugin | `socket_plugin.py` |
| WebSocket | WebSocketPlugin | `websocket_plugin.py` |

## Reference: tripwire Invariants

These rules are inviolable. Every plugin must follow them:

1. **Full assertion certainty**: `assertable_fields()` returns `frozenset(interaction.details.keys())` unless documented reason
2. **Auto-assert PROHIBITED**: No `mark_asserted()` in record/intercept hooks
3. **Module-level capture**: Save originals at module level before patches
4. **Class-level ref counting**: `_install_count` + `_install_lock` for nested sandbox support
5. **Per-verifier FIFO queues**: Each verifier instance gets its own queues
6. **ContextVar routing**: Use `_get_verifier_or_raise()` from interceptors
7. **Sentinel proxy pattern**: Opaque handle for type-safe `assert_interaction()` filtering
8. **Thread safety**: Lock around queue access (`_registry_lock`)
9. **No sensitive data in details**: Passwords, tokens, keys must never be stored in `interaction.details`
10. **`matches()` must do real comparison**: Field-by-field equality, never `return True`
