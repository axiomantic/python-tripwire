# Guard Mode

## What is guard mode?

Guard mode prevents accidental real I/O during tests. bigfoot installs interceptors at **session startup** and keeps them active for the entire test run. Every I/O call is routed through bigfoot, and any call that is not covered by a sandbox or an explicit allowlist is either warned about or blocked, depending on the guard level.

## I just saw a warning. What do I do?

If you see a warning like this:

```
GuardedCallWarning: 'http:request' called outside sandbox.
Silence with @pytest.mark.allow("http") or set guard = "error" in [tool.bigfoot] to make this an error.
```

This means your test made a real I/O call outside a bigfoot sandbox. The call still executed normally. To silence the warning, pick one of these options:

**Option 1: Allow the plugin for the entire test** (most common):

```python
@pytest.mark.allow("http")
def test_something():
    ...
```

**Option 2: Allow the plugin for a specific block:**

```python
with bigfoot.allow("http"):
    ...
```

**Option 3: Mock the call with a sandbox:**

```python
with bigfoot:
    ...
```

## Guard Levels

Configure the guard level in `pyproject.toml` under `[tool.bigfoot]`:

| Level | Config | Behavior |
|-------|--------|----------|
| warn (default) | `guard = "warn"` or omit key | Emit `GuardedCallWarning`, real call proceeds |
| error | `guard = "error"` | Raise `GuardedCallError`, test fails immediately |
| strict | `guard = "strict"` | Same as error (alias) |
| off | `guard = false` | No interception, no warnings |

```toml
# pyproject.toml
[tool.bigfoot]
guard = "error"  # strict enforcement
```

**Note:** `guard = true` is rejected with a clear error message. Use `"warn"`, `"error"`, or `false` instead.

## How it works

bigfoot's pytest plugin installs two layers of guard infrastructure:

1. **Session-scoped patches** (`_bigfoot_guard_patches`): At the start of the test session, bigfoot activates every guard-eligible plugin. The interceptors remain installed for the entire session.

2. **Per-test guard activation** (`pytest_runtest_call` hook): During each test function's body, bigfoot sets the guard ContextVars. When an interceptor fires and there is no active sandbox, it checks guard state and either warns, blocks, or passes through.

### Decision tree

When an interceptor fires, `get_verifier_or_raise()` follows this precedence:

1. **Sandbox active**: Return the verifier. The call is mocked and recorded as usual.
2. **Guard active, plugin in allowlist**: Raise `GuardPassThrough` internally. The interceptor catches this and delegates to the original function. The call is invisible to bigfoot.
3. **Guard active, plugin not in allowlist, warn mode**: Emit `GuardedCallWarning`, then raise `GuardPassThrough`. The real call proceeds.
4. **Guard active, plugin not in allowlist, error mode**: Raise `GuardedCallError`. The test fails immediately.
5. **Guard patches installed but guard not active** (fixture setup/teardown): Raise `GuardPassThrough`. Calls pass through to originals.
6. **No sandbox, no guard**: Raise `SandboxNotActiveError` (existing behavior for non-guard-eligible plugins).

In short: **sandbox > allow/deny > guard level**.

## Allowing Plugins

### pytest marker (whole test)

For tests that need real calls throughout the entire test body:

```python
@pytest.mark.allow("dns", "socket")
def test_needs_network():
    # DNS and socket calls pass through for the entire test
    ...
```

Multiple marks combine via union:

```python
@pytest.mark.allow("dns")
@pytest.mark.allow("socket")
def test_also_needs_network():
    ...
```

### Context manager (scoped block)

For allowing real calls in a specific block:

```python
import bigfoot

def test_boto3_integration():
    bigfoot.boto3_mock.mock_api_call("s3", "PutObject", returns={})

    with bigfoot.allow("dns", "socket"):
        # DNS resolution and raw socket calls pass through
        with bigfoot:
            upload_file("my-bucket", "key", b"data")

    bigfoot.boto3_mock.assert_api_call(
        service="s3", operation="PutObject", params={"Bucket": "my-bucket"},
    )
```

