# SubprocessPlugin Guide

`SubprocessPlugin` intercepts `subprocess.run` and `shutil.which` globally during a sandbox. It is included in core tripwire — no extra required.

## Setup

In pytest, access `SubprocessPlugin` through the `tripwire.subprocess_mock` proxy. It auto-creates the plugin for the current test on first use — no explicit instantiation needed:

```python
import tripwire

def test_build():
    tripwire.subprocess_mock.mock_run(["make", "all"], returncode=0)

    with tripwire:
        run_build()

    tripwire.subprocess_mock.assert_run(command=["make", "all"], returncode=0, stdout="", stderr="")
```

For manual use outside pytest, construct `SubprocessPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.subprocess import SubprocessPlugin

verifier = StrictVerifier()
sp = SubprocessPlugin(verifier)
```

Each verifier may have at most one `SubprocessPlugin`. A second `SubprocessPlugin(verifier)` raises `ValueError`.

## Registering `subprocess.run` mocks

Use `tripwire.subprocess_mock.mock_run(command, ...)` to register a mock before entering the sandbox:

```python
tripwire.subprocess_mock.mock_run(["git", "status"], returncode=0, stdout="On branch main\n")
```

Parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `command` | `list[str]` | required | Full command list, matched exactly in FIFO order |
| `returncode` | `int` | `0` | Return code of the completed process |
| `stdout` | `str` | `""` | Captured stdout |
| `stderr` | `str` | `""` | Captured stderr |
| `raises` | `BaseException \| None` | `None` | Exception to raise after recording the interaction |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

## FIFO ordering for `subprocess.run`

`subprocess.run` uses a strict FIFO queue. Each registered mock is consumed in registration order. If code calls `subprocess.run` with a command that does not match the next entry in the queue, `UnmockedInteractionError` is raised immediately at call time.

```python
tripwire.subprocess_mock.mock_run(["git", "fetch"], returncode=0)
tripwire.subprocess_mock.mock_run(["git", "merge", "origin/main"], returncode=0)
# The first subprocess.run call must be ["git", "fetch"],
# the second must be ["git", "merge", "origin/main"].
```

Calling `subprocess.run` with an unregistered command or in the wrong order raises `UnmockedInteractionError`.

## Asserting `subprocess.run` interactions

Use `tripwire.subprocess_mock.assert_run()` to assert subprocess interactions:

```python
tripwire.subprocess_mock.assert_run(command=["git", "fetch"], returncode=0, stdout="", stderr="")
tripwire.subprocess_mock.assert_run(command=["git", "merge", "origin/main"], returncode=0, stdout="", stderr="")
```

`assert_run()` is a convenience wrapper around the lower-level `assert_interaction()` call:

```python
# Convenience (recommended):
tripwire.subprocess_mock.assert_run(command=["git", "fetch"], returncode=0, stdout="", stderr="")

# Equivalent low-level call:
tripwire.assert_interaction(tripwire.subprocess_mock.run, command=["git", "fetch"],
                           returncode=0, stdout="", stderr="")
```

## Registering `shutil.which` mocks

Use `tripwire.subprocess_mock.mock_which(name, returns, ...)` to register a mock before entering the sandbox:

```python
tripwire.subprocess_mock.mock_which("git", returns="/usr/bin/git")
tripwire.subprocess_mock.mock_which("svn", returns=None)  # simulate not found
```

Parameters:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Binary name to match (e.g., `"git"`, `"docker"`) |
| `returns` | `str \| None` | required | Path returned by `shutil.which`, or `None` to simulate not found |
| `required` | `bool` | `False` | Whether an uncalled mock causes `UnusedMocksError` at teardown |

## Semi-permissive behavior for `shutil.which`

`shutil.which` is semi-permissive. Unregistered names return `None` silently — no `UnmockedInteractionError`. Only registered names record interactions on the timeline.

This differs from `subprocess.run`, which enforces a strict queue. The rationale: code often probes for optional binaries whose absence is a normal, handled case. Requiring mocks for every probe would force tests to enumerate every binary the code might check, including ones irrelevant to the scenario under test.

## Asserting `shutil.which` interactions

Use `tripwire.subprocess_mock.assert_which()` to assert `shutil.which` interactions:

```python
tripwire.subprocess_mock.assert_which(name="git", returns="/usr/bin/git")
```

`assert_which()` is a convenience wrapper around the lower-level `assert_interaction()` call:

```python
# Convenience (recommended):
tripwire.subprocess_mock.assert_which(name="git", returns="/usr/bin/git")

# Equivalent low-level call:
tripwire.assert_interaction(tripwire.subprocess_mock.which, name="git", returns="/usr/bin/git")
```

Only registered names record interactions. Calls to unregistered names are not recorded and cannot be asserted.

## Activating without mocks

`subprocess_mock.install()` activates the bouncer with no mocks registered. Any call to `subprocess.run` during the sandbox will raise `UnmockedInteractionError` immediately. Use this when you want to assert that a code path does not call subprocess at all:

```python
def test_no_subprocess_calls():
    tripwire.subprocess_mock.install()  # any subprocess.run call will raise UnmockedInteractionError

    with tripwire:
        result = function_that_should_not_call_subprocess()

    assert result == expected
```

`shutil.which` remains semi-permissive even after `install()`: unregistered names still return `None` silently.

## ConflictError

At sandbox entry, `SubprocessPlugin` checks whether `subprocess.run` or `shutil.which` have already been patched by another library. If either has been modified by a third party, tripwire raises `ConflictError`:

```
ConflictError: target='subprocess.run', patcher='unknown'
```

Nested tripwire sandboxes use reference counting and do not conflict with each other.

## Full example

**Production code** (`examples/cli_tool/app.py`):

```python
--8<-- "examples/cli_tool/app.py"
```

**Test** (`examples/cli_tool/test_app.py`):

```python
--8<-- "examples/cli_tool/test_app.py"
```
