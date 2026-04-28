# Installation

## Quick start

Install everything:

```bash
pip install python-tripwire[all]
```

This includes all plugins and their optional dependencies (httpx, requests, aiohttp, websockets, websocket-client, redis, psycopg2, asyncpg, dirty-equals).

## Selective installation

For a more compact installation, pick only the extras you need:

```bash
pip install python-tripwire                       # Core plugins (no extra deps)
pip install python-tripwire[http]                 # + HttpPlugin (httpx, requests, urllib)
pip install python-tripwire[aiohttp]              # + aiohttp support for HttpPlugin
pip install python-tripwire[psycopg2]             # + Psycopg2Plugin (PostgreSQL)
pip install python-tripwire[asyncpg]              # + AsyncpgPlugin (async PostgreSQL)
pip install python-tripwire[websockets]           # + AsyncWebSocketPlugin
pip install python-tripwire[websocket-client]     # + SyncWebSocketPlugin
pip install python-tripwire[redis]                # + RedisPlugin
pip install python-tripwire[matchers]             # + dirty-equals matchers
```

### Core plugins (no extra dependencies)

These plugins are always available with a bare `pip install python-tripwire`:

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
pip install python-tripwire[matchers]
```

## pytest fixture

The `tripwire_verifier` pytest fixture is registered automatically via the `pytest11` entry point. No `conftest.py` changes are needed:

```python
def test_example(tripwire_verifier):
    # tripwire_verifier is a StrictVerifier with automatic verify_all() at teardown
    ...
```

Or use the context manager directly:

```python
import tripwire

def test_example():
    with tripwire:
        ...  # all enabled plugins active
```

## Guard Mode

tripwire activates guard mode by default. When your tests make real I/O calls
(HTTP requests, database queries, subprocess calls, etc.) outside a tripwire
sandbox, you will see warnings like:

```
GuardedCallWarning: 'http:request' called outside sandbox.
```

This is expected and does not break your tests. The warnings show you which
calls are unguarded so you can decide how to handle them:

- **Silence a specific plugin:** `@pytest.mark.allow("http")` on the test
- **Silence all warnings:** `warnings.filterwarnings("ignore", category=GuardedCallWarning)`
- **Enforce strict mode:** Set `guard = "error"` in `[tool.tripwire]` in `pyproject.toml`

See the [Guard Mode guide](guard-mode.md) for full details.
