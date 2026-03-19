# FileIoPlugin Guide

`FileIoPlugin` intercepts file system operations across `builtins.open`, `pathlib.Path` read/write methods, `os` file operations, and `shutil` copy/remove operations. Each operation+path combination has its own independent FIFO queue. The plugin uses a `ContextVar`-based reentrancy guard to prevent self-interference with bigfoot's own file I/O.

**Important:** `FileIoPlugin` is always available (no extra install required) but is NOT default enabled. You must explicitly enable it via `enabled_plugins = ["file_io"]` in your bigfoot config, or access it through the `bigfoot.file_io_mock` proxy.

## Setup

In pytest, access `FileIoPlugin` through the `bigfoot.file_io_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import bigfoot

def test_read_config():
    bigfoot.file_io_mock.mock_operation(
        "read_text", "/etc/myapp/config.yaml",
        returns="database:\n  host: localhost\n  port: 5432",
    )

    with bigfoot:
        from pathlib import Path
        config = Path("/etc/myapp/config.yaml").read_text()

    assert "localhost" in config

    bigfoot.file_io_mock.assert_read_text(path="/etc/myapp/config.yaml")
```

For manual use outside pytest, construct `FileIoPlugin` explicitly:

```python
from bigfoot import StrictVerifier
from bigfoot.plugins.file_io_plugin import FileIoPlugin

verifier = StrictVerifier()
file_io_mock = FileIoPlugin(verifier)
```

Each verifier may have at most one `FileIoPlugin`. A second `FileIoPlugin(verifier)` raises `ValueError`.

## Registering mocks

Use `bigfoot.file_io_mock.mock_operation(operation, path_pattern, *, returns, ...)` to register a mock before entering the sandbox:

```python
bigfoot.file_io_mock.mock_operation("open", "/tmp/data.csv", returns="id,name\n1,Alice")
bigfoot.file_io_mock.mock_operation("remove", "/tmp/data.csv", returns=None)
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `operation` | `str` | required | File operation name (see supported operations below) |
| `path_pattern` | `str` | required | Path to match against |
| `returns` | `Any` | `None` | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### Supported operations

| Operation | Intercepts | Details fields |
|---|---|---|
| `open` | `builtins.open(...)` | `path`, `mode`, `encoding` |
| `read_text` | `Path.read_text(...)` | `path` |
| `read_bytes` | `Path.read_bytes(...)` | `path` |
| `write_text` | `Path.write_text(...)` | `path`, `data` |
| `write_bytes` | `Path.write_bytes(...)` | `path`, `data` |
| `remove` | `os.remove(...)` | `path` |
| `unlink` | `os.unlink(...)` | `path` |
| `rename` | `os.rename(...)` | `src`, `dst` |
| `replace` | `os.replace(...)` | `src`, `dst` |
| `makedirs` | `os.makedirs(...)` | `path`, `exist_ok` |
| `mkdir` | `os.mkdir(...)` | `path` |
| `copy` | `shutil.copy(...)` | `src`, `dst` |
| `copy2` | `shutil.copy2(...)` | `src`, `dst` |
| `copytree` | `shutil.copytree(...)` | `src`, `dst` |
| `rmtree` | `shutil.rmtree(...)` | `path` |

## FIFO queues

Each operation+path combination has its own independent FIFO queue. Multiple mocks for the same operation and path are consumed in registration order:

```python
def test_multiple_reads():
    bigfoot.file_io_mock.mock_operation("read_text", "/etc/myapp/config.yaml", returns="v1")
    bigfoot.file_io_mock.mock_operation("read_text", "/etc/myapp/config.yaml", returns="v2")

    with bigfoot:
        from pathlib import Path
        first = Path("/etc/myapp/config.yaml").read_text()
        second = Path("/etc/myapp/config.yaml").read_text()

    assert first == "v1"
    assert second == "v2"

    bigfoot.file_io_mock.assert_read_text(path="/etc/myapp/config.yaml")
    bigfoot.file_io_mock.assert_read_text(path="/etc/myapp/config.yaml")
