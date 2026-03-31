# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.19.1] - 2026-03-27

### Fixed

- **Guard mode no longer blocks non-connect socket operations:** `socket.send()`, `socket.sendall()`, `socket.recv()`, and `socket.close()` now pass through to the originals when no sandbox is active. Previously, these operations hit the "fail closed" guard path because they had no `FirewallRequest` to evaluate, causing `GuardedCallError` on asyncio internal socket cleanup (self-pipe sockets) and any other non-bigfoot-managed socket. The firewall decision is made at `socket.connect()` time; subsequent operations on the same socket are implicitly allowed.
- **`_get_socket_plugin` now accepts `source_id` parameter:** Previously hardcoded `_SOURCE_CONNECT` regardless of which operation called it, producing misleading error messages in sandbox mode.

### Changed

- **`require_response` now defaults to `True`:** `HttpPlugin` now requires response assertions by default. Calling `assert_request()` returns an `HttpAssertionBuilder` that must be completed with `.assert_response(status, headers, body)`. To opt out, pass `require_response=False` per-call or set `require_response = false` in `[tool.bigfoot.http]` config. This aligns with bigfoot's "certainty is the contract" philosophy: if you mock a response, you should assert you got it.

### Improved

- **Field-level diffs in `InteractionMismatchError`:** Mismatch errors now show per-field `expected:` / `actual:` comparisons for only the fields that differ, with `difflib.unified_diff` for long strings. Matching fields are collapsed to a single "N fields matched" line.
- **Compact remaining timeline:** The "Remaining timeline" section in mismatch errors is hidden when redundant (single interaction already shown above).
- **Assertion-first `UnassertedInteractionsError`:** Copy-pasteable assertion code is shown first per interaction; the teaching preamble appears once at the end, not repeated per interaction.
- **Slimmer `InteractionMismatchError` header:** Removed redundant `Expected={...}, actual={...}` repr dump from the one-liner; the formatted hint already contains all the detail.
- **Cleaner `VerificationError` nesting:** When both unasserted and unused errors fire, section headers (`--- Unasserted Interactions ---`, `--- Unused Mocks ---`) replace the old cramped nesting.
- **`MissingAssertionFieldsError` shows provided fields:** Error message now lists both the missing fields and the fields that were provided, so you see the gap at a glance.
- **Richer `MockPlugin.format_interaction`:** One-liners now include a truncated preview of the first positional arg (e.g., `[MockPlugin] mod:attr.__call__(["osascript", "-e", ...])`), making timeline listings distinguishable when multiple interactions share the same source.

## [0.19.0] - 2026-03-26

### Added

- **Firewall mode redesign:** Guard mode has been redesigned as a granular firewall system. The new `M()` pattern object enables matching against protocol-specific fields (host, path, method, command, service, etc.) using glob patterns, CIDR notation, regex, or callable predicates. Example: `@pytest.mark.allow(M(protocol="http", host="*.example.com"))`.
- **`restrict()` context manager:** Sets a ceiling on allowed calls that inner blocks cannot widen. Useful for enforcing that a code path only makes specific types of calls: `with bigfoot.restrict(M(protocol="http", host="api.stripe.com")): ...`.
- **`FirewallRequest` protocol-typed dataclasses:** Each protocol (HTTP, Redis, subprocess, boto3, etc.) defines a typed dataclass carrying the request details for firewall matching. Plugin authors construct these objects to provide precise matching fields.
- **TOML firewall configuration:** New `[tool.bigfoot.firewall]` section replaces the deprecated `guard_allow` key. Supports structured per-protocol rules (`[tool.bigfoot.firewall.allow.http]` with `hosts`, `paths`, `methods` arrays), deny rules, and per-file overrides.
- **Three-level configuration:** Firewall rules combine from TOML (project-wide), marks (per-test), and context managers (scoped blocks), with `restrict()` enforcing ceilings.

### Changed

