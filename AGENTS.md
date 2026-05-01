# tripwire - Agent Instructions

## Build & Test Commands

```bash
uv run pytest tests/                  # Run main test suite (1394 tests)
uv run pytest examples/               # Run example tests (22+ tests)
uv run pytest tests/ examples/        # Run everything
uv run pytest tests/unit/test_X.py    # Run a specific test file
uv run mkdocs build --strict          # Build documentation
uv run mkdocs serve                   # Serve docs locally at localhost:8000
```

## Architecture Overview

tripwire is a deterministic test interaction auditor for Python. It intercepts external calls (HTTP, DB, subprocess, etc.) via monkeypatching and enforces three guarantees: every call must be pre-authorized (mocked), every recorded interaction must be explicitly asserted, and every registered mock must be triggered.

### Key Modules

| Path | Purpose |
|------|---------|
| `src/tripwire/_verifier.py` | `StrictVerifier` - the core orchestrator |
| `src/tripwire/_base_plugin.py` | `BasePlugin` ABC for stateless intercept plugins |
| `src/tripwire/_state_machine_plugin.py` | `StateMachinePlugin` ABC for lifecycle plugins |
| `src/tripwire/_registry.py` | `PLUGIN_REGISTRY` with `PluginEntry` dataclasses |
| `src/tripwire/__init__.py` | Proxy singletons, `__all__` exports |
| `src/tripwire/plugins/` | All plugin implementations |
| `src/tripwire/pytest_plugin.py` | Auto-use fixture for pytest integration |

### Plugin Patterns

- **BasePlugin** (stateless): Single intercept point, per-command FIFO queues. Examples: `RedisPlugin`, `HttpPlugin`, `Boto3Plugin`.
- **StateMachinePlugin** (lifecycle): Connection-oriented with state transitions (`new_session().expect("connect", ...).expect("execute", ...)`). Examples: `SmtpPlugin`, `DatabasePlugin`, `SshPlugin`.

### Key Conventions

- `ContextVar` routing: interceptors look up the current verifier via `_get_verifier_or_raise()`
- Sentinel proxy pattern: `_FooProxy` classes in `__init__.py` auto-create plugins on first attribute access
- Class-level ref counting: plugins patch at the class/module level, not per-instance
- `assertable_fields()` must return `frozenset(interaction.details.keys())` (see CLAUDE.md)

## Guard Mode

Guard mode is enabled by default (`[tool.tripwire] guard = true`). It installs I/O plugin interceptors at session startup and blocks any real external call that happens outside a sandbox.

- Tests that need real network access (e.g., boto3 setup making DNS/socket calls) should use `@pytest.mark.allow("dns", "socket")` or `with tripwire.allow("dns", "socket"):`.
- To narrow the allowlist and re-guard specific plugins, use `@pytest.mark.deny(...)` or `with tripwire.deny(...)`. Deny removes plugins from the current allowlist; it nests and restores on exit.
- Non-I/O plugins must set `supports_guard: ClassVar[bool] = False` (e.g., LoggingPlugin, JwtPlugin, CryptoPlugin, CeleryPlugin, MockPlugin).
- Guard-eligible interceptors must handle `_GuardPassThrough` (catch it and call the original function).
- Allowed calls are invisible to tripwire and are not recorded on the timeline.

## Testable Documentation Examples

All code examples shown in plugin guide documentation MUST be runnable and tested.

### How It Works

1. **Example files** live in `examples/{name}/` with:
   - `__init__.py` (empty package marker)
   - `app.py` (production code - the function under test, NO tripwire imports)
   - `test_app.py` (tripwire test following the standard pattern)

2. **Guide pages** include these files via `pymdownx.snippets` in their "Full example" sections:
   ````markdown
   ## Full example

   **Production code** (`examples/dir_name/app.py`):

   ```python
   --8<-- "examples/dir_name/app.py"
   ```

   **Test** (`examples/dir_name/test_app.py`):

   ```python
   --8<-- "examples/dir_name/test_app.py"
   ```
   ````

3. **Tests run automatically** via `uv run pytest examples/` (examples/ is in `testpaths`).

### test_app.py Pattern

```python
"""Brief description."""

import tripwire

from .app import production_function


def test_something():
    # Register mocks BEFORE the sandbox
    tripwire.plugin_proxy.mock_xxx(...)

    with tripwire:
        result = production_function(...)

    # Value assertions
    assert result == expected

    # Interaction assertions (AFTER the sandbox)
    tripwire.plugin_proxy.assert_xxx(...)
```

### Rules

- **Never** put inline code examples in guide "Full example" sections. Always use snippet includes from `examples/`.
- **Every** new plugin guide must have a corresponding `examples/` directory with working tests.
- If a library generates DEBUG logs (boto3, pymongo, celery, etc.), add an autouse fixture to silence them so they don't interfere with LoggingPlugin.
- **Never** use `pytest.importorskip()` in tests. All optional dependencies are included in `pytest-tripwire[dev]` and are expected to be installed. Skipping on missing imports is a green mirage.
- The `.claude/skills/adding-plugins/SKILL.md` skill automates the full plugin creation lifecycle including examples and docs.

## Selective Installation

Core plugins (subprocess, logging, database, socket, file-io, native, dns) require no extras. Optional plugins need:

```bash
pip install pytest-tripwire[all]        # Everything
pip install pytest-tripwire[http]       # httpx, requests, urllib
pip install pytest-tripwire[redis]      # redis
pip install pytest-tripwire[boto3]      # botocore
pip install pytest-tripwire[pymongo]    # pymongo
# ... see pyproject.toml [project.optional-dependencies] for full list
```
