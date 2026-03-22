"""MockPlugin, MockProxy, MethodProxy, MockConfig, _BaseMock, ImportSiteMock, ObjectMock."""

import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._base_plugin import BasePlugin
from bigfoot._errors import ConflictError, UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Side effect sentinel types
# ---------------------------------------------------------------------------


@dataclass
class _ReturnValue:
    value: Any


@dataclass
class _RaiseException:
    exc: BaseException | type[BaseException]


@dataclass
class _CallFn:
    fn: Callable[..., Any]


# Sentinel: initial value for `result` in wraps delegation try/finally.
# Never actually returned -- the exception path re-raises before reaching `return result`.
_SENTINEL = object()

# Sentinel: distinguishes "parameter not passed" from None in assert_call().
_ABSENT = object()


# ---------------------------------------------------------------------------
# MockConfig
# ---------------------------------------------------------------------------


@dataclass
class MockConfig:
    """Tracks one configured side effect for a mock method."""

    mock_name: str
    method_name: str
    side_effect: "_ReturnValue | _RaiseException | _CallFn"
    required: bool = True
    registration_traceback: str = field(
        default_factory=lambda: "".join(traceback.format_stack()[:-2])
    )


# ---------------------------------------------------------------------------
# MethodProxy
# ---------------------------------------------------------------------------