```

## Asserting interactions

Use the typed assertion helpers on `bigfoot.file_io_mock`:

### `assert_open(**expected)`

All three fields (`path`, `mode`, `encoding`) are required:

```python
bigfoot.file_io_mock.assert_open(path="/tmp/data.csv", mode="r", encoding="utf-8")
```

### `assert_read_text(path)`

```python
bigfoot.file_io_mock.assert_read_text(path="/etc/myapp/config.yaml")
```

### `assert_read_bytes(path)`

```python
bigfoot.file_io_mock.assert_read_bytes(path="/var/data/image.png")
```

### `assert_write_text(path, data)`

```python
bigfoot.file_io_mock.assert_write_text(path="/tmp/output.txt", data="result: success")
```

### `assert_write_bytes(path, data)`

```python
bigfoot.file_io_mock.assert_write_bytes(path="/tmp/output.bin", data=b"\x00\x01\x02")
```

### `assert_remove(path)`

Matches both `os.remove` and `os.unlink` interactions:

```python
bigfoot.file_io_mock.assert_remove(path="/tmp/old-file.txt")
```

### `assert_rename(src, dst)`

Matches both `os.rename` and `os.replace` interactions:

```python
bigfoot.file_io_mock.assert_rename(src="/tmp/draft.txt", dst="/tmp/final.txt")
```

### `assert_makedirs(path, exist_ok)`

```python
bigfoot.file_io_mock.assert_makedirs(path="/var/data/exports", exist_ok=True)
```

### `assert_mkdir(path)`

```python
bigfoot.file_io_mock.assert_mkdir(path="/tmp/workdir")
```

### `assert_copy(src, dst)`

Matches both `shutil.copy` and `shutil.copy2` interactions:

```python
bigfoot.file_io_mock.assert_copy(src="/etc/myapp/config.yaml", dst="/tmp/config-backup.yaml")
```

### `assert_copytree(src, dst)`

```python
bigfoot.file_io_mock.assert_copytree(src="/var/data/source", dst="/var/data/archive")
```

### `assert_rmtree(path)`

```python
bigfoot.file_io_mock.assert_rmtree(path="/tmp/build-artifacts")
```

## Simulating errors

Use the `raises` parameter to simulate file system errors:

```python
import bigfoot

def test_file_not_found():
    bigfoot.file_io_mock.mock_operation(
        "read_text", "/etc/myapp/config.yaml",
        raises=FileNotFoundError("[Errno 2] No such file or directory: '/etc/myapp/config.yaml'"),
    )

    with bigfoot:
        from pathlib import Path
        with pytest.raises(FileNotFoundError):
            Path("/etc/myapp/config.yaml").read_text()

    bigfoot.file_io_mock.assert_read_text(path="/etc/myapp/config.yaml")
```

## Full example

**Production code** (`examples/file_processor/app.py`):

```python
--8<-- "examples/file_processor/app.py"
```

**Test** (`examples/file_processor/test_app.py`):

```python
--8<-- "examples/file_processor/test_app.py"
```

## `open()` return values

When mocking `open()`, the return value is automatically wrapped in the appropriate IO class:

- `str` return value: wrapped in `io.StringIO`
- `bytes` return value: wrapped in `io.BytesIO`
- `None` return value (write mode): returns empty `io.StringIO` or `io.BytesIO` depending on mode

```python
def test_open_read():
    bigfoot.file_io_mock.mock_operation(
        "open", "/tmp/data.csv",
        returns="id,name\n1,Alice\n2,Bob",
    )

    with bigfoot:
        with open("/tmp/data.csv", "r") as f:
            lines = f.readlines()

    assert len(lines) == 3

    bigfoot.file_io_mock.assert_open(path="/tmp/data.csv", mode="r", encoding="utf-8")
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
bigfoot.file_io_mock.mock_operation("read_text", "/tmp/cache.json", returns="{}", required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code performs a file operation that has no remaining mocks in its queue, bigfoot raises `UnmockedInteractionError`:

```
Path.read_text('/etc/myapp/config.yaml', ...) was called but no mock was registered.
Register a mock with:
    bigfoot.file_io_mock.mock_operation('read_text', '/etc/myapp/config.yaml', returns=...)
```
