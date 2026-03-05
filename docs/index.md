# bigfoot

**bigfoot** is a pluggable interaction auditor for Python tests. It enforces three guarantees that most mocking libraries leave silent:

- **Bouncer**: every external interaction must be pre-authorized. If code makes a call with no registered mock, bigfoot raises `UnmockedInteractionError` immediately at call time.
- **Auditor**: every recorded interaction must be explicitly asserted. At teardown, any interaction that was never passed to `assert_interaction()` causes `UnassertedInteractionsError`.
- **Accountant**: every registered mock must actually be triggered. At teardown, any mock that was registered with `required=True` (the default) but never called causes `UnusedMocksError`.

## Why this matters

Standard mocking libraries let tests pass silently when:

- A mock is registered for an endpoint that the code never calls (the logic path changed, but the test still passes)
- A side-effecting call is intercepted but the test never checks that it happened
- A mock is configured with wrong arguments and the code actually hit a different mock

bigfoot makes each of these conditions a hard test failure. Tests that pass with bigfoot are tests that actually exercised the behavior they claim to cover.

## Quick example

```python
from bigfoot import StrictVerifier

verifier = StrictVerifier()
email = verifier.mock("EmailService")
email.send.returns(True)

with verifier.sandbox():
    result = email.send(to="user@example.com", subject="Welcome")
    assert result is True

verifier.assert_interaction(email.send, kwargs="{'to': 'user@example.com', 'subject': 'Welcome'}")
verifier.verify_all()
```

If `email.send` is never called, `verify_all()` raises `UnusedMocksError`.
If `assert_interaction` is never called, `verify_all()` raises `UnassertedInteractionsError`.
If the code calls `email.send` with no mock configured, bigfoot raises `UnmockedInteractionError` immediately.

## Navigation

<div class="grid cards" markdown>

- **[Installation](guides/installation.md)**

    Install bigfoot and its optional extras.

- **[Quick Start](guides/quickstart.md)**

    A complete walkthrough from setup to teardown.

- **[MockPlugin Guide](guides/mock-plugin.md)**

    Full reference for configuring method mocks, FIFO queues, and in-any-order assertions.

- **[HttpPlugin Guide](guides/http-plugin.md)**

    Intercept httpx, requests, and urllib HTTP calls in tests.

- **[SubprocessPlugin Guide](guides/subprocess-plugin.md)**

    Intercept `subprocess.run` and `shutil.which` in tests.

</div>