- **`guard_allow` deprecated:** The `guard_allow` key in `[tool.bigfoot]` is still accepted but deprecated in favor of `[tool.bigfoot.firewall.allow]` structured config.
- **Guard mode docs rewritten** as the Firewall Mode guide, covering `M()` patterns, TOML config, `restrict()`, `FirewallRequest`, and per-protocol examples.
- **README** updated to describe firewall mode with `M()` patterns and `restrict()`.

## [0.18.0] - 2026-03-25

### Added

- **Cross-thread ContextVar propagation:** Bigfoot now propagates all ContextVars (sandbox state, guard mode, recording state) to child threads automatically. Tests using `threading.Thread`, `ThreadPoolExecutor`, Starlette `TestClient`, or anyio portal threads now work correctly inside bigfoot sandboxes. Activated at `pytest_configure` time via the new `_context_propagation` module.
- **Python 3.14 free-threaded support:** When `sys.flags.thread_inherit_context` is `True` (Python 3.14+ free-threaded builds), bigfoot skips its own thread patching and lets the runtime handle context propagation natively.
- **Threading and ContextVars guide:** New documentation page explaining how bigfoot handles context propagation across threads.

### Changed

- **HttpPlugin no longer patches `run_in_executor`:** The per-plugin `_patch_run_in_executor` method has been removed in favor of the centralized context propagation module. `run_in_executor` context propagation is now handled by the `ThreadPoolExecutor.submit` patch.

### Fixed

- **Python 3.13 compatibility:** Fixed `AttributeError: module 'threading' has no attribute '_start_new_thread'` caused by Python 3.13 renaming the internal thread creation function to `_start_joinable_thread`.

## [0.17.0] - 2026-03-24

### Added

- **Guard mode warn level (new default):** Guard mode now defaults to `warn` instead of `error`. Tests that trigger guarded calls emit `GuardedCallWarning` and continue rather than failing outright. Use `bigfoot_guard = "error"` (or `"strict"`) in `pyproject.toml` to restore the blocking behavior. `"strict"` is accepted as an alias for `"error"`.
- **`GuardedCallWarning`:** New `UserWarning` subclass exported from `bigfoot`. Allows filtering with `warnings.filterwarnings("error", category=bigfoot.GuardedCallWarning)` to make specific test suites fail on guard violations.
- **`bigfoot_guard` config key:** Guard level is now configurable via `pyproject.toml` `[tool.pytest.ini_options]` or `pytest.ini`. Accepted values: `"warn"` (default), `"error"`, `"strict"` (alias for error), `false` (disable entirely). Boolean `true` is rejected with a descriptive error.

### Fixed

- **`@pytest.mark.allow` hook clobber:** `pytest_runtest_call` previously overwrote the allowlist on each test, causing `bigfoot.allow()` fixture calls made before the test to be lost. The hook now merges the marker-derived allowlist with the existing context instead of replacing it.

### Changed

- **Guard mode docs rewritten** for first-time users encountering `GuardedCallError` or `GuardedCallWarning`. New focus on the `@pytest.mark.allow` fix-it workflow and common pitfalls.
- **`GuardedCallError` message** now leads with the `@pytest.mark.allow` fix and lists all valid plugin names rather than addressing plugin authors.

## [0.16.0] - 2026-03-24

### Added

- **Python 3.10 support:** bigfoot now supports Python 3.10 through 3.13. Uses `tomli` and `exceptiongroup` backport packages as conditional dependencies on Python <3.11. CI matrix expanded to include Python 3.10 across all three platforms.

### Fixed

- Redis example tests used incorrect `kwargs` assertion that broke across redis library versions; now uses `IsInstance(dict)` for portability.
- HTTP plugin tests with `requests` now allow DNS pass-through (`bigfoot.allow("dns")`) to handle Python 3.10's `urllib3` triggering `socket.gethostbyname` before the HTTP interceptor.

## [0.15.0] - 2026-03-22

### Added

