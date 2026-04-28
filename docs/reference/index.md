# API Reference

All public symbols are importable from `tripwire` directly. `HttpPlugin` requires the `python-tripwire[http]` extra and is imported from `tripwire.plugins.http`.

## Public symbols

| Symbol | Type | Description |
|---|---|---|
| `StrictVerifier` | class | Central orchestrator. Owns the timeline and plugin registry. Entry point for all tripwire operations. |
| `SandboxContext` | class | Context manager returned by `verifier.sandbox()`. Activates all plugins for the duration of the `with` block. Supports both sync and async. |
| `InAnyOrderContext` | class | Context manager returned by `verifier.in_any_order()`. Inside this block, `assert_interaction()` matches any unasserted interaction regardless of timeline order. |
| `MockPlugin` | class | Intercepts method calls on named proxy objects. Created automatically by `verifier.mock()`. |
| `HttpPlugin` | class | Intercepts `httpx`, `requests`, and `urllib` HTTP calls. Requires `python-tripwire[http]`. Import from `tripwire.plugins.http`. |
| `SubprocessPlugin` | class | Intercepts `subprocess.run` and `shutil.which`. Included in core tripwire. Import from `tripwire.plugins.subprocess`. |
| `subprocess` | proxy | Module-level proxy to `SubprocessPlugin` for the current test. Auto-creates the plugin on first access. |
| `TripwireError` | exception | Base class for all tripwire exceptions. |
| `UnmockedInteractionError` | exception | Raised at call time when an intercepted call has no matching mock. |
| `UnassertedInteractionsError` | exception | Raised at teardown when interactions were recorded but never asserted. |
| `UnusedMocksError` | exception | Raised at teardown when required mocks were registered but never triggered. |
| `VerificationError` | exception | Raised at teardown when both `UnassertedInteractionsError` and `UnusedMocksError` apply simultaneously. |
| `InteractionMismatchError` | exception | Raised by `assert_interaction()` when the expected source or fields do not match. |
| `SandboxNotActiveError` | exception | Raised when an intercepted call fires but no sandbox is active. |
| `ConflictError` | exception | Raised at sandbox entry when a target method has already been patched by another library. |

## Internal types (not exported)

These types appear in error messages, docstrings, and plugin implementations but are not part of the public API:

| Symbol | Module | Description |
|---|---|---|
| `MockProxy` | `tripwire._mock_plugin` | Object returned by `verifier.mock()`. Attribute access yields `MethodProxy`. |
| `MethodProxy` | `tripwire._mock_plugin` | Per-method interceptor with `.returns()`, `.raises()`, `.calls()`, `.required()`. |
| `MockConfig` | `tripwire._mock_plugin` | Internal record of one configured side effect. |
| `HttpRequestSentinel` | `tripwire.plugins.http` | Opaque object returned by `http.request`. Used as source in `assert_interaction()`. |
| `SubprocessRunSentinel` | `tripwire.plugins.subprocess` | Opaque handle returned by `subprocess.run`. Used as source in `assert_interaction()`. |
| `SubprocessWhichSentinel` | `tripwire.plugins.subprocess` | Opaque handle returned by `subprocess.which`. Used as source in `assert_interaction()`. |
| `RunMockConfig` | `tripwire.plugins.subprocess` | Internal record of a registered `mock_run` configuration. |
| `WhichMockConfig` | `tripwire.plugins.subprocess` | Internal record of a registered `mock_which` configuration. |
| `HttpMockConfig` | `tripwire.plugins.http` | Internal record of a registered mock response. |
| `BasePlugin` | `tripwire._base_plugin` | Abstract base class for all plugins. |
| `Interaction` | `tripwire._timeline` | Dataclass representing one recorded event in the timeline. |
| `Timeline` | `tripwire._timeline` | Thread-safe ordered list of `Interaction` objects. |