`allow()` calls are additive. Inner blocks add to the outer allowlist:

```python
with bigfoot.allow("dns"):
    # dns is allowed
    with bigfoot.allow("socket"):
        # both dns and socket are allowed
    # back to dns only
```

### Fixture-based (setup-time)

Fixtures can set up allowlists during test setup:

```python
@pytest.fixture
def allow_dns():
    with bigfoot.allow("dns"):
        yield
```

### Combining markers and fixtures

Marker allowlists and fixture allowlists are **merged** (unioned). A test with `@pytest.mark.allow("socket")` that uses a fixture which calls `bigfoot.allow("dns")` will have both `"dns"` and `"socket"` in its allowlist.

`@pytest.mark.deny` narrows the merged allowlist:

```python
@pytest.mark.allow("dns", "socket")
@pytest.mark.deny("dns")
def test_socket_only():
    # socket is allowed, dns is blocked
    ...
```

### Valid names

`allow()` accepts any plugin registry name (e.g., `"http"`, `"redis"`, `"boto3"`) or guard-eligible source-ID prefix (e.g., `"db"`, `"asyncio"`). Passing an unknown name raises `BigfootConfigError` immediately.

## Denying Plugins

### pytest marker

The `deny` mark removes plugins from the allowlist for the entire test:

```python
@pytest.mark.allow("dns", "socket", "http")
@pytest.mark.deny("http")
def test_network_but_not_http():
    # DNS and socket pass through, but http is guarded
    ...
```

Multiple deny marks combine via union:

```python
@pytest.mark.allow("dns", "socket", "http")
@pytest.mark.deny("http")
@pytest.mark.deny("socket")
def test_dns_only():
    # Only DNS passes through
    ...
```

### Context manager

`deny()` narrows the current allowlist by removing specific plugins:

```python
with bigfoot.allow("dns", "socket", "http"):
    with bigfoot.deny("http"):
        # dns and socket still pass through
        # http is guarded again
        ...
    # http is allowed again here
```

Like `allow()`, `deny()` blocks nest and restore the previous allowlist on exit:

```python
with bigfoot.allow("dns", "socket"):
    with bigfoot.deny("socket"):
        # only dns allowed
        with bigfoot.deny("dns"):
            # nothing allowed -- full guard mode
        # dns allowed again
    # dns and socket allowed again
```

Denying a plugin that is not currently allowed is a no-op (no error).

## Filtering Warnings

In warn mode, you can filter `GuardedCallWarning` using Python's standard `warnings` module:

```python
import warnings
from bigfoot import GuardedCallWarning

# Suppress all guard warnings
warnings.filterwarnings("ignore", category=GuardedCallWarning)

# Suppress warnings for a specific plugin
warnings.filterwarnings("ignore", message=".*http.*", category=GuardedCallWarning)
```

Or in `pyproject.toml` via pytest's warning filters:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "ignore::bigfoot.GuardedCallWarning",
]
```

## Configuration

Guard mode is **enabled by default** in warn mode. Configuration lives in `pyproject.toml` under `[tool.bigfoot]`:

```toml
# Default: warn mode (emit warnings, calls proceed)
[tool.bigfoot]
# guard key can be omitted entirely

# Strict enforcement: block unguarded calls
[tool.bigfoot]
guard = "error"

