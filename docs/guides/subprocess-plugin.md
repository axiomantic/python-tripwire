# SubprocessPlugin Guide

`SubprocessPlugin` intercepts `subprocess.run` and `shutil.which` globally during a sandbox. It is included in core bigfoot — no extra required.

## Setup

In pytest, access `SubprocessPlugin` through the `bigfoot.subprocess_mock` proxy. It auto-creates the plugin for the current test on first use — no explicit instantiation needed:

```python
import bigfoot

def test_build():
    bigfoot.subprocess_mock.mock_run(["make", "all"], returncode=0)

    with bigfoot.sandbox():
        run_build()

    bigfoot.assert_interaction(bigfoot.subprocess_mock.run, command=["make", "all"])
```

For manual use outside pytest, construct `SubprocessPlugin` explicitly:

```python
from bigfoot import StrictVerifier
from bigfoot.plugins.subprocess import SubprocessPlugin

verifier = StrictVerifier()
sp = SubprocessPlugin(verifier)
```

Each verifier may have at most one `SubprocessPlugin`. A second `SubprocessPlugin(verifier)` raises `ValueError`.

## Registering `subprocess.run` mocks

Use `bigfoot.subprocess_mock.mock_run(command, ...)` to register a mock before entering the sandbox:

```python
bigfoot.subprocess_mock.mock_run(["git", "status"], returncode=0, stdout="On branch main\n")
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
bigfoot.subprocess_mock.mock_run(["git", "fetch"], returncode=0)
bigfoot.subprocess_mock.mock_run(["git", "merge", "origin/main"], returncode=0)
# The first subprocess.run call must be ["git", "fetch"],
# the second must be ["git", "merge", "origin/main"].
```

Calling `subprocess.run` with an unregistered command or in the wrong order raises `UnmockedInteractionError`.

## Asserting `subprocess.run` interactions

Use `bigfoot.subprocess_mock.run` as the source in `assert_interaction()`. The `command` field is the sole assertable field:

```python
bigfoot.assert_interaction(bigfoot.subprocess_mock.run, command=["git", "fetch"])
bigfoot.assert_interaction(bigfoot.subprocess_mock.run, command=["git", "merge", "origin/main"])
```

## Registering `shutil.which` mocks

Use `bigfoot.subprocess_mock.mock_which(name, returns, ...)` to register a mock before entering the sandbox:

```python
bigfoot.subprocess_mock.mock_which("git", returns="/usr/bin/git")
bigfoot.subprocess_mock.mock_which("svn", returns=None)  # simulate not found
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

Use `bigfoot.subprocess_mock.which` as the source in `assert_interaction()`. The `name` field is the sole assertable field:

```python
bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="git")
```

Only registered names record interactions. Calls to unregistered names are not recorded and cannot be asserted.

## Activating without mocks

`subprocess_mock.install()` activates the bouncer with no mocks registered. Any call to `subprocess.run` during the sandbox will raise `UnmockedInteractionError` immediately. Use this when you want to assert that a code path does not call subprocess at all:

```python
def test_no_subprocess_calls():
    bigfoot.subprocess_mock.install()  # any subprocess.run call will raise UnmockedInteractionError

    with bigfoot.sandbox():
        result = function_that_should_not_call_subprocess()

    assert result == expected
```

`shutil.which` remains semi-permissive even after `install()`: unregistered names still return `None` silently.

## ConflictError

At sandbox entry, `SubprocessPlugin` checks whether `subprocess.run` or `shutil.which` have already been patched by another library. If either has been modified by a third party, bigfoot raises `ConflictError`:

```
ConflictError: target='subprocess.run', patcher='unknown'
```

Nested bigfoot sandboxes use reference counting and do not conflict with each other.

## Full example

```python
import bigfoot

def deploy():
    import shutil, subprocess
    git = shutil.which("git")
    subprocess.run([git, "pull", "--ff-only"], check=True)
    subprocess.run([git, "tag", "v1.0"], check=True)

def test_deploy():
    bigfoot.subprocess_mock.mock_which("git", returns="/usr/bin/git")
    bigfoot.subprocess_mock.mock_run(["/usr/bin/git", "pull", "--ff-only"], returncode=0, stdout="Already up to date.\n")
    bigfoot.subprocess_mock.mock_run(["/usr/bin/git", "tag", "v1.0"], returncode=0)

    with bigfoot.sandbox():
        deploy()

    bigfoot.assert_interaction(bigfoot.subprocess_mock.which, name="git")
    bigfoot.assert_interaction(bigfoot.subprocess_mock.run, command=["/usr/bin/git", "pull", "--ff-only"])
    bigfoot.assert_interaction(bigfoot.subprocess_mock.run, command=["/usr/bin/git", "tag", "v1.0"])
    # verify_all() runs automatically at test teardown
```
