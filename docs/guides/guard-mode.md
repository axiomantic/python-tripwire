# Firewall Mode

## What is firewall mode?

Firewall mode prevents accidental real I/O during tests. bigfoot installs interceptors at **session startup** and keeps them active for the entire test run. Every I/O call is routed through bigfoot, and any call that is not covered by a sandbox or an explicit firewall rule is either warned about or blocked, depending on the firewall level.

In earlier versions of bigfoot, this was called "guard mode" with coarse plugin-level `allow("http")` / `deny("redis")` rules. The firewall redesign introduces **granular pattern matching** via `M()` objects, **TOML-based configuration**, **ceiling restrictions** via `restrict()`, and **protocol-typed request objects** (`FirewallRequest`).

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

**Option 2: Allow with a granular pattern:**

```python
from bigfoot import M

@pytest.mark.allow(M(protocol="http", host="*.example.com"))
def test_something():
    ...
```

**Option 3: Allow in a scoped block:**

```python
with bigfoot.allow("http"):
    ...
```

**Option 4: Mock the call with a sandbox:**

```python
with bigfoot:
    ...
```

## Quick start

### String-based rules (simplest)

The simplest way to use the firewall is with string-based plugin names, which works the same as the old guard mode:

```python
import bigfoot

# Mark: allow for entire test
@pytest.mark.allow("http")
def test_needs_http():
    ...

# Context manager: allow for a block
def test_scoped():
    with bigfoot.allow("dns", "socket"):
        ...
```

### Pattern-based rules with M()

For granular control, use `M()` pattern objects:

```python
from bigfoot import M

# Allow HTTP only to specific hosts
@pytest.mark.allow(M(protocol="http", host="*.example.com"))
def test_api_calls():
    ...

# Allow HTTP GET only
@pytest.mark.allow(M(protocol="http", method="GET"))
def test_readonly():
    ...

# Allow Redis only for specific commands
@pytest.mark.allow(M(protocol="redis", command="GET"))
def test_cache_reads():
    ...
```

## Firewall levels

Configure the firewall level in `pyproject.toml` under `[tool.bigfoot]`:

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

## Pattern matching with M()

The `M()` object lets you define granular firewall rules that match against `FirewallRequest` fields. Each protocol defines its own set of matchable fields.

### Matching syntax

`M()` accepts keyword arguments. Each key corresponds to a field on the protocol's `FirewallRequest` dataclass:

```python
from bigfoot import M

# Exact match
M(protocol="http", method="GET")

# Glob patterns (for strings)
M(protocol="http", host="*.example.com")
M(protocol="subprocess", command="/usr/bin/*")

# CIDR notation (for hosts/IPs)
M(protocol="http", host="10.0.0.0/8")

# Regex (prefix with ~)
M(protocol="http", path="~^/api/v[0-9]+/users")

# Callable (arbitrary predicate)
M(protocol="http", host=lambda h: h.endswith(".internal"))
```

### Supported fields by protocol

| Protocol | Fields |
|----------|--------|
| `http` | `host`, `path`, `method`, `port` |
| `redis` | `host`, `port`, `command`, `db` |
| `subprocess` | `command`, `args` |
| `boto3` | `service`, `operation`, `region` |
| `database` | `host`, `port`, `database`, `driver` |
| `socket` | `host`, `port`, `family` |
| `dns` | `name`, `rdtype` |
| `smtp` | `host`, `port` |
| `grpc` | `host`, `port`, `service`, `method` |
| `ssh` | `host`, `port`, `username` |

### Combining patterns

Multiple `M()` objects in a single `allow()` or `deny()` create a union (any match allows/denies):

```python
@pytest.mark.allow(
    M(protocol="http", host="*.example.com"),
    M(protocol="http", host="*.internal.com"),
)
def test_multi_host():
    ...
```

## TOML configuration

### Basic TOML firewall config

The `[tool.bigfoot.firewall]` section in `pyproject.toml` replaces the old `guard_allow` key. It provides structured, per-protocol rules:

```toml
[tool.bigfoot]
guard = "error"

[tool.bigfoot.firewall]
allow = [
    "http://*.example.com",
    "http://api.stripe.com",
    "redis://localhost",
    "subprocess:/usr/bin/git",
    "subprocess:/usr/local/bin/helm",
    "boto3:s3",
    "boto3:sqs",
]
```

### Denying in TOML

```toml
[tool.bigfoot.firewall]
deny = ["http://*.production.internal"]
```

### Per-file allow rules

Override firewall rules for specific test files using the flat `per-file-allow` map.
Keys are glob patterns matched against test file paths; values are lists of allow rules:

```toml
[tool.bigfoot.firewall.per-file-allow]
"tests/integration/test_api.py" = ["http:*"]
"tests/api/*" = ["http:*", "dns:*"]
```