- **Public plugin authoring API:** `BasePlugin`, `Interaction`, `Timeline`, `GuardPassThrough`, `get_verifier_or_raise`, `GUARD_ELIGIBLE_PREFIXES`, and `PluginEntry` are now exported from `bigfoot` directly. Plugin authors no longer need private module imports.
- **`AllWildcardAssertionError`:** raised when all fields in an `assert_*` call are `AnyThing()` wildcards. The error message includes copy-pasteable assertions with the actual recorded values.
- **Pyright/mypy stub file:** `__init__.pyi` declares the module-level context manager protocol (`with bigfoot:`), `current_verifier()`, plugin proxy attributes, and all public exports. Fixes Pyright `reportGeneralTypeIssues` and `reportAttributeAccessIssue` errors.
- **Docs cross-linking:** `writing-plugins.md`, `pytest-integration.md`, and `configuration.md` now cross-reference each other. `configuration.md` documents `disabled_plugins`.
- **Copy-pasteable assertions in error messages:** `UnassertedInteractionsError` now includes "Copy this assertion into your test:" framing with actual recorded values.

### Changed

- **Breaking:** `_GuardPassThrough` renamed to `GuardPassThrough`.
- **Breaking:** `_get_verifier_or_raise` renamed to `get_verifier_or_raise`.
- **Breaking:** `BasePlugin` methods `_check_conflicts`, `_install_patches`, `_restore_patches` renamed to `check_conflicts`, `install_patches`, `restore_patches`.
- `StrictVerifier` class docstring now warns against direct instantiation. A `warnings.warn()` fires when `StrictVerifier()` is created outside pytest context.
- `BasePlugin` class docstring expanded with a 5-step plugin authoring guide.
- `UnmockedInteractionError` message now includes teaching content showing how to add a mock and assert it.
- `UnassertedInteractionsError` message now includes a count and teaching preamble with the assertion pattern.
- Module-level docstring in `bigfoot/__init__.py` expanded with quick start, anti-patterns, and plugin authoring guidance.

### Fixed

- **Release workflow:** `gh release create` now uses `--clobber` for idempotent releases (fixes failure on existing tags).
- **Type safety:** eliminated ~140 `# type: ignore` comments across 24 plugin files with proper `Callable` typing, `cast()`, and `setattr()`. Only 2 unavoidable `[import-untyped]` remain (psycopg2, asyncpg). `mypy --strict` passes with 0 errors.

## [0.14.0] - 2026-03-20

### Added

- **Import-site mocking:** `bigfoot.mock("mod:attr")` patches module-level attributes using colon-separated `"module.path:attribute"` syntax. Replaces the old `bigfoot.mock("Name")` string-name API.
- **Object mocking:** `bigfoot.mock.object(target, "attr")` patches an attribute on a specific object instance.
- **Spy factories:** `bigfoot.spy("mod:attr")` and `bigfoot.spy.object(target, "attr")` create spies that delegate to the real implementation when the queue is empty, recording `returned` or `raised` in interaction details.
- **Error mocking for HTTP:** `bigfoot.http.mock_error(method, url, raises=...)` registers a mock that raises an exception instead of returning a response. Error mocks participate in the same FIFO queue as `mock_response()`.
- **Error assertion for HTTP:** `bigfoot.http.assert_request(..., raised=...)` asserts error interactions with request fields plus the raised exception.
- **`raised` field in interaction details** across all plugins. When a mock raises an exception (via `.raises()` or spy delegation), the exception is captured in `interaction.details["raised"]` and must be asserted.
- **`returned` field for spy interactions.** When a spy delegates to the real implementation and the method returns successfully, the return value is captured in `interaction.details["returned"]` and must be asserted.
- **`enforce` flag on `Interaction`:** controls whether `verify_all()` checks the interaction. Mocks activated via individual context managers (`with mock:`) set `enforce=False`; sandbox activation sets `enforce=True`.
- **`PatchSet` and `PatchTarget` shared patching primitives:** ref-counted patching infrastructure used by `BasePlugin.activate()`/`deactivate()` and the new mock system.
- **`resolve_target()`** for colon-separated import-site path resolution.

### Changed

