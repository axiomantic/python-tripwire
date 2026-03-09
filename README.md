# bigfoot

[![CI](https://github.com/axiomantic/bigfoot/actions/workflows/ci.yml/badge.svg)](https://github.com/axiomantic/bigfoot/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bigfoot)](https://pypi.org/project/bigfoot/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Every call recorded. Every field asserted. Zero guesswork.**

bigfoot intercepts every external call your code makes -- HTTP, subprocess, database, socket, Redis, SMTP, WebSocket, logging -- and forces your tests to account for all of them. Forget to assert an interaction? Test fails. Register a mock that never fires? Test fails. Make a call you didn't mock? Test fails immediately.

```bash
pip install bigfoot[all]
```

## Quick Start

```python
import bigfoot
import httpx
from dirty_equals import IsInstance

def test_payment_flow():
    bigfoot.http.mock_response("POST", "https://api.stripe.com/v1/charges",
                               json={"id": "ch_123"}, status=200)

    with bigfoot:
        response = httpx.post("https://api.stripe.com/v1/charges",
                              json={"amount": 5000})

    bigfoot.http.assert_request(
        method="POST", url="https://api.stripe.com/v1/charges",
        headers=IsInstance(dict), body=IsInstance(str),
    )
    assert response.json()["id"] == "ch_123"
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

bigfoot ships with 14 plugins covering the most common external dependencies:

| Category | Plugins | Intercepts |
|----------|---------|------------|
| **General** | [MockPlugin](https://axiomantic.github.io/bigfoot/guides/mock-plugin/), [LoggingPlugin](https://axiomantic.github.io/bigfoot/guides/logging-plugin/) | Named mock proxies, `logging` module |
| **HTTP** | [HttpPlugin](https://axiomantic.github.io/bigfoot/guides/http-plugin/) | `httpx`, `requests`, `urllib`, `aiohttp` |
| **Subprocess** | [SubprocessPlugin](https://axiomantic.github.io/bigfoot/guides/subprocess-plugin/), [PopenPlugin](https://axiomantic.github.io/bigfoot/guides/popen-plugin/), [AsyncSubprocessPlugin](https://axiomantic.github.io/bigfoot/guides/async-subprocess-plugin/) | `subprocess.run`, `shutil.which`, `Popen`, `asyncio.create_subprocess_*` |
| **Database** | [DatabasePlugin](https://axiomantic.github.io/bigfoot/guides/database-plugin/), [Psycopg2Plugin](https://axiomantic.github.io/bigfoot/guides/psycopg2-plugin/), [AsyncpgPlugin](https://axiomantic.github.io/bigfoot/guides/asyncpg-plugin/) | `sqlite3`, `psycopg2`, `asyncpg` |
| **Network** | [SmtpPlugin](https://axiomantic.github.io/bigfoot/guides/smtp-plugin/), [SocketPlugin](https://axiomantic.github.io/bigfoot/guides/socket-plugin/), [RedisPlugin](https://axiomantic.github.io/bigfoot/guides/redis-plugin/), [WebSocket](https://axiomantic.github.io/bigfoot/guides/websocket-plugin/) | `smtplib`, `socket`, `redis`, `websockets`, `websocket-client` |

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
pip install bigfoot[psycopg2]             # + PostgreSQL (psycopg2)
pip install bigfoot[asyncpg]              # + PostgreSQL (asyncpg)
pip install bigfoot[websockets]           # + async WebSocket
pip install bigfoot[websocket-client]     # + sync WebSocket
pip install bigfoot[redis]                # + Redis
pip install bigfoot[matchers]             # + dirty-equals matchers
```

## Documentation

Full API reference, plugin guides, and advanced usage: **[axiomantic.github.io/bigfoot](https://axiomantic.github.io/bigfoot/)**

## License

MIT