### Legacy `guard_allow` migration

The old `guard_allow` config key has been removed. If you see a `BigfootConfigError` about `guard_allow`, migrate as follows:

```toml
# OLD (removed):
[tool.bigfoot]
guard_allow = ["socket", "database"]

# NEW:
[tool.bigfoot.firewall]
allow = ["socket:*", "database:*"]
```

## Three-level configuration

Firewall rules combine from three sources, with later sources able to narrow but not widen earlier ones:

1. **TOML** (`pyproject.toml`): Project-wide defaults. Applied to every test.
2. **Marks** (`@pytest.mark.allow`, `@pytest.mark.deny`): Per-test overrides. Can widen or narrow the TOML rules.
3. **Context managers** (`bigfoot.allow()`, `bigfoot.deny()`, `bigfoot.restrict()`): Scoped blocks within a test. `restrict()` enforces a ceiling that inner blocks cannot widen.

### Precedence

The general rule is: **sandbox > allow/deny > firewall level**.

Within the allow/deny layer:
- Marks merge with TOML rules (union for allow, union for deny)
- Context managers stack: `allow()` widens, `deny()` narrows, `restrict()` sets a ceiling
- `deny()` always wins over `allow()` when both match the same request

## Marks

### @pytest.mark.allow

Allow real calls for the entire test:

```python
# String form (allow entire plugin)
@pytest.mark.allow("dns", "socket")
def test_needs_network():
    ...

# Pattern form (granular)
@pytest.mark.allow(M(protocol="http", host="*.example.com"))
def test_api():
    ...
```

Multiple marks combine via union:

```python
@pytest.mark.allow("dns")
@pytest.mark.allow("socket")
def test_also_needs_network():
    ...
```

### @pytest.mark.deny

Narrow the allowlist for the entire test:

```python
@pytest.mark.allow("dns", "socket", "http")
@pytest.mark.deny("http")
def test_network_but_not_http():
    # DNS and socket pass through, but http is guarded
    ...
```

## Context managers

### allow()

Widen the allowlist for a scoped block:

```python
import bigfoot

def test_boto3_integration():
    bigfoot.boto3_mock.mock_api_call("s3", "PutObject", returns={})

    with bigfoot.allow("dns", "socket"):
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

### deny()

Narrow the current allowlist:

```python
with bigfoot.allow("dns", "socket", "http"):
    with bigfoot.deny("http"):
        # dns and socket still pass through
        # http is guarded again
        ...
    # http is allowed again here
```

Like `allow()`, `deny()` blocks nest and restore the previous state on exit.

### restrict()

Set a **ceiling** that inner blocks cannot widen. This is the key new context manager in the firewall redesign:

```python
with bigfoot.restrict("http"):
    # Only http is allowed in this block, nothing else
    with bigfoot.allow("dns"):
        # dns is NOT allowed here -- restrict() prevents widening
        # only http is still allowed
    ...
```

`restrict()` is useful for enforcing that a code path only makes specific types of calls:

```python
from bigfoot import M

def test_payment_isolation():
    with bigfoot.restrict(M(protocol="http", host="api.stripe.com")):
        # Only Stripe HTTP calls are allowed
        # Any other HTTP call, or any non-HTTP call, is blocked
        process_payment(amount=5000)
```

### Fixture-based

Fixtures can set up firewall rules during test setup:

```python
@pytest.fixture
def allow_dns():
    with bigfoot.allow("dns"):
        yield
```

## FirewallRequest protocol

Each protocol defines a `FirewallRequest` dataclass that carries the details of an intercepted call. Plugins construct these objects and pass them to the firewall for matching:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class HttpFirewallRequest:
    protocol: str = "http"
    method: str = ""
    host: str = ""
    port: int = 0
    path: str = ""

@dataclass(frozen=True)
class RedisFirewallRequest:
    protocol: str = "redis"
    host: str = ""
    port: int = 6379
    command: str = ""
    db: int = 0

@dataclass(frozen=True)
class SubprocessFirewallRequest:
    protocol: str = "subprocess"
    command: str = ""
    args: tuple = ()
```

Plugin authors constructing `FirewallRequest` objects should populate all available fields so that user-defined `M()` patterns can match precisely.

## How it works

bigfoot's pytest plugin installs two layers of firewall infrastructure:

1. **Session-scoped patches** (`_bigfoot_guard_patches`): At the start of the test session, bigfoot activates every guard-eligible plugin. The interceptors remain installed for the entire session.

2. **Per-test firewall activation** (`pytest_runtest_call` hook): During each test function's body, bigfoot sets the firewall ContextVars. When an interceptor fires and there is no active sandbox, it checks firewall state and either warns, blocks, or passes through.

