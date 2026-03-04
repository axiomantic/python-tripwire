# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.1.0
