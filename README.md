# bigfoot

[![CI](https://github.com/axiomantic/bigfoot/actions/workflows/ci.yml/badge.svg)](https://github.com/axiomantic/bigfoot/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bigfoot)](https://pypi.org/project/bigfoot/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Every call recorded. Every field asserted. Zero guesswork.**

bigfoot intercepts every external call your code makes and forces your tests to account for all of them. It ships with plugins for HTTP, subprocess, database, socket, Redis, SMTP, WebSocket, logging, and more. It enforces three guarantees that most mocking libraries leave silent:

1. **Every call must be pre-authorized.** Code makes a call with no registered mock? `UnmockedInteractionError`, immediately.
2. **Every recorded interaction must be explicitly asserted.** Forget to assert an interaction? `UnassertedInteractionsError` at teardown.
3. **Every registered mock must actually be triggered.** Register a mock that never fires? `UnusedMocksError` at teardown.

**Guard mode** (enabled by default) goes further: bigfoot installs interceptors at test session startup, blocking any real I/O call that happens outside a sandbox. Accidental network calls, database connections, and subprocess invocations are caught immediately with `GuardedCallError`, not silently sent to production. Use `bigfoot.allow("dns", "socket")` or `@pytest.mark.allow(...)` to selectively permit real calls when needed. Use `bigfoot.deny(...)` or `@pytest.mark.deny(...)` to narrow the allowlist and re-guard specific plugins in nested contexts.

A plugin system makes it straightforward to intercept any service and enforce all three guarantees.

```bash
pip install bigfoot[all]
```

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
    )
    assert result["id"] == "ch_123"
```

If you forget the `assert_request()` call, bigfoot fails the test at teardown:

```
E   bigfoot._errors.UnassertedInteractionsError: 1 interaction(s) were not asserted
E
E     [sequence=0] [HttpPlugin] POST https://api.stripe.com/v1/charges (status=200)
E       To assert this interaction:
E         http.assert_request(
E       "POST",
E       "https://api.stripe.com/v1/charges",
E       headers={'host': 'api.stripe.com', ...},
E       body='{"amount":5000}',
E   )
```

Every field is shown. Every value is real. Copy, paste, done.

## How It Works

1. **Register mocks** before the sandbox (`mock_response`, `mock_run`, `returns`, etc.)
2. **Open the sandbox** with `with bigfoot:` (or `async with bigfoot:`)
3. **Code runs normally** inside the sandbox, but external calls are intercepted and recorded
4. **Assert interactions** after the sandbox closes, in order
5. **`verify_all()`** runs automatically at test teardown via the pytest plugin

No fixture injection required. Install bigfoot, `import bigfoot`, and go.

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
svc = bigfoot.mock("PaymentService")
svc.charge.returns({"status": "ok"})
```

</details>

## Advanced Features

**Concurrent assertions** -- relax FIFO ordering for parallel requests:

```python
with bigfoot.in_any_order():
    bigfoot.http.assert_request(method="GET", url=".../a", headers=IsInstance(dict), body=None)
    bigfoot.http.assert_request(method="GET", url=".../b", headers=IsInstance(dict), body=None)
```

**Spy / pass-through** -- delegate to the real object, still record and require assertion:

```python
bigfoot.spy("Name", real_obj)
bigfoot.http.pass_through("GET", url)
```

**Configuration** via `pyproject.toml`:

```toml
[tool.bigfoot.http]
require_response = true  # Every assert_request() must be followed by .assert_response()
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