### Decision tree

When an interceptor fires, `get_verifier_or_raise()` follows this precedence:

1. **Sandbox active**: Return the verifier. The call is mocked and recorded as usual.
2. **Firewall active, request matches allow rule**: Raise `GuardPassThrough` internally. The interceptor catches this and delegates to the original function. The call is invisible to bigfoot.
3. **Firewall active, request matches deny rule** (or no allow rule matches): Check firewall level.
4. **Warn mode**: Emit `GuardedCallWarning`, then raise `GuardPassThrough`. The real call proceeds.
5. **Error mode**: Raise `GuardedCallError`. The test fails immediately.
6. **Firewall patches installed but firewall not active** (fixture setup/teardown): Raise `GuardPassThrough`. Calls pass through to originals.
7. **No sandbox, no firewall**: Raise `SandboxNotActiveError` (existing behavior for non-guard-eligible plugins).

In short: **sandbox > allow/deny/restrict > firewall level**.

## Examples for common protocols

### HTTP

```python
from bigfoot import M

# Allow all HTTP to a specific host
@pytest.mark.allow(M(protocol="http", host="api.example.com"))
def test_api_integration():
    ...

# Allow only GET requests
@pytest.mark.allow(M(protocol="http", method="GET", host="*.example.com"))
def test_readonly_api():
    ...
```

### Redis

```python
from bigfoot import M

# Allow read-only Redis commands
@pytest.mark.allow(M(protocol="redis", command="GET"))
def test_cache_read():
    ...

# Allow Redis to a specific host
@pytest.mark.allow(M(protocol="redis", host="localhost", port=6379))
def test_local_redis():
    ...
```

### Subprocess

```python
from bigfoot import M

# Allow specific binaries
@pytest.mark.allow(M(protocol="subprocess", command="/usr/bin/git"))
def test_git_operations():
    ...

# Allow a directory of binaries
@pytest.mark.allow(M(protocol="subprocess", command="/usr/local/bin/*"))
def test_local_tools():
    ...
```

### boto3

```python
from bigfoot import M

# Allow S3 operations only
@pytest.mark.allow(M(protocol="boto3", service="s3"))
def test_s3_upload():
    ...

# Allow S3 and SQS in a specific region
@pytest.mark.allow(
    M(protocol="boto3", service="s3", region="us-east-1"),
    M(protocol="boto3", service="sqs", region="us-east-1"),
)
def test_aws_pipeline():
    ...
```

### Mixed: boto3 with DNS and socket

boto3 makes DNS lookups and raw socket connections internally. A test that mocks the boto3 API call but runs outside a sandbox needs to allow the underlying network access:

```python
import pytest
import bigfoot
from bigfoot import M

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

## Error messages

When firewall mode is set to `"error"` and blocks a call, `GuardedCallError` provides resolution options:

```
GuardedCallError: 'http:request' blocked by bigfoot firewall.

  Request details:
    protocol=http, method=POST, host=api.stripe.com, path=/v1/charges

  Fix: allow this call to pass through:

    # Allow the entire plugin:
    @pytest.mark.allow("http")
    def test_something():
        ...

    # Allow with a pattern:
    @pytest.mark.allow(M(protocol="http", host="api.stripe.com"))
    def test_something():
        ...

    # Or use a context manager (scoped to a block):
    with bigfoot.allow("http"):
        ...

    # Or mock the call with a sandbox:
    with bigfoot:
        ...

  Valid plugin names for allow():
    async_subprocess, async_websocket, ...

  Docs: https://bigfoot.readthedocs.io/guides/guard-mode/
```

## Filtering warnings

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

## Supported plugins

Firewall mode applies to plugins that perform external I/O. The `supports_guard` class variable controls eligibility.

### Firewall-eligible plugins (21)

These plugins have `supports_guard = True` (the default) and are activated by firewall mode:

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

### Non-firewall plugins (7)

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

## Interaction with sandbox mode

Firewall mode and sandbox mode are complementary:

- **Inside a sandbox** (`with bigfoot:`): All calls are intercepted, mocked, and recorded. Firewall mode is irrelevant because the sandbox verifier handles everything.
- **Outside a sandbox, firewall active**: Calls to firewall-eligible plugins are checked against the allow/deny rules and `M()` patterns. Calls that do not match an allow rule are warned about or blocked. The `restrict()` context manager can set a ceiling that inner blocks cannot widen.
- **Outside a sandbox, firewall inactive** (fixture setup/teardown): Interceptors are installed but pass through to originals. This prevents the firewall from interfering with test infrastructure.

Firewall mode does not change how sandboxes work. It only adds protection for the code that runs outside sandboxes during a test.
