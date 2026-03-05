# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-03-05

### Added

- `SubprocessPlugin` — intercepts `subprocess.run` and `shutil.which` globally during a sandbox. `subprocess.run` uses a strict FIFO queue; calling with an unregistered or out-of-order command raises `UnmockedInteractionError` immediately. `shutil.which` is semi-permissive: unregistered names return `None` silently; registered names record an interaction and return their configured value.
- `bigfoot.subprocess_mock` proxy — auto-creates `SubprocessPlugin` on the current test verifier on first access, matching the `bigfoot.http` pattern.
- `subprocess_mock.mock_run(command, *, returncode, stdout, stderr, raises, required)` — register a FIFO mock for `subprocess.run`. `raises` causes the exception to be raised after the interaction is recorded.
- `subprocess_mock.mock_which(name, returns, *, required=False)` — register a mock for `shutil.which` keyed by binary name. `required=False` by default because tests often register more alternatives than any single code path will exercise.
- `subprocess_mock.install()` — activates the bouncer without registering any mocks; use to assert that no subprocess calls are made.

## [0.2.0] - 2026-03-05

### Added

- `bigfoot.spy(name, real)` — creates a `MockProxy` that delegates to `real` when its call queue is empty; queue entries take priority; the real method call is always recorded on the timeline (even if it raises)
- `StrictVerifier.mock(name, wraps=real)` / `bigfoot.mock(name, wraps=real)` — keyword form of spy creation
- `bigfoot.http.pass_through(method, url)` — registers a permanent routing rule that forwards matching requests to the original transport; recorded on the timeline; no unused-rule enforcement
- `BasePlugin.assertable_fields(interaction)` — new abstract method; returns the set of `interaction.details` keys that callers must include in `assert_interaction(**expected)`
- `MissingAssertionFieldsError` — raised by `assert_interaction()` when one or more assertable fields are omitted from `**expected`; has `missing_fields: frozenset[str]` attribute

### Changed

- **Breaking:** `assert_interaction()` now enforces complete field coverage. MockPlugin requires `args` and `kwargs`; HttpPlugin requires `method`, `url`, `headers`, `body`, and `status`. Use dirty-equals values (e.g., `Anything()`) to satisfy a field without exact matching.
- `MockPlugin` now stores `args` and `kwargs` in `interaction.details` as actual Python objects instead of `repr()` strings, enabling full dirty-equals compatibility on mock assertions
- `format_assert_hint()` on both `MockPlugin` and `HttpPlugin` now generates hints that include all assertable fields

## [0.1.1] - 2026-03-04

### Added

- Module-level implicit API: `bigfoot.mock()`, `bigfoot.sandbox()`, `bigfoot.assert_interaction()`, `bigfoot.in_any_order()`, `bigfoot.verify_all()`, `bigfoot.current_verifier()` — no fixture injection required
- `bigfoot.http` proxy — auto-creates `HttpPlugin` on the current test verifier on first access
- `AssertionInsideSandboxError` — raised when `assert_interaction()`, `in_any_order()`, or `verify_all()` is called while a sandbox is active; enforces post-sandbox assertion discipline
- `NoActiveVerifierError` — raised when module-level API is called outside a pytest test context

### Changed

- `bigfoot_verifier` fixture retained as an explicit escape hatch; the autouse `_bigfoot_auto_verifier` fixture now drives per-test verifier lifecycle invisibly

## [0.1.0] - 2026-03-04

### Added

- `StrictVerifier` — central coordinator that owns the interaction timeline and plugin registry
- `StrictVerifier.sandbox()` — sync and async context manager that activates all registered plugins and isolates state per async task via `contextvars.ContextVar`
- `StrictVerifier.in_any_order()` — sync and async context manager that relaxes FIFO ordering for assertions within a block
- `StrictVerifier.mock(name)` — creates a named `MockProxy` via `MockPlugin`
- `StrictVerifier.assert_interaction(source, **expected)` — asserts the next unasserted interaction matches source and expected fields
- `StrictVerifier.verify_all()` — enforces the Auditor and Accountant guarantees at teardown
- `MockPlugin` — strict call-by-call mock with FIFO deque; supports `returns()`, `raises()`, `calls()`, `required()`
- `HttpPlugin` *(optional, requires `[http]` extra)* — intercepts httpx (sync + async), requests, urllib, and `asyncio.BaseEventLoop.run_in_executor`; reference-counted for nested sandboxes
- `panoptest_verifier` pytest fixture — zero-boilerplate `StrictVerifier` with automatic `verify_all()` at teardown, registered via `pytest11` entry point
- `BigfootError` base exception and seven typed subtypes: `UnmockedInteractionError`, `UnassertedInteractionsError`, `UnusedMocksError`, `VerificationError`, `InteractionMismatchError`, `SandboxNotActiveError`, `ConflictError`
- Multi-OS CI matrix (Ubuntu, macOS, Windows) across Python 3.11, 3.12, and 3.13
- OIDC trusted publishing to PyPI on `v*` tags

[0.3.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.3.0
[0.2.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.2.0
[0.1.1]: https://github.com/axiomantic/bigfoot/releases/tag/v0.1.1
[0.1.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.1.0
