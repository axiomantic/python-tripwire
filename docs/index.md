# tripwire

**tripwire** intercepts every external call your code makes and forces your tests to account for all of them. It ships with 27 plugins for HTTP, subprocess, database, cache, cloud, messaging, crypto, file I/O, and more. It enforces three guarantees that most mocking libraries leave silent:

1. **Every call must be pre-authorized.** Code makes a call with no registered mock? `UnmockedInteractionError`, immediately.
2. **Every recorded interaction must be explicitly asserted.** Forget to assert an interaction? `UnassertedInteractionsError` at teardown.
3. **Every registered mock must actually be triggered.** Register a mock that never fires? `UnusedMocksError` at teardown.

A plugin system makes it straightforward to intercept any service and enforce all three guarantees.

## Quick example

```python
import tripwire
from dirty_equals import IsInstance

def create_charge(amount):
    """Production code -- calls Stripe via httpx internally."""
    import httpx
    response = httpx.post("https://api.stripe.com/v1/charges",
                          json={"amount": amount})
    return response.json()

def test_payment_flow():
    tripwire.http.mock_response("POST", "https://api.stripe.com/v1/charges",
                               json={"id": "ch_123"}, status=200)

    with tripwire:
        result = create_charge(5000)

    tripwire.http.assert_request(
        "POST", "https://api.stripe.com/v1/charges",
        headers=IsInstance(dict), body='{"amount": 5000}',
    )
    assert result["id"] == "ch_123"
```

The test calls `create_charge()`, which internally uses httpx. tripwire intercepts the HTTP call transparently. If you forget the `assert_request()` call, tripwire raises `UnassertedInteractionsError` at teardown. If the mock is never triggered, `UnusedMocksError`. If code makes an unmocked HTTP call, `UnmockedInteractionError` immediately.

## Navigation

<div class="grid cards" markdown>

- **[Installation](guides/installation.md)**

    Install tripwire and its optional extras.

- **[Quick Start](guides/quickstart.md)**

    A complete walkthrough from setup to teardown.

- **[MockPlugin Guide](guides/mock-plugin.md)**

    Full reference for configuring method mocks, FIFO queues, and in-any-order assertions.

- **[HttpPlugin Guide](guides/http-plugin.md)**

    Intercept httpx, requests, and urllib HTTP calls in tests.

- **[SubprocessPlugin Guide](guides/subprocess-plugin.md)**

    Intercept `subprocess.run` and `shutil.which` in tests.

</div>