- **Breaking:** `bigfoot.mock("Name")` string-name API replaced by `bigfoot.mock("mod:attr")` colon-separated import-site format. The old `MockProxy`-based API is retained internally for backward compatibility but is no longer the public API.
- **Breaking:** `MockPlugin.assertable_fields()` now adapts based on interaction content: returns `{args, kwargs}` for standard calls, adds `raised` when present, adds `returned` when present.
- `_MockFactory` and `_SpyFactory` replace the prior `mock()` and `spy()` functions, providing `.object()` methods.
- `SandboxContext` now activates/deactivates all registered `_BaseMock` instances alongside plugin lifecycle.
- All `BasePlugin` subclasses and `StateMachinePlugin` subclasses migrated to shared patching hooks (`PatchSet`-based `activate()`/`deactivate()`).

### Improved

- README updated with new mock/spy API examples, error mocking, and spy observability sections.
- All ruff lint errors resolved.
- All mypy errors in `_BaseMock` context manager token cleanup resolved.

## [0.13.2] - 2026-03-17

### Changed

- Updated tagline to "Full-certainty test mocking for Python".

## [0.13.1] - 2026-03-17

### Changed

- Updated PyPI long description for better discoverability.

## [0.13.0] - 2026-03-16

### Added

- **Guard mode:** bigfoot installs interceptors at test session startup, blocking real I/O calls that happen outside a sandbox. Accidental network calls, database connections, and subprocess invocations raise `GuardedCallError` immediately. Use `bigfoot.allow("dns", "socket")` or `@pytest.mark.allow(...)` to selectively permit real calls. Use `bigfoot.deny(...)` or `@pytest.mark.deny(...)` to narrow the allowlist. Enabled by default; opt out via `[tool.bigfoot] guard = false` in `pyproject.toml`.
- **13 new plugins** across 11 categories: MongoPlugin, ElasticsearchPlugin, MemcachePlugin, DnsPlugin, SshPlugin, GrpcPlugin, Boto3Plugin, CeleryPlugin, PikaPlugin, JwtPlugin, CryptoPlugin, FileIoPlugin, NativePlugin.

## [0.12.2] - 2026-03-15

### Improved

- README and docs index: rewritten intro with plugin list, three guarantees, and plugin system callout. Quick start examples now show a production function under test rather than calling httpx directly.

## [0.12.1] - 2026-03-15

### Fixed

- `HttpPlugin.format_assert_hint()` now shows `http.assert_request(...)` convenience wrapper instead of raw `verifier.assert_interaction()`. Shows chained `.assert_response(...)` only when `require_response=True`.
- `MockPlugin`: added `assert_call()` convenience method on `MethodProxy`. `format_assert_hint()` now shows `verifier.mock("name").method.assert_call(...)`.
- `SubprocessPlugin`: added `assert_run()` and `assert_which()` convenience methods. `format_assert_hint()` now shows these instead of `assert_interaction()`.

### Improved

- Documentation landing page: replaced Bouncer/Auditor/Accountant metaphors with direct technical language.
- All plugin guides now recommend convenience assertion wrappers as the primary API, with `assert_interaction()` shown as the low-level equivalent.
- Writing-plugins guide: expanded `format_assert_hint` docs, added "Convenience assertion methods" section with pattern and guidelines, fixed example plugin (missing sentinel init), corrected abstract method count (9, not 10), removed incorrect auto-assert claim about StateMachinePlugin.
- HTTP plugin guide: fixed `body=None` to `body=""` in all `assert_request()` examples (matching actual signature).
- Mock plugin guide: fixed error hint examples to match actual output (`verifier.mock(...)` not `bigfoot.mock(...)`), corrected `args`/`kwargs` field descriptions.

## [0.12.0] - 2026-03-15

### Added

- `MethodProxy.assert_call()` convenience wrapper for MockPlugin assertions.
- `SubprocessPlugin.assert_run()` and `SubprocessPlugin.assert_which()` convenience wrappers.

### Changed

