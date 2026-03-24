# Installation

## Quick start

Install everything:

```bash
pip install bigfoot[all]
```

This includes all plugins and their optional dependencies (httpx, requests, aiohttp, websockets, websocket-client, redis, psycopg2, asyncpg, dirty-equals).

## Selective installation

For a more compact installation, pick only the extras you need:

```bash
pip install bigfoot                       # Core plugins (no extra deps)
pip install bigfoot[http]                 # + HttpPlugin (httpx, requests, urllib)
pip install bigfoot[aiohttp]              # + aiohttp support for HttpPlugin
pip install bigfoot[psycopg2]             # + Psycopg2Plugin (PostgreSQL)
pip install bigfoot[asyncpg]              # + AsyncpgPlugin (async PostgreSQL)
pip install bigfoot[websockets]           # + AsyncWebSocketPlugin
pip install bigfoot[websocket-client]     # + SyncWebSocketPlugin
pip install bigfoot[redis]                # + RedisPlugin
pip install bigfoot[matchers]             # + dirty-equals matchers
```

### Core plugins (no extra dependencies)

These plugins are always available with a bare `pip install bigfoot`:

- `MockPlugin` -- general-purpose mock objects
- `SubprocessPlugin` -- `subprocess.run` and `shutil.which`
- `PopenPlugin` -- `subprocess.Popen`
- `AsyncSubprocessPlugin` -- `asyncio.create_subprocess_exec/shell`
- `DatabasePlugin` -- `sqlite3.connect`
- `SmtpPlugin` -- `smtplib.SMTP`
- `SocketPlugin` -- `socket.socket`
- `LoggingPlugin` -- `logging.Logger`

### Matcher support

[dirty-equals](https://dirty-equals.helpmanual.io/) matchers can be used as expected field values in assertions:

```bash
pip install bigfoot[matchers]
```

## pytest fixture

The `bigfoot_verifier` pytest fixture is registered automatically via the `pytest11` entry point. No `conftest.py` changes are needed:

```python
def test_example(bigfoot_verifier):
    # bigfoot_verifier is a StrictVerifier with automatic verify_all() at teardown
    ...
```

Or use the context manager directly:

```python
import bigfoot

def test_example():
    with bigfoot:
        ...  # all enabled plugins active
```

## Guard Mode

bigfoot activates guard mode by default. When your tests make real I/O calls
(HTTP requests, database queries, subprocess calls, etc.) outside a bigfoot
sandbox, you will see warnings like:

```
GuardedCallWarning: 'http:request' called outside sandbox.
```

This is expected and does not break your tests. The warnings show you which
calls are unguarded so you can decide how to handle them:

- **Silence a specific plugin:** `@pytest.mark.allow("http")` on the test
- **Silence all warnings:** `warnings.filterwarnings("ignore", category=GuardedCallWarning)`
- **Enforce strict mode:** Set `guard = "error"` in `[tool.bigfoot]` in `pyproject.toml`

See the [Guard Mode guide](guard-mode.md) for full details.
