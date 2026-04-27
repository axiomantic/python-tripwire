# PopenPlugin Guide

`PopenPlugin` intercepts `subprocess.Popen` by replacing the class with a fake that routes process lifecycle through a session script. It is included in core tripwire -- no extra required.

## Coexistence with SubprocessPlugin

`SubprocessPlugin` patches `subprocess.run` and `shutil.which`. `PopenPlugin` patches `subprocess.Popen`. The two plugins target independent names in the subprocess module and do not interfere with each other. Both can be active in the same sandbox simultaneously.

## Setup

In pytest, access `PopenPlugin` through the `tripwire.popen_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_run_command():
    (tripwire.popen_mock
        .new_session()
        .expect("spawn",       returns=None)
        .expect("communicate", returns=(b"hello\n", b"", 0)))

    with tripwire:
        import subprocess
        proc = subprocess.Popen(["echo", "hello"], stdout=subprocess.PIPE)
        stdout, stderr = proc.communicate()

    assert stdout == b"hello\n"
    assert proc.returncode == 0

    tripwire.popen_mock.assert_spawn(command=["echo", "hello"], stdin=None)
    tripwire.popen_mock.assert_communicate(input=None)
```

For manual use outside pytest, construct `PopenPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.popen_plugin import PopenPlugin

verifier = StrictVerifier()
popen = PopenPlugin(verifier)
```

Each verifier may have at most one `PopenPlugin`. A second `PopenPlugin(verifier)` raises `ValueError`.

## State machine

```
created --spawn (Popen() call)--> running --communicate--> terminated
                                  running --wait--> terminated
```

The `spawn` step fires automatically during `subprocess.Popen(...)` construction. After that, either `communicate()` or `wait()` terminates the process.

## Scripting a session

Use `new_session()` to create a `SessionHandle` and chain `.expect()` calls to build the script:

```python
(tripwire.popen_mock
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
| `spawn` | `None` | No return value; the `Popen` object is constructed |
| `communicate` | `tuple[bytes, bytes, int]` | `(stdout, stderr, returncode)` |
| `wait` | `int` | The process return code |

## Asserting interactions

Each step records an interaction on the timeline. Use the typed assertion helpers on `tripwire.popen_mock`:

### `assert_spawn(*, command, stdin)`

Asserts the next spawn interaction. Both `command` and `stdin` are required fields.

```python
tripwire.popen_mock.assert_spawn(command=["git", "status"], stdin=None)
```

### `assert_communicate(*, input)`

Asserts the next communicate interaction. The `input` field is required.

```python
tripwire.popen_mock.assert_communicate(input=None)
```

### `assert_wait()`

Asserts the next wait interaction. No fields are required.

```python
tripwire.popen_mock.assert_wait()
```

## Full example

**Production code** (`examples/popen_example/app.py`):

```python
--8<-- "examples/popen_example/app.py"
```

**Test** (`examples/popen_example/test_app.py`):

```python
--8<-- "examples/popen_example/test_app.py"
```

## Non-zero exit code

```python
def test_failing_command():
    (tripwire.popen_mock
        .new_session()
        .expect("spawn",       returns=None)
        .expect("communicate", returns=(b"", b"command not found\n", 127)))

    with tripwire:
        proc = subprocess.Popen(["bogus-cmd"])
        stdout, stderr = proc.communicate()

    assert proc.returncode == 127
    assert stderr == b"command not found\n"

    tripwire.popen_mock.assert_spawn(command=["bogus-cmd"], stdin=None)
    tripwire.popen_mock.assert_communicate(input=None)
```

## Passing input to communicate()

```python
def test_communicate_with_input():
    (tripwire.popen_mock
        .new_session()
        .expect("spawn",       returns=None)
        .expect("communicate", returns=(b"response\n", b"", 0)))

    with tripwire:
        proc = subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        stdout, stderr = proc.communicate(input=b"hello\n")

    assert stdout == b"response\n"

    tripwire.popen_mock.assert_spawn(command=["cat"], stdin=None)
    tripwire.popen_mock.assert_communicate(input=b"hello\n")
```

## ConflictError

At sandbox entry, `PopenPlugin` checks whether `subprocess.Popen` has already been patched by another library. If it has been modified by a third party (unittest.mock, pytest-mock, or an unknown library), tripwire raises `ConflictError`:

```
ConflictError: target='subprocess.Popen', patcher='unittest.mock'
```

Nested tripwire sandboxes use reference counting and do not conflict with each other.
