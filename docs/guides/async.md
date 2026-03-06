# Async Usage

bigfoot supports async tests natively. `bigfoot` and `bigfoot.in_any_order()` both implement `__aenter__` and `__aexit__`.

## async with bigfoot

Use `async with bigfoot:` in an async test function:

```python
import bigfoot
import httpx

async def test_async_http():
    bigfoot.http.mock_response("GET", "https://api.example.com/data", json={"ok": True})

    async with bigfoot:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.example.com/data")
        assert response.json() == {"ok": True}

    bigfoot.assert_interaction(bigfoot.http.request, method="GET", url="https://api.example.com/data")
```

`async with bigfoot:` is shorthand for `async with bigfoot.sandbox():`. Both return the active `StrictVerifier` from `__aenter__`. `bigfoot.sandbox()` is also available as the explicit form and returns a `SandboxContext` for cases where you need to pass the context manager around.

The sync and async forms are equivalent. `SandboxContext._enter()` and `_exit()` are synchronous under the hood; the async wrapper simply delegates to them.

## ContextVar isolation

The active verifier is stored in a `contextvars.ContextVar`. Each `asyncio.create_task()` call inherits a copy of the current context, so concurrent tasks see the correct verifier without interference:

```python
import bigfoot
import asyncio, httpx

async def fetch(url: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
    return response.json()

async def test_concurrent_requests():
    bigfoot.http.mock_response("GET", "https://api.example.com/a", json={"name": "a"})
    bigfoot.http.mock_response("GET", "https://api.example.com/b", json={"name": "b"})

    async with bigfoot:
        a, b = await asyncio.gather(
            asyncio.create_task(fetch("https://api.example.com/a")),
            asyncio.create_task(fetch("https://api.example.com/b")),
        )

    with bigfoot.in_any_order():
        bigfoot.assert_interaction(bigfoot.http.request, method="GET", url="https://api.example.com/a")
        bigfoot.assert_interaction(bigfoot.http.request, method="GET", url="https://api.example.com/b")
```

Because concurrent tasks may complete in any order, use `bigfoot.in_any_order()` when asserting interactions from concurrent work.

## async with in_any_order

`in_any_order()` also supports `async with`:

```python
async with bigfoot.in_any_order():
    bigfoot.assert_interaction(bigfoot.http.request, method="GET", url="https://api.example.com/a")
    bigfoot.assert_interaction(bigfoot.http.request, method="GET", url="https://api.example.com/b")
```

## run_in_executor propagation

When `HttpPlugin` is active, bigfoot patches `asyncio.BaseEventLoop.run_in_executor` to copy the current `contextvars` context into the thread pool executor. This means HTTP calls made from a thread via `run_in_executor` are intercepted by the correct verifier:

```python
import bigfoot
import asyncio, urllib.request

async def fetch_in_thread(url: str) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: urllib.request.urlopen(url).read())

async def test_thread_pool_interception():
    bigfoot.http.mock_response("GET", "https://api.example.com/data", body=b"hello")

    async with bigfoot:
        data = await fetch_in_thread("https://api.example.com/data")
        assert data == b"hello"

    bigfoot.assert_interaction(bigfoot.http.request, method="GET")
```

Without this patch, the thread would not inherit the ContextVar and would see no active sandbox.

## MockPlugin with async tests

`MockPlugin` works identically in async tests. No special async API is needed because mock calls are synchronous intercepts:

```python
import bigfoot

async def test_async_mock():
    repo = bigfoot.mock("UserRepository")
    repo.find_by_id.returns({"id": 1, "name": "Alice"})

    async with bigfoot:
        user = repo.find_by_id(1)
        assert user["name"] == "Alice"

    bigfoot.assert_interaction(repo.find_by_id)
```
