# Guard Mode

Guard mode prevents accidental real I/O during tests. When active, any external call that is not inside a bigfoot sandbox or an explicit `allow()` block raises `GuardedCallError` immediately, rather than silently hitting production services.

## Why it exists

Without guard mode, a test that forgets to open a `with bigfoot:` sandbox (or opens one but makes a call outside it) can accidentally reach real servers, databases, or file systems. Guard mode closes that gap: bigfoot installs interceptors at **session startup** and keeps them active for the entire test run. Every I/O call is routed through bigfoot, and any call that is not covered by a sandbox or an explicit allowlist is blocked.

## How it works

bigfoot's pytest plugin installs two layers of guard infrastructure:

1. **Session-scoped patches** (`_bigfoot_guard_patches`): At the start of the test session, bigfoot activates every guard-eligible plugin. The interceptors remain installed for the entire session.

2. **Per-test guard activation** (`pytest_runtest_call` hook): During each test function's body, bigfoot sets the `_guard_active` ContextVar to `True`. When an interceptor fires and there is no active sandbox, it checks guard state and either blocks the call or passes it through.

### Decision tree

When an interceptor fires, `_get_verifier_or_raise()` follows this precedence:

1. **Sandbox active**: Return the verifier. The call is mocked and recorded as usual.
2. **Guard active, plugin in allowlist**: Raise `_GuardPassThrough` internally. The interceptor catches this and delegates to the original function. The call is invisible to bigfoot.
3. **Guard active, plugin not in allowlist**: Raise `GuardedCallError`. The test fails immediately with a clear error message.
4. **Guard patches installed but guard not active** (fixture setup/teardown): Raise `_GuardPassThrough`. Calls pass through to originals.
5. **No sandbox, no guard**: Raise `SandboxNotActiveError` (existing behavior for non-guard-eligible plugins).

In short: **sandbox > allow/deny > guard**.

## Using `allow()`

The `allow()` context manager permits specific plugin categories to make real calls during guard mode. Allowed calls bypass bigfoot entirely and are not recorded on the timeline.

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

### Combining and nesting

`allow()` calls are additive. Inner blocks add to the outer allowlist:

```python
with bigfoot.allow("dns"):
    # dns is allowed
    with bigfoot.allow("socket"):
        # both dns and socket are allowed
    # back to dns only
```

### Valid names

`allow()` accepts any plugin registry name (e.g., `"http"`, `"redis"`, `"boto3"`) or guard-eligible source-ID prefix (e.g., `"db"`, `"asyncio"`). Passing an unknown name raises `BigfootConfigError` immediately.

## Using `deny()`

The `deny()` context manager narrows the current allowlist by removing specific plugins. It is the inverse of `allow()`: where `allow()` adds plugins to the allowlist, `deny()` removes them.

`deny()` is designed for use inside an `allow()` block when you need to re-guard specific plugins for a section of code:

```python
import bigfoot

def test_selective_network():
    with bigfoot.allow("dns", "socket", "http"):
        # dns, socket, and http all pass through
        with bigfoot.deny("http"):
            # dns and socket still pass through
            # http is guarded again -- calls raise GuardedCallError
            ...
        # http is allowed again here
```

### Nestability

Like `allow()`, `deny()` blocks nest and restore the previous allowlist on exit:

```python
with bigfoot.allow("dns", "socket"):
    # dns and socket allowed
    with bigfoot.deny("socket"):
        # only dns allowed
        with bigfoot.deny("dns"):
            # nothing allowed -- full guard mode
        # dns allowed again
    # dns and socket allowed again
```

### Valid names

`deny()` accepts the same plugin names as `allow()`. Passing an unknown name raises `BigfootConfigError` immediately. Denying a plugin that is not currently allowed is a no-op (no error).

## Using `@pytest.mark.allow`

For tests that need real calls throughout the entire test body, use the `allow` pytest mark instead of wrapping code in `allow()`:

```python
import pytest

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

## Using `@pytest.mark.deny`

The `deny` mark removes plugins from the allowlist for the entire test. When combined with `@pytest.mark.allow`, the deny mark narrows the allow mark:

```python
import pytest

@pytest.mark.allow("dns", "socket", "http")
@pytest.mark.deny("http")
def test_network_but_not_http():
    # DNS and socket pass through, but http is guarded
    ...
```

This is useful when a base class or fixture applies a broad `@pytest.mark.allow`, and a specific test needs to re-guard one of the allowed plugins.

Multiple deny marks combine via union, just like allow marks:

```python
@pytest.mark.allow("dns", "socket", "http")
@pytest.mark.deny("http")
@pytest.mark.deny("socket")
def test_dns_only():
    # Only DNS passes through
    ...
```

## Configuration

Guard mode is **enabled by default**. To disable it, add to your `pyproject.toml`:

```toml
[tool.bigfoot]
guard = false
```

When guard mode is disabled, bigfoot does not install session-scoped patches and does not activate guard during tests. Plugins only intercept calls inside explicit sandboxes, which is the pre-guard-mode behavior.

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

When guard mode blocks a call, `GuardedCallError` provides three resolution options:

```
GuardedCallError: 'http:request' blocked by bigfoot guard mode.

  FOR TEST AUTHORS:
    Option 1: Use a sandbox to mock this call:
      with bigfoot_verifier.sandbox():
          # ... your code ...
    Option 2: Explicitly allow this call (no assertion tracking):
      with bigfoot.allow("http"):
          # ... your code ...
    Option 3: Allow via pytest mark (entire test):
      @pytest.mark.allow("http")
      def test_something():
          ...
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

Without the `@pytest.mark.allow("dns", "socket")` mark, any DNS or socket calls that happen during test setup (before `with bigfoot:`) would raise `GuardedCallError`.

## Interaction with sandbox mode

Guard mode and sandbox mode are complementary:

- **Inside a sandbox** (`with bigfoot:`): All calls are intercepted, mocked, and recorded. Guard mode is irrelevant because the sandbox verifier handles everything.
- **Outside a sandbox, guard active**: Calls to guard-eligible plugins are blocked unless in an `allow()` block or marked with `@pytest.mark.allow`. The `deny()` context manager and `@pytest.mark.deny` can narrow the allowlist to re-guard specific plugins.
- **Outside a sandbox, guard inactive** (fixture setup/teardown): Interceptors are installed but pass through to originals. This prevents guard from interfering with test infrastructure.

Guard mode does not change how sandboxes work. It only adds protection for the code that runs outside sandboxes during a test.
