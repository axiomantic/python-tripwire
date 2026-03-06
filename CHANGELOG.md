# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-03-06

### Added

- `AutoAssertError` — raised immediately when `mark_asserted()` is called while `record()` is in progress; prevents the auto-assert anti-pattern at runtime rather than silently passing tests
- `HttpPlugin.assert_request()` — chained builder returning `HttpAssertionBuilder`; call `.assert_response(status, headers, body)` to assert all 7 HTTP fields in one ergonomic expression
- Named per-step assertion helpers on all state-machine plugin proxies: `socket_mock.assert_connect/send/recv/close`, `db_mock.assert_connect/execute/commit/rollback/close`, `smtp_mock.assert_connect/ehlo/helo/starttls/login/sendmail/send_message/quit`, `popen_mock.assert_spawn/communicate/wait`, `async_websocket_mock.assert_connect/send/recv/close`, `sync_websocket_mock.assert_connect/send/recv/close`
- `redis_mock.assert_command(command, args, kwargs)` — typed helper for Redis command assertions
- `DatabasePlugin` now records a `connect` step when `sqlite3.connect()` is called; initial state changed from `"connected"` to `"disconnected"`

### Changed

- **BREAKING:** `HttpPlugin` interaction fields renamed and expanded: `headers` → `request_headers`, `body` → `request_body`; two new required fields added: `response_headers` and `response_body`. All 7 fields are now required in `assert_interaction()` calls.
- **BREAKING:** `SubprocessPlugin` now requires all fields in assertions: `run` interactions require `command`, `returncode`, `stdout`, `stderr`; `which` interactions require `name` and `returns`.
- **BREAKING:** `RedisPlugin` interactions are no longer auto-asserted; callers must explicitly call `assert_interaction()` or `assert_command()`. All three fields (`command`, `args`, `kwargs`) are now required.
- **BREAKING:** All `StateMachinePlugin` subclasses (Socket, Database, Smtp, Popen, AsyncWebSocket, SyncWebSocket) now use named per-step fields in `interaction.details` instead of generic `{method, args, kwargs}`. Interactions are no longer auto-asserted.
- **BREAKING:** `PopenPlugin` step renamed: `"init"` → `"spawn"`. Stream operations (`stdin.write`, `stdout.read`, `stderr.read`) removed; `_FakeStream.read()` returns `b""`, `write()` returns `0`.
- `BasePlugin.assertable_fields()` changed from `@abstractmethod` to a concrete default returning `frozenset(interaction.details.keys())`. Subclasses may still override for steps with no assertable fields.

### Fixed

- Auto-assert anti-pattern eliminated from `StateMachinePlugin` and `RedisPlugin`: interactions no longer marked asserted at record time; tests that omit `assert_interaction()` calls now correctly fail at `verify_all()`.
- `Timeline.mark_asserted()` raises `AutoAssertError` if called while `record()` is in progress, providing an immediate and actionable error message when plugins attempt to auto-assert.

## [0.4.1] - 2026-03-05

### Added

- `with bigfoot:` and `async with bigfoot:` — shorthand for `with bigfoot.sandbox():` / `async with bigfoot.sandbox():`. Both forms return the active `StrictVerifier` from `__enter__`, so `with bigfoot as v:` gives direct access to the verifier when needed (e.g. for registering custom plugins manually).

## [0.4.0] - 2026-03-05

### Added

- `StateMachinePlugin` — abstract base class for stateful protocol plugins; provides FIFO session queue, state-transition validation via `_transitions()`, `new_session()` / `_bind_connection()` / `_execute_step()` / `_release_session()` lifecycle, and automatic interaction assertion at step execution time
- `InvalidStateError` — raised when a method is called from a state not listed as a valid from-state in the plugin's transition table; carries `source_id`, `method`, `current_state`, and `valid_states` attributes
- `SocketPlugin` — mocks `socket.socket` connect/send/sendall/recv/close with state machine `disconnected → connected → closed`; accessible via `bigfoot.socket_mock`
- `DatabasePlugin` — mocks `sqlite3.connect()` with full execute/commit/rollback/close lifecycle and state machine `connected → in_transaction → connected/closed`; returns fake cursor objects supporting `fetchone()`, `fetchall()`, `fetchmany()`, and iteration; accessible via `bigfoot.db_mock`
- `AsyncWebSocketPlugin` — mocks `websockets.connect` async context manager with state machine `connecting → open → closed`; requires `bigfoot[websockets]`; accessible via `bigfoot.async_websocket_mock`
- `SyncWebSocketPlugin` — mocks `websocket.create_connection` (websocket-client library) with state machine `connecting → open → closed`; requires `bigfoot[websocket-client]`; accessible via `bigfoot.sync_websocket_mock`
- `PopenPlugin` — mocks `subprocess.Popen` with stdin/stdout/stderr stream scripting and `communicate()`/`wait()` lifecycle; state machine `created → running → terminated`; coexists with `SubprocessPlugin`; accessible via `bigfoot.popen_mock`
- `SmtpPlugin` — mocks `smtplib.SMTP` with full state machine including optional `starttls`/`login` branches; supports both authenticated and unauthenticated send flows; accessible via `bigfoot.smtp_mock`
- `RedisPlugin` — mocks `redis.Redis.execute_command` with per-command FIFO queues; stateless (no state machine); requires `bigfoot[redis]`; accessible via `bigfoot.redis_mock`
- `bigfoot[websockets]` optional extra — adds `websockets>=13.0` dependency for `AsyncWebSocketPlugin`
- `bigfoot[websocket-client]` optional extra — adds `websocket-client>=1.7.0` dependency for `SyncWebSocketPlugin`
- `bigfoot[redis]` optional extra — adds `redis>=5.0.0` dependency for `RedisPlugin`

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

[0.4.1]: https://github.com/axiomantic/bigfoot/releases/tag/v0.4.1
[0.4.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.4.0
[0.3.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.3.0
[0.2.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.2.0
[0.1.1]: https://github.com/axiomantic/bigfoot/releases/tag/v0.1.1
[0.1.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.1.0
