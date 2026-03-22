# pytest Integration

> **Writing a custom plugin?** See [Writing Plugins](writing-plugins.md) for the plugin authoring guide.
> **Configuring plugins?** See [Configuration](configuration.md) for `[tool.bigfoot]` settings.

bigfoot integrates with pytest automatically via the `pytest11` entry point. No `conftest.py` changes are required. Install bigfoot and every test gets a fresh verifier with automatic teardown verification.

## Module-level API (preferred)

The simplest way to use bigfoot is to `import bigfoot` and call module-level functions directly:

```python
import bigfoot

def test_example():
    email = bigfoot.mock("EmailService")
    email.send.returns(True)

    with bigfoot:
        email.send(to="user@example.com")

    bigfoot.assert_interaction(email.send)
    # verify_all() is called automatically at teardown
```

`with bigfoot:` is shorthand for `with bigfoot.sandbox():`. Both return the active `StrictVerifier` from `__enter__`, so `with bigfoot as v:` gives you the verifier directly if you need it. `bigfoot.sandbox()` remains available as the explicit form for cases where you need to pass the context manager around.

Behind the scenes, an autouse fixture creates one `StrictVerifier` per test, stores it in a `ContextVar`, and calls `verify_all()` after the test completes.

## Async tests

`bigfoot` and `bigfoot.in_any_order()` both support `async with`. Use `pytest-asyncio` for async test functions:

```python
import bigfoot
import httpx

async def test_async_http():
    bigfoot.http.mock_response("GET", "https://api.example.com/items", json={"items": []})

    async with bigfoot:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.example.com/items")
        assert response.json() == {"items": []}

    bigfoot.assert_interaction(bigfoot.http.request, method="GET")
    # verify_all() called at teardown
```

## Using bigfoot.http

`bigfoot.http` is a proxy to the `HttpPlugin` for the current test. It auto-creates the plugin on first access, so no explicit instantiation is needed:

```python
import bigfoot
import requests

def test_api_call():
    bigfoot.http.mock_response("POST", "https://api.example.com/users",
                               json={"id": 42}, status=201)

    with bigfoot:
        response = requests.post("https://api.example.com/users", json={"name": "Alice"})
        assert response.status_code == 201
        assert response.json()["id"] == 42

    bigfoot.assert_interaction(
        bigfoot.http.request,
        method="POST",
        url="https://api.example.com/users",
        status=201,
    )
```

## Teardown behavior

`verify_all()` is called after the test function returns (or raises). If the test fails with an assertion error mid-way, `verify_all()` still runs. If both the test assertion and `verify_all()` fail, pytest reports both errors.

## bigfoot_verifier fixture (escape hatch)

When you need direct access to the `StrictVerifier` object, inject the `bigfoot_verifier` fixture. It returns the same verifier that the module-level API uses for that test:

```python
from bigfoot import StrictVerifier

def test_with_fixture(bigfoot_verifier: StrictVerifier):
    email = bigfoot_verifier.mock("EmailService")
    email.send.returns(True)

    with bigfoot_verifier.sandbox():
        email.send(to="user@example.com")

    bigfoot_verifier.assert_interaction(email.send)
    # verify_all() called automatically at teardown
```

You can also mix styles within the same test:

```python
import bigfoot
from bigfoot import StrictVerifier

def test_mixed(bigfoot_verifier: StrictVerifier):
    email = bigfoot.mock("EmailService")  # same verifier
    email.send.returns(True)

    with bigfoot:
        email.send(to="user@example.com")

    assert bigfoot.current_verifier() is bigfoot_verifier  # True
    bigfoot.assert_interaction(email.send)
```

## Manual StrictVerifier

If you need a verifier outside of pytest (e.g., in a script or custom test runner), create one manually and call `verify_all()` yourself:

```python
from bigfoot import StrictVerifier

def test_manual():
    verifier = StrictVerifier()
    try:
        email = verifier.mock("EmailService")
        email.send.returns(True)

        with verifier.sandbox():
            email.send(to="user@example.com")

        verifier.assert_interaction(email.send)
    finally:
        verifier.verify_all()
```

The `try/finally` ensures `verify_all()` runs even if assertions fail.
