# NativePlugin Guide

`NativePlugin` intercepts `ctypes.CDLL` and `cffi.FFI.dlopen` at the class level, replacing loaded native libraries with proxy objects that route all function calls through tripwire's FIFO queue. Each library:function pair has its own independent queue. Arguments are automatically serialized from ctypes types to Python equivalents for assertion.

**Important:** `NativePlugin` is always available (no extra install required) but is NOT default enabled. You must explicitly enable it via `enabled_plugins = ["native"]` in your tripwire config, or access it through the `tripwire.native_mock` proxy. cffi interception is available when `cffi` is installed.

## Setup

In pytest, access `NativePlugin` through the `tripwire.native_mock` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_call_native_sqrt():
    tripwire.native_mock.mock_call("libm", "sqrt", returns=3.0)

    with tripwire:
        import ctypes
        libm = ctypes.CDLL("libm")
        result = libm.sqrt(ctypes.c_double(9.0))

    assert result == 3.0

    tripwire.native_mock.assert_call(
        library="libm", function="sqrt", args=(9.0,),
    )
```

For manual use outside pytest, construct `NativePlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.native_plugin import NativePlugin

verifier = StrictVerifier()
native_mock = NativePlugin(verifier)
```

Each verifier may have at most one `NativePlugin`. A second `NativePlugin(verifier)` raises `ValueError`.

## Registering mocks

Use `tripwire.native_mock.mock_call(library, function, *, returns, ...)` to register a mock before entering the sandbox:

```python
tripwire.native_mock.mock_call("libcrypto", "RAND_bytes", returns=0)
tripwire.native_mock.mock_call("libm", "pow", returns=8.0)
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `library` | `str` | required | Library name (e.g., `"libm"`, `"libcrypto"`) |
| `function` | `str` | required | Function name (e.g., `"sqrt"`, `"RAND_bytes"`) |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

## FIFO queues

Each library:function pair has its own independent FIFO queue. Multiple mocks for the same function are consumed in registration order:

```python
def test_multiple_native_calls():
    tripwire.native_mock.mock_call("libm", "sqrt", returns=2.0)
    tripwire.native_mock.mock_call("libm", "sqrt", returns=3.0)

    with tripwire:
        import ctypes
        libm = ctypes.CDLL("libm")
        r1 = libm.sqrt(ctypes.c_double(4.0))
        r2 = libm.sqrt(ctypes.c_double(9.0))

    assert r1 == 2.0
    assert r2 == 3.0

    tripwire.native_mock.assert_call(library="libm", function="sqrt", args=(4.0,))
    tripwire.native_mock.assert_call(library="libm", function="sqrt", args=(9.0,))
```

## Asserting interactions

Use the `assert_call` helper on `tripwire.native_mock`. All three fields (`library`, `function`, `args`) are required:

### `assert_call(library, function, *, args)`

```python
tripwire.native_mock.assert_call(
    library="libm", function="pow", args=(2.0, 3.0),
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `library` | `str` | required | Library name |
| `function` | `str` | required | Function name |
| `args` | `tuple` | `()` | Serialized arguments passed to the function |

### Argument serialization

ctypes arguments are automatically converted to Python equivalents for assertion:

| ctypes type | Serialized as |
|---|---|
| `ctypes.c_double(9.0)` | `9.0` |
| `ctypes.c_int(42)` | `42` |
| `ctypes.c_char_p(b"hello")` | `b"hello"` |
| `ctypes.Structure` | `dict` of field names to values |
| `ctypes._CFuncPtr` (callback) | `"<callback>"` |
| `ctypes._Pointer` | `contents` or `None` |
| Plain Python values | Passed through unchanged |

## Simulating errors

Use the `raises` parameter to simulate native function failures:

```python
import tripwire

def test_library_load_error():
    tripwire.native_mock.mock_call(
        "libcustom", "initialize",
        returns=None,
        raises=OSError("Symbol not found: initialize"),
    )

    with tripwire:
        import ctypes
        lib = ctypes.CDLL("libcustom")
        with pytest.raises(OSError, match="Symbol not found"):
            lib.initialize()

    tripwire.native_mock.assert_call(library="libcustom", function="initialize", args=())
```

## Full example

**Production code** (`examples/native_lib/app.py`):

```python
--8<-- "examples/native_lib/app.py"
```

**Test** (`examples/native_lib/test_app.py`):

```python
--8<-- "examples/native_lib/test_app.py"
```

## cffi support

When `cffi` is installed, `NativePlugin` also intercepts `cffi.FFI.dlopen`. The same `mock_call` and `assert_call` API applies:

```python
import tripwire

def test_cffi_library():
    tripwire.native_mock.mock_call("libz", "compressBound", returns=1024)

    with tripwire:
        import cffi
        ffi = cffi.FFI()
        ffi.cdef("long compressBound(long sourceLen);")
        libz = ffi.dlopen("libz")
        bound = libz.compressBound(512)

    assert bound == 1024

    tripwire.native_mock.assert_call(
        library="libz", function="compressBound", args=(512,),
    )
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.native_mock.mock_call("libm", "log", returns=0.0, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls a native function that has no remaining mocks in its queue, tripwire raises `UnmockedInteractionError`:

```
libm.sqrt(...) was called but no mock was registered.
Register a mock with:
    tripwire.native_mock.mock_call('libm', 'sqrt', returns=...)
```
