# bigfoot

[![CI](https://github.com/axiomantic/bigfoot/actions/workflows/ci.yml/badge.svg)](https://github.com/axiomantic/bigfoot/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bigfoot)](https://pypi.org/project/bigfoot/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> *"Let me tell you why you're here. You're here because you know something. What you know you can't explain, but you feel it. You've felt it your entire life, that there's something wrong with the world. You don't know what it is, but it's there, like a splinter in your mind, driving you mad."*
> -- Morpheus, The Matrix (1999)

You've had tests pass in CI and then watched the thing they were supposedly testing break in production. You go back and look at the test, and it turns out the mock was wrong, or incomplete, or your production code was making a call the test didn't even know about. The green checkmark was meaningless.

This is what testing with `unittest.mock` is like. It gives you the tools to mock things, but it's entirely on you to remember to assert every call, verify every argument, and notice when production code starts making calls your tests don't account for. Most of the time, you won't. Not because you're careless, but because `unittest.mock` is designed around silence -- if you forget to check something, it has no way of telling you.

bigfoot replaces `unittest.mock` with mocking that actually enforces correctness.

```bash
pip install bigfoot[all]
```

## The three guarantees

bigfoot intercepts every external call your code makes and enforces three rules that `unittest.mock` leaves entirely to you:

1. **Every call must be pre-authorized.** Code makes a call with no registered mock? `UnmockedInteractionError`, immediately.
2. **Every recorded interaction must be explicitly asserted.** Forget to assert an interaction? `UnassertedInteractionsError` at teardown.
3. **Every registered mock must actually be triggered.** Register a mock that never fires? `UnusedMocksError` at teardown.

## What this looks like in practice

```python
# unittest.mock -- this test passes, but proves nothing
from unittest.mock import patch

@patch("myapp.payments.httpx.post")
def test_payment(mock_post):
    mock_post.return_value.json.return_value = {"id": "ch_123"}
    create_charge(5000)
    # Forgot assert_called_with? Test passes.
    # Called with wrong amount? Test passes.
    # Added a second HTTP call? Test passes.

# bigfoot -- every interaction is accounted for
def test_payment():
    bigfoot.http.mock_response("POST", "https://api.stripe.com/v1/charges",
                               json={"id": "ch_123"}, status=200)

    with bigfoot:
        result = create_charge(5000)

    # MUST assert this or test fails at teardown
    bigfoot.http.assert_request(
        "POST", "https://api.stripe.com/v1/charges",
        headers=IsInstance(dict), body='{"amount": 5000}',
    ).assert_response(200, IsInstance(dict), '{"id": "ch_123"}')
    assert result["id"] == "ch_123"
```

| Scenario | unittest.mock | bigfoot |
|----------|---------------|---------|
| Mocked function is never called | Passes silently | `UnusedMocksError` |
| Wrong arguments | Only caught if you add `assert_called_with` | Recorded, must be asserted with exact args |
| Real HTTP/DB/Redis call leaks through | Goes to production | `UnmockedInteractionError` |
| Forgot to assert a call | Passes silently | `UnassertedInteractionsError` |
| `MagicMock` returns wrong type | Auto-generates attributes forever | You declare explicit return values |
| Production code adds a new external call | Existing tests still pass | `UnmockedInteractionError` forces you to handle it |

## Firewall mode

Firewall mode is on by default. When your test session starts, bigfoot installs interceptors that catch any real I/O call happening outside a sandbox.

In `"warn"` mode (the default), accidental calls emit a `GuardedCallWarning` and proceed normally, so your existing suite keeps working while showing you exactly which calls are unguarded. Set `guard = "error"` for strict enforcement.

```python
# Selectively permit real calls
bigfoot.allow("dns", "socket")

# Or via marker
@pytest.mark.allow("dns", "socket")

# Granular patterns
bigfoot.allow(M(protocol="http", host="*.example.com"))

# Set a ceiling that inner blocks cannot widen
bigfoot.restrict("http", "subprocess")
```

Configure project-wide rules in `[tool.bigfoot.firewall]` in your `pyproject.toml`.

## Quick Start

```python
import bigfoot
from dirty_equals import IsInstance

def create_charge(amount):
    """Production code -- calls Stripe via httpx internally."""
    import httpx
    response = httpx.post("https://api.stripe.com/v1/charges",
                          json={"amount": amount})
    return response.json()

def test_payment_flow():
    bigfoot.http.mock_response("POST", "https://api.stripe.com/v1/charges",
                               json={"id": "ch_123"}, status=200)

    with bigfoot:
        result = create_charge(5000)

    bigfoot.http.assert_request(
        "POST", "https://api.stripe.com/v1/charges",
        headers=IsInstance(dict), body='{"amount": 5000}',
    ).assert_response(200, IsInstance(dict), '{"id": "ch_123"}')
    assert result["id"] == "ch_123"
```

If you forget the `assert_request()` call, bigfoot fails the test at teardown:

```
E   bigfoot._errors.UnassertedInteractionsError: 1 interaction was not asserted.
E
E       http.assert_request(
E           "POST",
E           "https://api.stripe.com/v1/charges",
E           headers={'host': 'api.stripe.com', ...},
E           body='{"amount":5000}',
E           require_response=True,
E       ).assert_response(
E           status=200,
E           headers={'content-type': 'application/json'},
E           body='{"id": "ch_123"}',
E       )
E       # ^ [sequence=0] [HttpPlugin] POST https://api.stripe.com/v1/charges (status=200)
```

The error output includes every field with its actual value, so you can usually just copy it directly into your test as the assertion.

## How it works

1. **Register mocks** before the sandbox (`mock_response`, `mock_run`, `returns`, etc.)
2. **Open the sandbox** with `with bigfoot:` (or `async with bigfoot:`)
3. **Code runs normally** inside the sandbox, but external calls are intercepted and recorded
4. **Assert interactions** after the sandbox closes, in order
5. **`verify_all()`** runs automatically at test teardown via the pytest plugin

Since bigfoot uses a module-level API, there are no fixtures to set up or inject. You just import it.

## Coming from unittest.mock

### Concepts mapping

| unittest.mock | bigfoot equivalent | Notes |
|---------------|-------------------|-------|
| `@patch("module.Class")` | `bigfoot.mock("module:Class")` | Colon-separated import path |
| `@patch.object(obj, "attr")` | `bigfoot.mock.object(obj, "attr")` | Same idea, stricter enforcement |
| `MagicMock()` | Plugin-specific mocks | `mock_response`, `mock_run`, `mock_command`, etc. |
| `mock.return_value = X` | `.returns(X)` | Explicit, typed return values |
| `mock.side_effect = Exception` | `mock_error(..., raises=Exception)` | Explicit error mocking |
| `mock.assert_called_with(...)` | `plugin.assert_request(...)` / `spy.assert_call(...)` | Required, not optional |
| `mock.assert_not_called()` | Not needed | If it was not registered, it cannot be called |
| `call_args_list` | Interaction log | Automatic, exhaustive, shown on failure |

### Migration by example

**Patching an HTTP call**

```python
# BEFORE: unittest.mock
from unittest.mock import patch, MagicMock

@patch("myapp.client.requests.get")
def test_fetch_user(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"name": "Alice"}),
    )
    user = fetch_user(42)
    mock_get.assert_called_once_with("https://api.example.com/users/42")
    assert user["name"] == "Alice"

# AFTER: bigfoot
from dirty_equals import IsInstance

def test_fetch_user():
    bigfoot.http.mock_response(
        "GET", "https://api.example.com/users/42",
        json={"name": "Alice"}, status=200,
    )
    with bigfoot:
        user = fetch_user(42)

    bigfoot.http.assert_request(
        "GET", "https://api.example.com/users/42",
        headers=IsInstance(dict), body=None,
    ).assert_response(200, IsInstance(dict), '{"name": "Alice"}')
    assert user["name"] == "Alice"
```

**Patching a subprocess call**

```python
# BEFORE: unittest.mock
from unittest.mock import patch

@patch("myapp.deploy.subprocess.run")
def test_deploy(mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "deployed"
    result = deploy("prod")
    mock_run.assert_called_once()

# AFTER: bigfoot
def test_deploy():
    bigfoot.subprocess_mock.mock_run(
        ["kubectl", "apply", "-f", "prod.yaml"],
        returncode=0, stdout="deployed",
    )
    with bigfoot:
        result = deploy("prod")

    bigfoot.subprocess_mock.assert_run(
        ["kubectl", "apply", "-f", "prod.yaml"],
        returncode=0, stdout="deployed",
    )
```

**Patching an arbitrary object**

```python
# BEFORE: unittest.mock
from unittest.mock import patch

@patch("myapp.services.cache")
def test_cached_lookup(mock_cache):
    mock_cache.get.return_value = "cached_value"
    result = lookup("key")
    mock_cache.get.assert_called_once_with("key")

# AFTER: bigfoot
def test_cached_lookup():
    cache_mock = bigfoot.mock("myapp.services:cache")
    cache_mock.get.returns("cached_value")

    with bigfoot:
        result = lookup("key")

    cache_mock.get.assert_call(args=("key",), kwargs={}, returned="cached_value")
```

### Incremental adoption

You do not have to migrate your entire test suite at once. bigfoot and `unittest.mock` can coexist in the same project:

1. **Start with guard mode.** Install bigfoot and run your suite. Guard mode (default `"warn"`) will show you every real I/O call across all tests without breaking anything.
2. **Migrate test by test.** Pick tests that touch HTTP, subprocess, or database calls first -- these benefit most from bigfoot's strict enforcement.
3. **Escalate to strict guard mode.** Once coverage is high, set `guard = "error"` in `pyproject.toml` to catch any remaining leaks.

## Plugins

bigfoot ships with 27 plugins covering the most common external dependencies:

| Category | Plugins | Intercepts |
|----------|---------|------------|
| **General** | [MockPlugin](https://axiomantic.github.io/bigfoot/guides/mock-plugin/), [LoggingPlugin](https://axiomantic.github.io/bigfoot/guides/logging-plugin/) | Named mock proxies, `logging` module |
| **HTTP** | [HttpPlugin](https://axiomantic.github.io/bigfoot/guides/http-plugin/) | `httpx`, `requests`, `urllib`, `aiohttp` |
| **Subprocess** | [SubprocessPlugin](https://axiomantic.github.io/bigfoot/guides/subprocess-plugin/), [PopenPlugin](https://axiomantic.github.io/bigfoot/guides/popen-plugin/), [AsyncSubprocessPlugin](https://axiomantic.github.io/bigfoot/guides/async-subprocess-plugin/) | `subprocess.run`, `shutil.which`, `Popen`, `asyncio.create_subprocess_*` |
| **Database** | [DatabasePlugin](https://axiomantic.github.io/bigfoot/guides/database-plugin/), [Psycopg2Plugin](https://axiomantic.github.io/bigfoot/guides/psycopg2-plugin/), [AsyncpgPlugin](https://axiomantic.github.io/bigfoot/guides/asyncpg-plugin/), [MongoPlugin](https://axiomantic.github.io/bigfoot/guides/mongo-plugin/), [ElasticsearchPlugin](https://axiomantic.github.io/bigfoot/guides/elasticsearch-plugin/) | `sqlite3`, `psycopg2`, `asyncpg`, `pymongo`, `elasticsearch` |
| **Cache** | [RedisPlugin](https://axiomantic.github.io/bigfoot/guides/redis-plugin/), [MemcachePlugin](https://axiomantic.github.io/bigfoot/guides/memcache-plugin/) | `redis`, `pymemcache` |
| **Network** | [SmtpPlugin](https://axiomantic.github.io/bigfoot/guides/smtp-plugin/), [SocketPlugin](https://axiomantic.github.io/bigfoot/guides/socket-plugin/), [WebSocket](https://axiomantic.github.io/bigfoot/guides/websocket-plugin/), [DnsPlugin](https://axiomantic.github.io/bigfoot/guides/dns-plugin/), [SshPlugin](https://axiomantic.github.io/bigfoot/guides/ssh-plugin/), [GrpcPlugin](https://axiomantic.github.io/bigfoot/guides/grpc-plugin/) | `smtplib`, `socket`, `websockets`, `websocket-client`, DNS resolution, `paramiko`, `grpcio` |
| **Cloud & Messaging** | [Boto3Plugin](https://axiomantic.github.io/bigfoot/guides/boto3-plugin/), [CeleryPlugin](https://axiomantic.github.io/bigfoot/guides/celery-plugin/), [PikaPlugin](https://axiomantic.github.io/bigfoot/guides/pika-plugin/) | `boto3` (AWS), `celery` tasks, `pika` (RabbitMQ) |
| **Crypto & Auth** | [JwtPlugin](https://axiomantic.github.io/bigfoot/guides/jwt-plugin/), [CryptoPlugin](https://axiomantic.github.io/bigfoot/guides/crypto-plugin/) | `PyJWT`, `cryptography` |
| **System** | [FileIoPlugin](https://axiomantic.github.io/bigfoot/guides/file-io-plugin/), [NativePlugin](https://axiomantic.github.io/bigfoot/guides/native-plugin/) | `open`, `pathlib`, `os`; `ctypes`, `cffi` |

<details>
<summary>Plugin examples</summary>

**Subprocess**
```python
bigfoot.subprocess_mock.mock_run(["git", "pull"], returncode=0, stdout="Up to date.\n")
```

**Database (sqlite3)**
```python
bigfoot.db_mock.new_session() \
    .expect("connect", returns=None) \
    .expect("execute", returns=[]) \
    .expect("commit", returns=None) \
    .expect("close", returns=None)
```

**Redis**
```python
bigfoot.redis_mock.mock_command("GET", returns=b"cached_value")
```

**MongoDB**
```python
bigfoot.mongo_mock.mock_operation("find_one", returns={"_id": "abc", "name": "Alice"})
```

**AWS (boto3)**
```python
bigfoot.boto3_mock.mock_api_call("s3", "GetObject", returns={"Body": b"file contents"})
```

**RabbitMQ (pika)**
```python
bigfoot.pika_mock.new_session() \
    .expect("connect", returns=None) \
    .expect("channel", returns=None) \
    .expect("publish", returns=None) \
    .expect("close", returns=None)
```

**SSH (paramiko)**
```python
bigfoot.ssh_mock.new_session() \
    .expect("connect", returns=None) \
    .expect("exec_command", returns=(b"", b"output\n", b"")) \
    .expect("close", returns=None)
```

**SMTP**
```python
bigfoot.smtp_mock.new_session() \
    .expect("connect", returns=(220, b"OK")) \
    .expect("ehlo", returns=(250, b"OK")) \
    .expect("sendmail", returns={}) \
    .expect("quit", returns=(221, b"Bye"))
```

**Logging**
```python
bigfoot.log_mock.assert_info("User logged in", "myapp")
```

**Mock (general)**
```python
svc = bigfoot.mock("myapp.payments:PaymentService")
svc.charge.returns({"status": "ok"})
```

</details>

## Advanced Features

**Concurrent assertions** -- relax FIFO ordering for parallel requests:

```python
with bigfoot.in_any_order():
    bigfoot.http.assert_request(method="GET", url=".../a", headers=IsInstance(dict), body=None,
                                require_response=False)
    bigfoot.http.assert_request(method="GET", url=".../b", headers=IsInstance(dict), body=None,
                                require_response=False)
```

**Mock / spy** -- composable mocks with import-site patching:

```python
# Mock a module-level attribute
cache_mock = bigfoot.mock("myapp.services:cache")
cache_mock.get.returns("cached_value")

# Mock an attribute on a specific object
mock = bigfoot.mock.object(my_module, "service")

# Spy on real implementation
spy = bigfoot.spy("myapp.services:cache")
```

**Context managers** -- sandbox activates all mocks and enforces assertions:

```python
# Sandbox activates all mocks, enforces assertions
with bigfoot.sandbox():
    result = code_under_test()

# Individual activation (no assertion enforcement)
with cache_mock:
    setup_code()
```

**Error mocking** -- mock exceptions and assert error interactions:

```python
# Mock errors
bigfoot.http.mock_error("GET", url, raises=httpx.ConnectError("refused"))

# Assert errors
bigfoot.http.assert_request("GET", url, headers=..., body="",
                            raised=IsInstance(httpx.ConnectError))
```

**Spy observability** -- assert return values and raised exceptions:

```python
spy.assert_call(args=("key",), kwargs={}, returned="value")
spy.assert_call(args=("bad",), kwargs={}, raised=IsInstance(KeyError))
```

**Pass-through** -- delegate to the real service, still record and require assertion:

```python
bigfoot.http.pass_through("GET", url)
```

**Configuration** via `pyproject.toml`:

```toml
[tool.bigfoot.http]
require_response = true  # This is the default; set to false to opt out
```

Per-call arguments override project-level settings. See the [configuration guide](https://axiomantic.github.io/bigfoot/guides/configuration/).

## Selective Installation

`bigfoot[all]` installs everything. For a smaller footprint, pick only what you need:

```bash
pip install bigfoot                       # Core plugins (no optional deps)
pip install bigfoot[http]                 # + httpx, requests, urllib
pip install bigfoot[aiohttp]              # + aiohttp
pip install bigfoot[redis]                # + Redis
pip install bigfoot[pymemcache]           # + Memcached
pip install bigfoot[pymongo]              # + MongoDB
pip install bigfoot[elasticsearch]        # + Elasticsearch/OpenSearch
pip install bigfoot[psycopg2]             # + PostgreSQL (psycopg2)
pip install bigfoot[asyncpg]              # + PostgreSQL (asyncpg)
pip install bigfoot[boto3]                # + AWS SDK
pip install bigfoot[pika]                 # + RabbitMQ
pip install bigfoot[celery]               # + Celery tasks
pip install bigfoot[grpc]                 # + gRPC
pip install bigfoot[paramiko]             # + SSH
pip install bigfoot[jwt]                  # + PyJWT
pip install bigfoot[crypto]               # + cryptography
pip install bigfoot[cffi]                 # + cffi (C FFI)
pip install bigfoot[websockets]           # + async WebSocket
pip install bigfoot[websocket-client]     # + sync WebSocket
pip install bigfoot[matchers]             # + dirty-equals matchers
```

## Documentation

Full API reference, plugin guides, and advanced usage: **[axiomantic.github.io/bigfoot](https://axiomantic.github.io/bigfoot/)**

## License

MIT
