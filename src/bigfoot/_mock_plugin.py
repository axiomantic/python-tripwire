"""MockPlugin, MockProxy, MethodProxy, MockConfig."""

import threading
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._base_plugin import BasePlugin
from bigfoot._errors import UnmockedInteractionError
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
# Never actually returned — the exception path re-raises before reaching `return result`.
_SENTINEL = object()


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

    Attribute access on MockProxy returns a MethodProxy. Calling it routes
    through the bigfoot interceptor.
    """

    def __init__(
        self, mock_name: str, method_name: str, plugin: "MockPlugin", proxy: "MockProxy"
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
    ) -> None:
        """Assert the next call to this mock method with the given arguments.

        Convenience wrapper around verifier.assert_interaction().
        """
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        _get_test_verifier_or_raise().assert_interaction(
            self,
            args=args,
            kwargs=kwargs if kwargs is not None else {},
        )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Called when the mock is invoked. Routes through bigfoot interceptor."""
        from bigfoot._context import _get_verifier_or_raise

        # Step 1: Verify sandbox is active (raises SandboxNotActiveError if not)
        _get_verifier_or_raise(self.source_id)

        # Step 2: Check side-effect queue; fall back to wraps delegation if empty
        wraps_obj: object = object.__getattribute__(self._proxy, "_wraps")
        if not self._config_queue:
            if wraps_obj is None:
                raise UnmockedInteractionError(
                    source_id=self.source_id,
                    args=args,
                    kwargs=kwargs,
                    hint=self._plugin.format_unmocked_hint(self.source_id, args, kwargs),
                )
            # Wraps delegation: call the real object, record interaction regardless
            result: Any = _SENTINEL
            try:
                real_method = getattr(wraps_obj, self._method_name)
                result = real_method(*args, **kwargs)
            finally:
                # Record even if the real method raised — result stays as _SENTINEL
                interaction = Interaction(
                    source_id=self.source_id,
                    sequence=0,
                    details={
                        "mock_name": self._mock_name,
                        "method_name": self._method_name,
                        "args": args,
                        "kwargs": kwargs,
                    },
                    plugin=self._plugin,
                )
                self._plugin.record(interaction)
            return result

        config = self._config_queue.popleft()

        # Step 3: Record the interaction
        interaction = Interaction(
            source_id=self.source_id,
            sequence=0,
            details={
                "mock_name": self._mock_name,
                "method_name": self._method_name,
                "args": args,
                "kwargs": kwargs,
            },
            plugin=self._plugin,
        )
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
# MockProxy
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

    _install_count: int = 0
    _install_lock: threading.Lock = threading.Lock()

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        self._proxies: dict[str, MockProxy] = {}

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

    def activate(self) -> None:
        """Reference-counted install. Increments _install_count under lock."""
        with MockPlugin._install_lock:
            MockPlugin._install_count += 1

    def deactivate(self) -> None:
        """Reference-counted uninstall. Decrements _install_count, floored at 0."""
        with MockPlugin._install_lock:
            MockPlugin._install_count = max(0, MockPlugin._install_count - 1)

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
        return (
            f'verifier.mock("{mock_name}").{method_name}.assert_call(\n'
            f"    args={args!r},\n"
            f"    kwargs={kwargs!r},\n"
            f")"
        )

    def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
        """Return the field names required in **expected when asserting a mock interaction."""
        return frozenset({"args", "kwargs"})

    def get_unused_mocks(self) -> list[MockConfig]:
        """Return MockConfig objects that are required=True and still in the queue (never
        consumed)."""
        unused: list[MockConfig] = []
        for proxy in self._proxies.values():
            methods = object.__getattribute__(proxy, "_methods")
            for method_proxy in methods.values():
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