- `HttpPlugin.format_assert_hint()` shows convenience wrappers in error messages.
- `MockPlugin.format_assert_hint()` shows convenience wrappers in error messages.
- `SubprocessPlugin.format_assert_hint()` shows convenience wrappers in error messages.

## [0.11.1] - 2026-03-08

### Changed

- CI test matrix now uses `fail-fast: true` to cancel remaining jobs on first failure, saving CI costs.

## [0.11.0] - 2026-03-07

### Changed

- **Breaking:** Error messages now render the hint text directly instead of wrapping it in `repr()`. `UnassertedInteractionsError`, `UnusedMocksError`, `InteractionMismatchError`, and `UnmockedInteractionError` now produce readable, multi-line output in pytest tracebacks with copy-pasteable remediation code.

### Improved

- README rewritten: real pytest error output, corrected API examples (`IsInstance(dict)` not `IsMapping()`), plugin table with direct guide links, tighter first-viewport messaging.
- PyPI classifiers and keywords expanded for better discoverability.
- Added `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `.github/FUNDING.yml`.

## [0.10.1] - 2026-03-06

### Fixed

- `SubprocessPlugin`: `_which_interceptor` now passes `**kwargs` (mode, path) through to `_handle_which` instead of silently dropping them.
- `SubprocessPlugin`: `_handle_run` now raises `TypeError` when called with a string command instead of silently splitting it into characters (e.g., `"ls"` becoming `['l', 's']`).
- Replaced loop-and-break plugin lookup patterns with idiomatic `next()` expressions in subprocess, popen, and proxy code.
- Extracted `_push_cm()` helper in `_BigfootModule` to eliminate duplicated sandbox creation logic in `__enter__` and `__aenter__`.

## [0.10.0] - 2026-03-06

### Added

- `Psycopg2Plugin` -- intercepts `psycopg2.connect()` during a sandbox and returns a fake connection with cursor proxy supporting `execute()`, `fetchone()`, `fetchall()`, `fetchmany()`, `commit()`, `rollback()`, and `close()`. Uses the same state machine as `DatabasePlugin` (disconnected -> connected -> in_transaction -> closed). Supports both DSN and keyword-based connection parameters.
- `AsyncpgPlugin` -- intercepts `asyncpg.connect()` during a sandbox and returns a fake async connection supporting `execute()`, `fetch()`, `fetchrow()`, `fetchval()`, and `close()`. All methods are async, matching asyncpg's native interface. Connection stays in `connected` state for all query methods.
- `bigfoot.psycopg2_mock` proxy -- auto-creates `Psycopg2Plugin` on the current test verifier on first access. Raises `ImportError` if `bigfoot[psycopg2]` is not installed.
- `bigfoot.asyncpg_mock` proxy -- auto-creates `AsyncpgPlugin` on the current test verifier on first access. Raises `ImportError` if `bigfoot[asyncpg]` is not installed.
- `bigfoot[psycopg2]` optional extra -- adds `psycopg2-binary>=2.9.0` dependency.
- `bigfoot[asyncpg]` optional extra -- adds `asyncpg>=0.29.0` dependency.

## [0.9.0] - 2026-03-06

### Added

- `aiohttp.ClientSession` interception in `HttpPlugin` -- when `bigfoot[aiohttp]` is installed, `HttpPlugin` intercepts all requests made through `aiohttp.ClientSession` (GET, POST, etc.) alongside the existing httpx, requests, and urllib transports. Mock responses return a lightweight fake response supporting `.status`, `.json()`, `.text()`, `.read()`, `.headers`, and async context manager usage.
- `bigfoot[aiohttp]` optional extra -- adds `aiohttp>=3.9.0` dependency.
- Conflict detection for `aiohttp.ClientSession._request` -- raises `ConflictError` if another library has patched it before bigfoot activates.
- Pass-through support for aiohttp requests -- `http.pass_through(method, url)` works with aiohttp the same as with other transports.

## [0.8.0] - 2026-03-06

### Added

- `AsyncSubprocessPlugin` -- intercepts `asyncio.create_subprocess_exec` and `asyncio.create_subprocess_shell` during a sandbox. The async complement to `PopenPlugin`. Sessions follow the same state machine (spawn -> communicate/wait -> terminated) and provide typed assertion helpers (`assert_spawn`, `assert_communicate`, `assert_wait`).
- `bigfoot.async_subprocess_mock` proxy -- auto-creates `AsyncSubprocessPlugin` on the current test verifier on first access.
- `create_subprocess_exec` records `command` as a `list[str]`; `create_subprocess_shell` records `command` as a `str`.

## [0.7.0] - 2026-03-06

### Added

- `LoggingPlugin` -- intercepts Python's `logging` module globally during a sandbox. All log calls are swallowed (not emitted to handlers) and recorded on the timeline, requiring explicit assertion at teardown. Fire-and-forget behavior: unmocked log calls are silently recorded rather than raising `UnmockedInteractionError`.
- `bigfoot.log_mock` proxy -- auto-creates `LoggingPlugin` on the current test verifier on first access, matching the pattern of other plugin proxies.
- `log_mock.mock_log(level, message, logger_name=None)` -- register a FIFO mock for expected log calls. `logger_name=None` matches any logger.
- `log_mock.assert_log(level, message, logger_name)` -- assert the next log interaction with all 3 fields.
- Per-level assertion helpers: `assert_debug()`, `assert_info()`, `assert_warning()`, `assert_error()`, `assert_critical()`.

## [0.6.0] - 2026-03-06

### Added

- Plugin config system: bigfoot now reads `[tool.bigfoot]` from the nearest `pyproject.toml` (walking up from `Path.cwd()` at `StrictVerifier` construction time). Config is loaded once per verifier and distributed to plugins via a `load_config()` hook.
- `BasePlugin.config_key()` classmethod — returns the TOML sub-table key for a plugin (e.g. `"http"`), or `None` to opt out of configuration.
- `BasePlugin.load_config(config: dict[str, Any])` — no-op by default; concrete plugins override to read and validate their options. Called as the last line of each concrete plugin's `__init__`, after all instance attributes are initialized.
- `[tool.bigfoot.http] require_response` — boolean option (default `false`). When `true`, `http.assert_request()` returns an `HttpAssertionBuilder` requiring `.assert_response()` to complete the assertion with all seven HTTP fields. Per-call `require_response` argument still overrides the project-level setting.
- `bigfoot._config.load_bigfoot_config()` — internal function that walks the filesystem for `pyproject.toml` and returns the `[tool.bigfoot]` table; propagates `tomllib.TOMLDecodeError` on malformed TOML.

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

[0.19.1]: https://github.com/axiomantic/bigfoot/releases/tag/v0.19.1
[0.19.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.19.0
[0.18.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.18.0
[0.17.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.17.0
[0.16.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.16.0
[0.15.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.15.0
[0.14.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.14.0
[0.13.2]: https://github.com/axiomantic/bigfoot/releases/tag/v0.13.2
[0.13.1]: https://github.com/axiomantic/bigfoot/releases/tag/v0.13.1
[0.13.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.13.0
[0.12.2]: https://github.com/axiomantic/bigfoot/releases/tag/v0.12.2
[0.12.1]: https://github.com/axiomantic/bigfoot/releases/tag/v0.12.1
[0.12.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.12.0
[0.11.1]: https://github.com/axiomantic/bigfoot/releases/tag/v0.11.1
[0.11.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.11.0
[0.10.1]: https://github.com/axiomantic/bigfoot/releases/tag/v0.10.1
[0.10.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.10.0
[0.9.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.9.0
[0.8.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.8.0
[0.7.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.7.0
[0.6.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.6.0
[0.4.1]: https://github.com/axiomantic/bigfoot/releases/tag/v0.4.1
[0.4.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.4.0
[0.3.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.3.0
[0.2.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.2.0
[0.1.1]: https://github.com/axiomantic/bigfoot/releases/tag/v0.1.1
[0.1.0]: https://github.com/axiomantic/bigfoot/releases/tag/v0.1.0
