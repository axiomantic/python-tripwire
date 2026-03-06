# AsyncSubprocessPlugin Guide

`AsyncSubprocessPlugin` intercepts `asyncio.create_subprocess_exec` and `asyncio.create_subprocess_shell` by replacing them with fake implementations that route process lifecycle through a session script. It is included in core bigfoot -- no extra required.

## Relationship to PopenPlugin

`PopenPlugin` patches synchronous `subprocess.Popen`. `AsyncSubprocessPlugin` patches async `asyncio.create_subprocess_exec` and `asyncio.create_subprocess_shell`. The two plugins target independent names and do not interfere with each other. Both can be active in the same sandbox simultaneously.

## Setup

In pytest, access `AsyncSubprocessPlugin` through the `bigfoot.async_subprocess_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import asyncio
import bigfoot

async def test_run_command():
    (bigfoot.async_subprocess_mock
        .new_session()
        .expect("spawn",       returns=None)
        .expect("communicate", returns=(b"hello\n", b"", 0)))

    with bigfoot:
        proc = await asyncio.create_subprocess_exec("echo", "hello")
        stdout, stderr = await proc.communicate()

    assert stdout == b"hello\n"
    assert proc.returncode == 0

    bigfoot.async_subprocess_mock.assert_spawn(command=["echo", "hello"], stdin=None)
    bigfoot.async_subprocess_mock.assert_communicate(input=None)
```

For manual use outside pytest, construct `AsyncSubprocessPlugin` explicitly:

```python
from bigfoot import StrictVerifier
from bigfoot.plugins.async_subprocess_plugin import AsyncSubprocessPlugin

verifier = StrictVerifier()
plugin = AsyncSubprocessPlugin(verifier)
```

## State machine

```
created --spawn (create_subprocess_exec/shell call)--> running --communicate--> terminated
                                                       running --wait--> terminated
```

The `spawn` step fires automatically during `asyncio.create_subprocess_exec(...)` or `asyncio.create_subprocess_shell(...)`. After that, either `communicate()` or `wait()` terminates the process.

## Exec vs Shell

- `create_subprocess_exec("program", "arg1", "arg2")` records `command` as `["program", "arg1", "arg2"]` (a list).
- `create_subprocess_shell("echo hello | tr a-z A-Z")` records `command` as `"echo hello | tr a-z A-Z"` (a string).

## Scripting a session

Use `new_session()` to create a `SessionHandle` and chain `.expect()` calls to build the script:

```python
(bigfoot.async_subprocess_mock
    .new_session()
    .expect("spawn",       returns=None)
    .expect("communicate", returns=(b"output", b"errors", 0)))
```

### `expect()` parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `method` | `str` | required | Step name: `"spawn"`, `"communicate"`, or `"wait"` |
| `returns` | `Any` | required | Value returned by the step (see below) |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused step causes `UnusedMocksError` at teardown |

### Return values by step

| Step | `returns` type | Description |
|---|---|---|
| `spawn` | `None` | No return value; the fake process object is constructed |
| `communicate` | `tuple[bytes, bytes, int]` | `(stdout, stderr, returncode)` |
| `wait` | `int` | The process return code |

## Asserting interactions

Each step records an interaction on the timeline. Use the typed assertion helpers on `bigfoot.async_subprocess_mock`:

### `assert_spawn(*, command, stdin)`

Asserts the next spawn interaction. Both `command` and `stdin` are required fields.

```python
# For exec:
bigfoot.async_subprocess_mock.assert_spawn(command=["git", "status"], stdin=None)

# For shell:
bigfoot.async_subprocess_mock.assert_spawn(command="ls -la | grep foo", stdin=None)
```

### `assert_communicate(*, input)`

Asserts the next communicate interaction. The `input` field is required.

```python
bigfoot.async_subprocess_mock.assert_communicate(input=None)
```

### `assert_wait()`

Asserts the next wait interaction. No fields are required.

```python
bigfoot.async_subprocess_mock.assert_wait()
```

## Full example: exec with communicate()

```python
import asyncio
import bigfoot

async def run_linter(path):
    proc = await asyncio.create_subprocess_exec(
        "ruff", "check", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode()

async def test_linter_clean():
    (bigfoot.async_subprocess_mock
        .new_session()
        .expect("spawn",       returns=None)
        .expect("communicate", returns=(b"All checks passed.\n", b"", 0)))

    with bigfoot:
        rc, output = await run_linter("src/")

    assert rc == 0
    assert output == "All checks passed.\n"

    bigfoot.async_subprocess_mock.assert_spawn(
        command=["ruff", "check", "src/"], stdin=None
    )
    bigfoot.async_subprocess_mock.assert_communicate(input=None)
```

## Full example: shell with communicate()

```python
async def test_shell_pipeline():
    (bigfoot.async_subprocess_mock
        .new_session()
        .expect("spawn",       returns=None)
        .expect("communicate", returns=(b"HELLO\n", b"", 0)))

    with bigfoot:
        proc = await asyncio.create_subprocess_shell("echo hello | tr a-z A-Z")
        stdout, stderr = await proc.communicate()

    assert stdout == b"HELLO\n"

    bigfoot.async_subprocess_mock.assert_spawn(
        command="echo hello | tr a-z A-Z", stdin=None
    )
    bigfoot.async_subprocess_mock.assert_communicate(input=None)
```

## Full example: wait()

```python
async def test_wait_for_process():
    (bigfoot.async_subprocess_mock
        .new_session()
        .expect("spawn", returns=None)
        .expect("wait",  returns=0))

    with bigfoot:
        proc = await asyncio.create_subprocess_exec("sleep", "1")
        rc = await proc.wait()

    assert rc == 0
    assert proc.returncode == 0

    bigfoot.async_subprocess_mock.assert_spawn(command=["sleep", "1"], stdin=None)
    bigfoot.async_subprocess_mock.assert_wait()
```

## ConflictError

At sandbox entry, `AsyncSubprocessPlugin` checks whether `asyncio.create_subprocess_exec` and `asyncio.create_subprocess_shell` have already been patched by another library. If either has been modified by a third party, bigfoot raises `ConflictError`:

```
ConflictError: target='asyncio.create_subprocess_exec', patcher='unittest.mock'
```

Nested bigfoot sandboxes use reference counting and do not conflict with each other.