class MethodProxy:
    """Interceptor + source filter for a single mock method.

    Attribute access on _BaseMock or MockProxy returns a MethodProxy.
    Calling it routes through the bigfoot interceptor.
    """

    def __init__(
        self,
        mock_name: str,
        method_name: str,
        plugin: "MockPlugin",
        proxy: "MockProxy | _BaseMock",
    ) -> None:
        self._mock_name = mock_name
        self._method_name = method_name
        self._plugin = plugin
        self._proxy = proxy
        self._config_queue: deque[MockConfig] = deque()
        self.source_id = f"mock:{mock_name}.{method_name}"
        self._next_required: bool = True  # sticky flag for subsequent configurations

    def required(self, flag: bool = True) -> "MethodProxy":
        """Set sticky required flag for subsequent .returns()/.raises()/.calls()."""
        self._next_required = flag
        return self

    def returns(self, value: Any) -> "MethodProxy":  # noqa: ANN401
        """Append a return-value side effect to the FIFO queue."""
        self._config_queue.append(
            MockConfig(
                mock_name=self._mock_name,
                method_name=self._method_name,
                side_effect=_ReturnValue(value),
                required=self._next_required,
            )
        )
        return self

    def raises(self, exc: BaseException | type[BaseException]) -> "MethodProxy":
        """Append a raise side effect to the FIFO queue."""
        self._config_queue.append(
            MockConfig(
                mock_name=self._mock_name,
                method_name=self._method_name,
                side_effect=_RaiseException(exc),
                required=self._next_required,
            )
        )
        return self

    def calls(self, fn: Callable[..., Any]) -> "MethodProxy":
        """Append a callable side effect to the FIFO queue."""
        self._config_queue.append(
            MockConfig(
                mock_name=self._mock_name,
                method_name=self._method_name,
                side_effect=_CallFn(fn),
                required=self._next_required,
            )
        )
        return self

    def assert_call(
        self,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        raised: Any = _ABSENT,  # noqa: ANN401
        returned: Any = _ABSENT,  # noqa: ANN401
    ) -> None:
        """Assert the next call to this mock method with the given arguments.

        Convenience wrapper around verifier.assert_interaction().

        Args:
            args: Expected positional arguments.
            kwargs: Expected keyword arguments (defaults to {}).
            raised: Expected exception (required when .raises() was used or spy raised).
            returned: Expected return value (required for spy mode when real method returned).
        """
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        expected: dict[str, Any] = {
            "args": args,
            "kwargs": kwargs if kwargs is not None else {},
        }
        if raised is not _ABSENT:
            expected["raised"] = raised
        if returned is not _ABSENT:
            expected["returned"] = returned

        _get_test_verifier_or_raise().assert_interaction(self, **expected)

    def _get_enforce(self) -> bool:
        """Get the enforce flag from the proxy. Handles both _BaseMock and MockProxy."""
        proxy = self._proxy
        if isinstance(proxy, _BaseMock):
            return proxy._enforce
        # Old-style MockProxy: always enforce (sandbox-only usage)
        return True

    def _get_spy_flag(self) -> bool:
        """Check if proxy is in spy mode."""
        proxy = self._proxy
        if isinstance(proxy, _BaseMock):
            return proxy._spy
        # Old-style MockProxy: check _wraps
        wraps_obj: object = object.__getattribute__(proxy, "_wraps")
        return wraps_obj is not None

    def _get_wraps_target(self) -> Any:  # noqa: ANN401
        """Get the wraps target for delegation."""
        proxy = self._proxy
        if isinstance(proxy, _BaseMock):
            return getattr(proxy, "_wraps_target", None)
        # Old-style MockProxy
        return object.__getattribute__(proxy, "_wraps")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Called when the mock is invoked. Routes through bigfoot interceptor."""
        from bigfoot._context import get_verifier_or_raise

        # Step 1: Verify sandbox is active (raises SandboxNotActiveError if not)
        get_verifier_or_raise(self.source_id)

        # Step 2: Check side-effect queue; fall back to spy delegation if empty
        is_spy = self._get_spy_flag()
        wraps_target = self._get_wraps_target()

        if not self._config_queue:
            if not is_spy or wraps_target is None:
                raise UnmockedInteractionError(
                    source_id=self.source_id,
                    args=args,
                    kwargs=kwargs,
                    hint=self._plugin.format_unmocked_hint(self.source_id, args, kwargs),
                )
            # Spy delegation: call the original, record with returned/raised
            result: Any = _SENTINEL
            raised_exc: BaseException | None = None
            try:
                if self._method_name == "__call__":
                    # Direct callable: call the wraps target itself
                    result = wraps_target(*args, **kwargs)
                else:
                    real_method = getattr(wraps_target, self._method_name)
                    result = real_method(*args, **kwargs)
            except BaseException as exc:
                raised_exc = exc
                raise
            finally:
                details: dict[str, Any] = {
                    "mock_name": self._mock_name,
                    "method_name": self._method_name,
                    "args": args,
                    "kwargs": kwargs,
                }
                if raised_exc is not None:
                    details["raised"] = raised_exc
                elif result is not _SENTINEL:
                    details["returned"] = result

                interaction = Interaction(
                    source_id=self.source_id,
                    sequence=0,
                    details=details,
                    plugin=self._plugin,
                )
                interaction.enforce = self._get_enforce()
                self._plugin.record(interaction)
            return result

        config = self._config_queue.popleft()

        # Step 3: Record the interaction (with raised in details if applicable)
        details_dict: dict[str, Any] = {
            "mock_name": self._mock_name,
            "method_name": self._method_name,
            "args": args,
            "kwargs": kwargs,
        }

        if isinstance(config.side_effect, _RaiseException):
            details_dict["raised"] = config.side_effect.exc

        interaction = Interaction(
            source_id=self.source_id,
            sequence=0,
            details=details_dict,
            plugin=self._plugin,
        )
        interaction.enforce = self._get_enforce()
        self._plugin.record(interaction)

        # Step 4: Execute the side effect
        if isinstance(config.side_effect, _ReturnValue):
            return config.side_effect.value
        elif isinstance(config.side_effect, _RaiseException):
            raise config.side_effect.exc
        elif isinstance(config.side_effect, _CallFn):
            return config.side_effect.fn(*args, **kwargs)
        else:
            raise RuntimeError(f"Unknown side effect type: {type(config.side_effect)}")


# ---------------------------------------------------------------------------
# _BaseMock, ImportSiteMock, ObjectMock
# ---------------------------------------------------------------------------


class _BaseMock:
    """Base class for ImportSiteMock and ObjectMock.

    Handles method proxy management, context manager protocol, activation
    state tracking, and setattr-based patching/restoration.
    """

    def __init__(self, plugin: "MockPlugin", spy: bool = False) -> None:
        self._plugin = plugin
        self._spy = spy
        self._methods: dict[str, MethodProxy] = {}
        self._original: Any = None  # captured at activation time
        self._active: bool = False
        self._enforce: bool = False  # True when activated via sandbox
        if spy:
            self._wraps_target: Any = None
        # Register with plugin so SandboxContext can activate this mock
        plugin._mocks.append(self)

    # --- Subclass hook: resolve the (parent, attr_name) pair ---

    def _resolve_target(self) -> tuple[object, str]:
        """Return (parent_object, attr_name) for setattr patching."""
        raise NotImplementedError

    @property
    def _display_name(self) -> str:
        """Human-readable name for error messages and source_id."""
        raise NotImplementedError

    # --- Attribute access for method-level configuration ---

    def __getattr__(self, method_name: str) -> MethodProxy:
        if method_name.startswith("_") and method_name != "__call__":
            raise AttributeError(method_name)
        if method_name not in self._methods:
            self._methods[method_name] = MethodProxy(
                mock_name=self._display_name,
                method_name=method_name,
                plugin=self._plugin,
                proxy=self,
            )
        return self._methods[method_name]

    # --- Sync context manager (individual activation, enforce=False) ---

    def __enter__(self) -> "_BaseMock":
        from bigfoot._context import _active_verifier  # noqa: PLC0415

        self._activate(enforce=False)
        # Set active verifier so MethodProxy.__call__ can find it
        self._verifier_token = _active_verifier.set(self._plugin.verifier)
        return self

    def __exit__(self, *exc_info: Any) -> None:  # noqa: ANN401
        from bigfoot._context import _active_verifier  # noqa: PLC0415

        self._deactivate()
        if hasattr(self, "_verifier_token") and self._verifier_token is not None:
            _active_verifier.reset(self._verifier_token)
            del self._verifier_token

    # --- Async context manager ---

    async def __aenter__(self) -> "_BaseMock":
        from bigfoot._context import _active_verifier  # noqa: PLC0415

        self._activate(enforce=False)
        self._verifier_token = _active_verifier.set(self._plugin.verifier)
        return self

    async def __aexit__(self, *exc_info: Any) -> None:  # noqa: ANN401
        from bigfoot._context import _active_verifier  # noqa: PLC0415

        self._deactivate()
        if hasattr(self, "_verifier_token") and self._verifier_token is not None:
            _active_verifier.reset(self._verifier_token)
            del self._verifier_token

    # --- Activation / Deactivation ---

    def _activate(self, enforce: bool) -> None:
        """Resolve target, save original, install dispatcher via setattr."""
        if self._active:
            return  # Already active (e.g., individual + sandbox)
        parent, attr_name = self._resolve_target()
        self._original = getattr(parent, attr_name)
        self._enforce = enforce

        if self._spy:
            self._wraps_target = self._original

        # Conflict detection
        patch_key = (id(parent), attr_name)
        self._plugin._register_active_patch(patch_key, self)

        dispatcher = self._make_dispatcher()
        setattr(parent, attr_name, dispatcher)
        self._active = True

    def _deactivate(self) -> None:
        """Restore original via setattr, clear activation state."""
        if not self._active:
            return
        parent, attr_name = self._resolve_target()
        setattr(parent, attr_name, self._original)
        self._active = False

        patch_key = (id(parent), attr_name)
        self._plugin._unregister_active_patch(patch_key)

        self._original = None

    def _make_dispatcher(self) -> Any:  # noqa: ANN401
        """Create the replacement object installed at the import site."""
        mock_ref = self

        if callable(self._original) or not self._methods:
            def dispatch(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
                method = mock_ref.__getattr__("__call__")
                return method(*args, **kwargs)
            return dispatch
        else:
            return _MockDispatchProxy(mock_ref)

    # --- Ergonomic shortcuts for single-callable targets ---

    def returns(self, value: Any) -> "_BaseMock":  # noqa: ANN401
        self.__getattr__("__call__").returns(value)
        return self

    def raises(self, exc: BaseException | type[BaseException]) -> "_BaseMock":
        self.__getattr__("__call__").raises(exc)
        return self

    def calls(self, fn: Callable[..., Any]) -> "_BaseMock":
        self.__getattr__("__call__").calls(fn)
        return self

    def assert_call(self, **kwargs: Any) -> None:  # noqa: ANN401
        self.__getattr__("__call__").assert_call(**kwargs)


class ImportSiteMock(_BaseMock):
    """A mock registered via bigfoot.mock("mod:attr")."""

    def __init__(self, path: str, plugin: "MockPlugin", spy: bool = False) -> None:
        super().__init__(plugin=plugin, spy=spy)
        if ":" not in path:
            raise ValueError(
                f"Mock path {path!r} must use colon-separated format: "
                f"'module.path:attr.path'. Example: 'myapp.services:cache'"
            )
        self._path = path

    def _resolve_target(self) -> tuple[object, str]:
        from bigfoot._path_resolution import resolve_target  # noqa: PLC0415
        return resolve_target(self._path)

    @property
    def _display_name(self) -> str:
        return self._path


class ObjectMock(_BaseMock):
    """A mock registered via bigfoot.mock.object(target, "attr")."""

    def __init__(
        self, target: object, attr: str, plugin: "MockPlugin", spy: bool = False
    ) -> None:
        super().__init__(plugin=plugin, spy=spy)
        self._target = target
        self._attr = attr

    def _resolve_target(self) -> tuple[object, str]:
        return self._target, self._attr

    @property
    def _display_name(self) -> str:
        return f"{type(self._target).__name__}.{self._attr}"


# ---------------------------------------------------------------------------
# _MockDispatchProxy
# ---------------------------------------------------------------------------


class _MockDispatchProxy:
    """Installed at the import site when the target is an object with methods."""

    def __init__(self, mock: _BaseMock) -> None:
        object.__setattr__(self, "_mock", mock)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        mock: _BaseMock = object.__getattribute__(self, "_mock")
        if name in mock._methods:
            return mock._methods[name]
        # For spy mode, delegate unknown attributes to the wrapped original
        if mock._spy and mock._original is not None:
            return getattr(mock._original, name)
        raise AttributeError(f"Mock {mock._display_name!r} has no configured method {name!r}")


# ---------------------------------------------------------------------------
# MockProxy (legacy, kept for backward compatibility during migration)
# ---------------------------------------------------------------------------


class MockProxy:
    """Object returned by plugin.get_or_create_proxy('Name').

    Attribute access returns a cached MethodProxy for the named method.
    If wraps is set, method calls with an empty queue are delegated to the
    wrapped object instead of raising UnmockedInteractionError.
    """

    def __init__(self, name: str, plugin: "MockPlugin", wraps: object = None) -> None:
        # Use object.__setattr__ to avoid triggering __getattr__ during __init__
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_plugin", plugin)
        object.__setattr__(self, "_methods", {})
        object.__setattr__(self, "_wraps", wraps)

    @property
    def wraps(self) -> object:
        """The real object being wrapped, or None."""
        return object.__getattribute__(self, "_wraps")

    def __getattr__(self, method_name: str) -> MethodProxy:
        if method_name.startswith("_"):
            raise AttributeError(method_name)
        methods: dict[str, MethodProxy] = object.__getattribute__(self, "_methods")
        if method_name not in methods:
            mock_name: str = object.__getattribute__(self, "_name")
            plugin: MockPlugin = object.__getattribute__(self, "_plugin")
            methods[method_name] = MethodProxy(
                mock_name=mock_name,
                method_name=method_name,
                plugin=plugin,
                proxy=self,
            )
        return methods[method_name]


# ---------------------------------------------------------------------------
# MockPlugin
# ---------------------------------------------------------------------------


class MockPlugin(BasePlugin):
    """Core mock plugin: intercepts method calls on named proxy objects."""

    supports_guard: ClassVar[bool] = False

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        self._proxies: dict[str, MockProxy] = {}
        self._mocks: list[_BaseMock] = []
        self._active_patches: dict[tuple[int, str], _BaseMock] = {}

    # --- New API: import-site and object mocks ---

    def create_import_site_mock(
        self, path: str, *, spy: bool = False
    ) -> ImportSiteMock:
        """Create an ImportSiteMock. Registration happens in _BaseMock.__init__."""
        return ImportSiteMock(path=path, plugin=self, spy=spy)

    def create_object_mock(
        self, target: object, attr: str, *, spy: bool = False
    ) -> ObjectMock:
        """Create an ObjectMock. Registration happens in _BaseMock.__init__."""
        return ObjectMock(target=target, attr=attr, plugin=self, spy=spy)

    def _register_active_patch(
        self, patch_key: tuple[int, str], mock: _BaseMock
    ) -> None:
        """Register an active patch for conflict detection."""
        if patch_key in self._active_patches:
            existing = self._active_patches[patch_key]
            raise ConflictError(
                target=mock._display_name,
                patcher=f"bigfoot mock ({existing._display_name})",
            )
        self._active_patches[patch_key] = mock

    def _unregister_active_patch(self, patch_key: tuple[int, str]) -> None:
        """Unregister an active patch."""
        self._active_patches.pop(patch_key, None)

    # --- Legacy API (kept for backward compat during migration) ---

    def get_or_create_proxy(self, name: str, wraps: object = None) -> MockProxy:
        """Return an existing MockProxy for name, or create a new one.

        If wraps is provided and a proxy already exists, update its wraps
        attribute (e.g., spy() called after mock()). If no proxy exists,
        create a new one with wraps set.
        """
        if name not in self._proxies:
            self._proxies[name] = MockProxy(name=name, plugin=self, wraps=wraps)
        elif wraps is not None:
            # Allow updating wraps on a pre-existing proxy (e.g., spy() called after mock())
            object.__setattr__(self._proxies[name], "_wraps", wraps)
        return self._proxies[name]

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
        """Return True if all expected fields match the interaction's details."""
        try:
            for key, expected_val in expected.items():
                actual_val = interaction.details.get(key)
                if expected_val != actual_val:
                    return False
            return True
        except Exception:
            return False

    def format_interaction(self, interaction: Interaction) -> str:
        """One-line description: '[MockPlugin] MockName.method_name'."""
        # source_id is "mock:MockName.method_name"
        readable = interaction.source_id.replace("mock:", "[MockPlugin] ", 1)
        return readable

    def format_mock_hint(self, interaction: Interaction) -> str:
        """Copy-pasteable code to configure a mock for this interaction."""
        mock_name = interaction.details.get("mock_name", "?")
        method_name = interaction.details.get("method_name", "?")
        if "raised" in interaction.details:
            raised = interaction.details["raised"]
            return f'verifier.mock("{mock_name}").{method_name}.raises({raised!r})'
        return f'verifier.mock("{mock_name}").{method_name}.returns(<value>)'

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        """Copy-pasteable code snippet for mocking a call that had no side effect."""
        # source_id = "mock:Name.method"
        without_prefix = source_id.replace("mock:", "", 1)
        parts = without_prefix.split(".", 1)
        mock_name = parts[0] if len(parts) > 0 else "?"
        method_name = parts[1] if len(parts) > 1 else "?"
        return (
            f"Unexpected call to {mock_name}.{method_name}\n\n"
            f"  Called with: args={args!r}, kwargs={kwargs!r}\n\n"
            f"  To mock this interaction, add before your sandbox:\n"
            f'    verifier.mock("{mock_name}").{method_name}.returns(<value>)\n\n'
            f"  Or to mark it optional:\n"
            f'    verifier.mock("{mock_name}").{method_name}.required(False).returns(<value>)'
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        """Copy-pasteable code to assert this interaction."""
        mock_name = interaction.details.get("mock_name", "?")
        method_name = interaction.details.get("method_name", "?")
        args = interaction.details.get("args", ())
        kwargs = interaction.details.get("kwargs", {})
        lines = [
            f'verifier.mock("{mock_name}").{method_name}.assert_call(',
            f"    args={args!r},",
            f"    kwargs={kwargs!r},",
        ]
        if "raised" in interaction.details:
            lines.append(f"    raised={interaction.details['raised']!r},")
        if "returned" in interaction.details:
            lines.append(f"    returned={interaction.details['returned']!r},")
        lines.append(")")
        return "\n".join(lines)

    def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
        """Return the field names required in **expected when asserting a mock interaction.

        Adapts based on interaction content:
        - Standard mock calls: {args, kwargs}
        - .raises() side effects: {args, kwargs, raised}
        - Spy returned: {args, kwargs, returned}
        - Spy raised: {args, kwargs, raised}
        """
        base = {"args", "kwargs"}
        if "raised" in interaction.details:
            base.add("raised")
        if "returned" in interaction.details:
            base.add("returned")
        return frozenset(base)

    def get_unused_mocks(self) -> list[MockConfig]:
        """Return MockConfig objects that are required=True and still in the queue (never
        consumed)."""
        unused: list[MockConfig] = []
        # Legacy MockProxy path
        for proxy in self._proxies.values():
            methods = object.__getattribute__(proxy, "_methods")
            for method_proxy in methods.values():
                for config in method_proxy._config_queue:
                    if config.required:
                        unused.append(config)
        # New _BaseMock path
        for mock in self._mocks:
            for method_proxy in mock._methods.values():
                for config in method_proxy._config_queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_unused_mock_hint(self, mock_config: object) -> str:
        """Hint for an unused mock: show registration traceback and how to suppress."""
        assert isinstance(mock_config, MockConfig)
        return (
            f"mock:{mock_config.mock_name}.{mock_config.method_name}\n"
            f"    Mock registered at:\n"
            f"{mock_config.registration_traceback}\n"
            f"    Options:\n"
            f"      - Remove this mock if it's not needed\n"
            f'      - Mark it optional: verifier.mock("{mock_config.mock_name}")'
            f".{mock_config.method_name}.required(False).returns(...)"
        )
