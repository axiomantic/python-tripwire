"""NativePlugin: intercepts ctypes.CDLL and cffi.FFI.dlopen with per-function FIFO queues."""

from __future__ import annotations

import ctypes
import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import _get_verifier_or_raise
from bigfoot._errors import ConflictError, UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import cffi as cffi_lib

    _CFFI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CFFI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Capture originals at module-load time for conflict detection
# ---------------------------------------------------------------------------

_CDLL_INIT_ORIGINAL: Any = ctypes.CDLL.__init__

# ---------------------------------------------------------------------------
# NativeMockConfig
# ---------------------------------------------------------------------------


@dataclass
class NativeMockConfig:
    """Configuration for a single mocked native function call.

    Attributes:
        library: The library name (e.g., "libm").
        function: The function name (e.g., "sqrt").
        returns: The value to return when this mock is consumed.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the mock is reported as unused if never triggered.
        registration_traceback: Captured automatically at creation time.
    """

    library: str
    function: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper: find the NativePlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_native_plugin() -> NativePlugin:
    verifier = _get_verifier_or_raise("native:call")
    for plugin in verifier._plugins:
        if isinstance(plugin, NativePlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot NativePlugin interceptor is active but no "
        "NativePlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _NativeSentinel:
    """Opaque handle for a native function call; used as source filter in assert_interaction."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


# ---------------------------------------------------------------------------
# Argument serialization
# ---------------------------------------------------------------------------


def _serialize_struct(value: Any) -> dict[str, Any]:  # noqa: ANN401
    """Convert a ctypes.Structure to a dict of field_name -> value."""
    result: dict[str, Any] = {}
    for field_name, _ in value._fields_:
        result[field_name] = getattr(value, field_name)
    return result


def _serialize_arg(value: Any) -> Any:  # noqa: ANN401
    """Simplify ctypes argument to Python equivalent."""
    if isinstance(value, ctypes.Structure):
        return _serialize_struct(value)
    if isinstance(value, ctypes._SimpleCData):
        return value.value
    if isinstance(value, ctypes._CFuncPtr):  # type: ignore[attr-defined]
        return "<callback>"
    if callable(value) and hasattr(value, "_type_"):
        return "<callback>"
    if isinstance(value, ctypes._Pointer):
        try:
            return value.contents if value else None
        except ValueError:
            return None
    return value


# ---------------------------------------------------------------------------
# Proxy classes
# ---------------------------------------------------------------------------


class _FuncProxy:
    """Proxy for a single native function. Records calls and returns mocked values."""

    def __init__(self, plugin: NativePlugin, library_name: str, function_name: str) -> None:
        self._plugin = plugin
        self._library_name = library_name
        self._function_name = function_name

    def __call__(self, *args: Any) -> Any:  # noqa: ANN401
        queue_key = f"{self._library_name}:{self._function_name}"
        source_id = f"native:{self._library_name}:{self._function_name}"

        with self._plugin._registry_lock:
            queue = self._plugin._queues.get(queue_key)
            if not queue:
                serialized_args = tuple(_serialize_arg(a) for a in args)
                hint = self._plugin.format_unmocked_hint(source_id, serialized_args, {})
                raise UnmockedInteractionError(
                    source_id=source_id,
                    args=serialized_args,
                    kwargs={},
                    hint=hint,
                )
            config = queue.popleft()

        serialized_args = tuple(_serialize_arg(a) for a in args)
        details_native: dict[str, Any] = {
            "library": self._library_name,
            "function": self._function_name,
            "args": serialized_args,
        }
        if config.raises is not None:
            details_native["raised"] = config.raises
        interaction = Interaction(
            source_id=source_id,
            sequence=0,
            details=details_native,
            plugin=self._plugin,
        )
        self._plugin.record(interaction)

        if config.raises is not None:
            raise config.raises
        return config.returns


class CdllProxy:
    """Proxy that replaces loaded C libraries via ctypes.CDLL."""

    def __init__(self, library_name: str, plugin: NativePlugin) -> None:
        # Use object.__setattr__ to avoid triggering __getattr__
        object.__setattr__(self, "_library_name", library_name)
        object.__setattr__(self, "_plugin", plugin)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, name: str) -> _FuncProxy:
        if object.__getattribute__(self, "_closed"):
            raise OSError(f"Library '{self._library_name}' is closed")
        return _FuncProxy(
            object.__getattribute__(self, "_plugin"),
            object.__getattribute__(self, "_library_name"),
            name,
        )

    def close(self) -> None:
        object.__setattr__(self, "_closed", True)


class CffiProxy:
    """Proxy that replaces loaded C libraries via cffi.FFI.dlopen (ABI mode)."""

    def __init__(self, library_name: str, plugin: NativePlugin) -> None:
        object.__setattr__(self, "_library_name", library_name)
        object.__setattr__(self, "_plugin", plugin)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, name: str) -> _FuncProxy:
        if object.__getattribute__(self, "_closed"):
            raise OSError(f"Library '{self._library_name}' is closed")
        return _FuncProxy(
            object.__getattribute__(self, "_plugin"),
            object.__getattribute__(self, "_library_name"),
            name,
        )

    def close(self) -> None:
        object.__setattr__(self, "_closed", True)


# ---------------------------------------------------------------------------
# Patched CDLL.__init__
# ---------------------------------------------------------------------------


def _patched_cdll_init(cdll_self: Any, name: Any, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
    """Replacement for ctypes.CDLL.__init__ that converts the instance into a CdllProxy."""
    plugin = _get_native_plugin()
    library_name = name if isinstance(name, str) else str(name)
    # Replace the CDLL instance's class with CdllProxy by setting attributes directly
    cdll_self.__class__ = CdllProxy
    object.__setattr__(cdll_self, "_library_name", library_name)
    object.__setattr__(cdll_self, "_plugin", plugin)
    object.__setattr__(cdll_self, "_closed", False)


# ---------------------------------------------------------------------------
# Identify patcher for conflict detection
# ---------------------------------------------------------------------------


def _identify_native_patcher(method: object) -> str:
    mod = getattr(method, "__module__", None) or ""
    qualname = getattr(method, "__qualname__", None) or ""
    if "unittest.mock" in mod or "MagicMock" in qualname:
        return "unittest.mock"
    if "pytest_mock" in mod:
        return "pytest-mock"
    return "an unknown library"


# ---------------------------------------------------------------------------
# NativePlugin
# ---------------------------------------------------------------------------


class NativePlugin(BasePlugin):
    """Native function interception plugin.

    Patches ctypes.CDLL.__init__ and optionally cffi.FFI.dlopen at the class level.
    Uses reference counting so nested sandboxes work correctly.

    Each library:function pair has its own FIFO deque of NativeMockConfig objects.
    """

    supports_guard: ClassVar[bool] = False

    # Saved originals, restored when count reaches 0
    _original_cdll_init: ClassVar[Any] = None
    _original_ffi_dlopen: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[NativeMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API: register mock calls
    # ------------------------------------------------------------------

    def mock_call(
        self,
        library: str,
        function: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a single native function call.

        Args:
            library: The library name (e.g., "libm").
            function: The function name (e.g., "sqrt").
            returns: Value to return when this mock is consumed.
            raises: If provided, this exception is raised instead of returning.
            required: If False, the mock is not reported as unused at teardown.
        """
        config = NativeMockConfig(
            library=library,
            function=function,
            returns=returns,
            raises=raises,
            required=required,
        )
        queue_key = f"{library}:{function}"
        with self._registry_lock:
            if queue_key not in self._queues:
                self._queues[queue_key] = deque()
            self._queues[queue_key].append(config)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def _check_conflicts(self) -> None:
        """Verify ctypes.CDLL.__init__ has not been patched by a third party."""
        current_init = ctypes.CDLL.__init__
        if (
            current_init is not _CDLL_INIT_ORIGINAL
            and current_init is not _patched_cdll_init
        ):
            patcher = _identify_native_patcher(current_init)
            raise ConflictError(
                target="ctypes.CDLL.__init__",
                patcher=patcher,
            )

    def _install_patches(self) -> None:
        """Install ctypes.CDLL and optionally cffi.FFI patches."""
        NativePlugin._original_cdll_init = ctypes.CDLL.__init__
        ctypes.CDLL.__init__ = _patched_cdll_init  # type: ignore[assignment]

        # Optionally patch cffi if available
        if _CFFI_AVAILABLE:
            NativePlugin._original_ffi_dlopen = cffi_lib.FFI.dlopen
            cffi_lib.FFI.dlopen = _patched_ffi_dlopen

    def _restore_patches(self) -> None:
        """Restore original ctypes.CDLL and cffi.FFI functions."""
        if NativePlugin._original_cdll_init is not None:
            ctypes.CDLL.__init__ = NativePlugin._original_cdll_init  # type: ignore[method-assign]
            NativePlugin._original_cdll_init = None
        if NativePlugin._original_ffi_dlopen is not None and _CFFI_AVAILABLE:
            cffi_lib.FFI.dlopen = NativePlugin._original_ffi_dlopen
            NativePlugin._original_ffi_dlopen = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
        """Field-by-field comparison with dirty-equals support."""
        try:
            for key, expected_val in expected.items():
                actual_val = interaction.details.get(key)
                if expected_val != actual_val:
                    return False
            return True
        except Exception:
            return False

    def get_unused_mocks(self) -> list[NativeMockConfig]:
        """Return all NativeMockConfig with required=True still in any queue."""
        unused: list[NativeMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        library = interaction.details.get("library", "?")
        function = interaction.details.get("function", "?")
        args = interaction.details.get("args", ())
        parts = [repr(a) for a in args]
        return f"[NativePlugin] {library}.{function}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        library = interaction.details.get("library", "?")
        function = interaction.details.get("function", "?")
        return f"    bigfoot.native_mock.mock_call({library!r}, {function!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        # source_id is like "native:libm:sqrt"
        parts = source_id.split(":", 2)
        library = parts[1] if len(parts) > 1 else "?"
        function = parts[2] if len(parts) > 2 else "?"
        return (
            f"{library}.{function}(...) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    bigfoot.native_mock.mock_call({library!r}, {function!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.native_mock"
        library = interaction.details.get("library", "?")
        function = interaction.details.get("function", "?")
        args = interaction.details.get("args", ())
        return (
            f"    {sm}.assert_call(\n"
            f"        library={library!r},\n"
            f"        function={function!r},\n"
            f"        args={args!r},\n"
            f"    )"
        )

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config: NativeMockConfig = mock_config  # type: ignore[assignment]
        library = getattr(config, "library", "?")
        function = getattr(config, "function", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"{library}.{function}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helper
    # ------------------------------------------------------------------

    def assert_call(
        self,
        library: str,
        function: str,
        *,
        args: tuple[Any, ...] = (),
    ) -> None:
        """Typed helper: assert the next native function call interaction.

        Wraps assert_interaction() for ergonomic use. All three fields
        (library, function, args) are required.
        """
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"native:{library}:{function}"
        sentinel = _NativeSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            library=library,
            function=function,
            args=args,
        )


# ---------------------------------------------------------------------------
# Patched cffi.FFI.dlopen
# ---------------------------------------------------------------------------


def _patched_ffi_dlopen(ffi_self: Any, name: Any, *args: Any, **kwargs: Any) -> CffiProxy:  # noqa: ANN401
    """Replacement for cffi.FFI.dlopen that returns a CffiProxy."""
    plugin = _get_native_plugin()
    library_name = name if isinstance(name, str) else str(name)
    return CffiProxy(library_name, plugin)