# Disable guard mode entirely
[tool.bigfoot]
guard = false
```

When guard mode is disabled (`guard = false`), bigfoot does not install session-scoped patches and does not activate guard during tests. Plugins only intercept calls inside explicit sandboxes, which is the pre-guard-mode behavior.

## Supported plugins

Guard mode applies to plugins that perform external I/O. The `supports_guard` class variable controls eligibility.

### Guard-eligible plugins (21)

These plugins have `supports_guard = True` (the default) and are activated by guard mode:

| Plugin | Intercepts |
|--------|------------|
| HttpPlugin | `httpx`, `requests`, `urllib`, `aiohttp` |
| SubprocessPlugin | `subprocess.run`, `shutil.which` |
| PopenPlugin | `subprocess.Popen` |
| AsyncSubprocessPlugin | `asyncio.create_subprocess_*` |
| SmtpPlugin | `smtplib` |
| SocketPlugin | `socket` |
| DatabasePlugin | `sqlite3` |
| Psycopg2Plugin | `psycopg2` |
| AsyncpgPlugin | `asyncpg` |
| RedisPlugin | `redis` |
| MemcachePlugin | `pymemcache` |
| MongoPlugin | `pymongo` |
| ElasticsearchPlugin | `elasticsearch` |
| Boto3Plugin | `boto3` |
| PikaPlugin | `pika` (RabbitMQ) |
| SshPlugin | `paramiko` |
| GrpcPlugin | `grpcio` |
| DnsPlugin | DNS resolution |
| AsyncWebSocketPlugin | `websockets` |
| SyncWebSocketPlugin | `websocket-client` |
| McpPlugin | `mcp` |

### Non-guard plugins (7)

These plugins set `supports_guard = False` because they do not perform external I/O:

| Plugin | Why excluded |
|--------|-------------|
| MockPlugin | Generic mock proxies, no real I/O |
| LoggingPlugin | Intercepts `logging` module, no I/O |
| JwtPlugin | JWT encoding/decoding, pure computation |
| CryptoPlugin | Cryptographic operations, pure computation |
| CeleryPlugin | Task dispatch interception, no direct I/O |
| FileIoPlugin | Opt-in (`default_enabled=False`), local filesystem |
| NativePlugin | Opt-in (`default_enabled=False`), ctypes/cffi |

## Error messages

When guard mode is set to `"error"` and blocks a call, `GuardedCallError` provides three resolution options:

```
GuardedCallError: 'http:request' blocked by bigfoot guard mode.

  Fix: allow this plugin to make real calls:

    @pytest.mark.allow("http")
    def test_something():
        ...

  Or use a context manager (scoped to a block):

    with bigfoot.allow("http"):
        ...

  Or mock the call with a sandbox:

    with bigfoot:
        ...

  Valid plugin names for allow():
    async_subprocess, async_websocket, ...

  Docs: https://bigfoot.readthedocs.io/guides/guard-mode/
```

## Example: boto3 with DNS and socket

boto3 makes DNS lookups and raw socket connections internally. A test that mocks the boto3 API call but runs outside a sandbox needs to allow the underlying network access:

```python
import pytest
import bigfoot

@pytest.mark.allow("dns", "socket")
def test_s3_upload():
    bigfoot.boto3_mock.mock_api_call("s3", "PutObject", returns={})

    with bigfoot:
        upload_to_s3("my-bucket", "my-key", b"hello")

    bigfoot.boto3_mock.assert_api_call(
        service="s3", operation="PutObject",
        params={"Bucket": "my-bucket", "Key": "my-key", "Body": b"hello"},
    )
```

Without the `@pytest.mark.allow("dns", "socket")` mark, any DNS or socket calls that happen during test setup (before `with bigfoot:`) would trigger warnings (or errors in strict mode).

## Interaction with sandbox mode

Guard mode and sandbox mode are complementary:

- **Inside a sandbox** (`with bigfoot:`): All calls are intercepted, mocked, and recorded. Guard mode is irrelevant because the sandbox verifier handles everything.
- **Outside a sandbox, guard active**: Calls to guard-eligible plugins are warned about or blocked, unless in an `allow()` block or marked with `@pytest.mark.allow`. The `deny()` context manager and `@pytest.mark.deny` can narrow the allowlist to re-guard specific plugins.
- **Outside a sandbox, guard inactive** (fixture setup/teardown): Interceptors are installed but pass through to originals. This prevents guard from interfering with test infrastructure.

Guard mode does not change how sandboxes work. It only adds protection for the code that runs outside sandboxes during a test.
