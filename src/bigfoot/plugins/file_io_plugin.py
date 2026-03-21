"""FileIoPlugin: intercepts file system operations with a per-operation FIFO queue.

This plugin patches builtins.open, pathlib.Path read/write methods, os file
operations, and shutil copy/remove operations. It uses a ContextVar-based
reentrancy guard to prevent self-interference with bigfoot's own file I/O.

NOT default enabled: requires explicit enabled_plugins = ["file_io"] in config.
"""

from __future__ import annotations

import builtins
import io
import os
import pathlib
import shutil
import threading
import traceback
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import get_active_verifier
from bigfoot._errors import ConflictError, UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Reentrancy guard
# ---------------------------------------------------------------------------

_file_io_bypass = ContextVar("_file_io_bypass", default=False)

# ---------------------------------------------------------------------------
# FileIoMockConfig
# ---------------------------------------------------------------------------


@dataclass
class FileIoMockConfig:
    """Configuration for a single mocked file I/O operation.

    Attributes:
        operation: The file operation name (e.g., "open", "read_text", "remove").
        path_pattern: The path pattern to match against.
        returns: The value to return when this mock is consumed.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the mock is reported as unused if never triggered.
        registration_traceback: Captured automatically at creation time.
    """

    operation: str
    path_pattern: str
    returns: Any = None  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _FileIoSentinel:
    """Opaque handle for a file I/O operation; used as source filter in assert_interaction."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


# ---------------------------------------------------------------------------
# Module-level helper: find the FileIoPlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_file_io_plugin(verifier: StrictVerifier) -> FileIoPlugin | None:
    for plugin in verifier._plugins:
        if isinstance(plugin, FileIoPlugin):
            return plugin
    return None


# ---------------------------------------------------------------------------
# Operation-to-format mapping for format_interaction
# ---------------------------------------------------------------------------

# Maps operation prefixes to their display format
_OP_DISPLAY = {
    "open": "open",
    "read_text": "Path.read_text",
    "read_bytes": "Path.read_bytes",
    "write_text": "Path.write_text",
    "write_bytes": "Path.write_bytes",
    "remove": "os.remove",
    "unlink": "os.unlink",
    "rename": "os.rename",
    "replace": "os.replace",
    "makedirs": "os.makedirs",
    "mkdir": "os.mkdir",
    "copy": "shutil.copy",
    "copy2": "shutil.copy2",
    "copytree": "shutil.copytree",
    "rmtree": "shutil.rmtree",
}

# Maps operation to which assert helper to suggest
_OP_ASSERT_HELPER = {
    "open": "assert_open",
    "read_text": "assert_read_text",
    "read_bytes": "assert_read_bytes",
    "write_text": "assert_write_text",
    "write_bytes": "assert_write_bytes",
    "remove": "assert_remove",
    "unlink": "assert_remove",
    "rename": "assert_rename",
    "replace": "assert_rename",
    "makedirs": "assert_makedirs",
    "mkdir": "assert_makedirs",
    "copy": "assert_copy",
    "copy2": "assert_copy",
    "copytree": "assert_copytree",
    "rmtree": "assert_rmtree",
}


# ---------------------------------------------------------------------------
# Interceptor functions
# ---------------------------------------------------------------------------


def _intercept_operation(
    operation: str,
    path: str,
    details: dict[str, Any],
    original_fn: Any,  # noqa: ANN401
    original_args: tuple[Any, ...],
    original_kwargs: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Core interception logic shared by all intercepted functions."""
    if _file_io_bypass.get():
        return original_fn(*original_args, **original_kwargs)

    token = _file_io_bypass.set(True)
    try:
        verifier = get_active_verifier()
        if verifier is None:
            return original_fn(*original_args, **original_kwargs)

        plugin = _get_file_io_plugin(verifier)
        if plugin is None:
            return original_fn(*original_args, **original_kwargs)

        source_id = f"file_io:{operation}"
        path = os.path.normpath(path)
        queue_key = f"{operation}:{path}"
        # Ensure details uses the normalized path for cross-platform consistency.
        for key in ("path", "src", "dst"):
            if key in details:
                details[key] = os.path.normpath(details[key])

        with plugin._registry_lock:
            queue = plugin._queues.get(queue_key)
            if not queue:
                hint = plugin.format_unmocked_hint(source_id, (path,), {})
                raise UnmockedInteractionError(
                    source_id=source_id,
                    args=(path,),
                    kwargs={},
                    hint=hint,
                )
            config = queue.popleft()

        if config.raises is not None:
            details["raised"] = config.raises
        interaction = Interaction(
            source_id=source_id,
            sequence=0,
            details=details,
            plugin=plugin,
        )
        plugin.record(interaction)

        if config.raises is not None:
            raise config.raises

        return config.returns
    finally:
        _file_io_bypass.reset(token)


def _intercepted_open(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for builtins.open."""
    # Parse arguments
    if args:
        file_path = str(args[0])
    else:
        file_path = str(kwargs.get("file", ""))

    mode = "r"
    if len(args) > 1:
        mode = args[1]
    else:
        mode = kwargs.get("mode", "r")

    # open(file, mode='r', buffering=-1, encoding=None, ...)
    # encoding is the 4th arg (index 3 in args after file is args[0])
    encoding = kwargs.get("encoding")
    if encoding is None and len(args) > 3:
        encoding = args[3]
    if encoding is None and "b" not in mode:
        encoding = "utf-8"

    details = {"path": file_path, "mode": mode, "encoding": encoding}

    result = _intercept_operation(
        "open",
        file_path,
        details,
        FileIoPlugin._original_open,
        args,
        kwargs,
    )

    # If result came from a mock (not original), wrap in appropriate IO
    if isinstance(result, str):
        return io.StringIO(result)
    elif isinstance(result, bytes):
        return io.BytesIO(result)
    elif result is None:
        # Write mode: return empty StringIO/BytesIO
        if "b" in mode:
            return io.BytesIO()
        return io.StringIO()
    return result


def _intercepted_read_text(self_path: pathlib.Path, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for pathlib.Path.read_text."""
    path_str = str(self_path)
    details = {"path": path_str}

    return _intercept_operation(
        "read_text",
        path_str,
        details,
        FileIoPlugin._original_read_text,
        (self_path, *args),
        kwargs,
    )


def _intercepted_read_bytes(self_path: pathlib.Path, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for pathlib.Path.read_bytes."""
    path_str = str(self_path)
    details = {"path": path_str}

    return _intercept_operation(
        "read_bytes",
        path_str,
        details,
        FileIoPlugin._original_read_bytes,
        (self_path, *args),
        kwargs,
    )


def _intercepted_write_text(self_path: pathlib.Path, data: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for pathlib.Path.write_text."""
    path_str = str(self_path)
    details = {"path": path_str, "data": data}

    return _intercept_operation(
        "write_text",
        path_str,
        details,
        FileIoPlugin._original_write_text,
        (self_path, data, *args),
        kwargs,
    )


def _intercepted_write_bytes(
    self_path: pathlib.Path, data: bytes,
    *args: Any, **kwargs: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    """Interceptor for pathlib.Path.write_bytes."""
    path_str = str(self_path)
    details = {"path": path_str, "data": data}

    return _intercept_operation(
        "write_bytes",
        path_str,
        details,
        FileIoPlugin._original_write_bytes,
        (self_path, data, *args),
        kwargs,
    )


def _intercepted_remove(path: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for os.remove."""
    path_str = str(path)
    details = {"path": path_str}

    return _intercept_operation(
        "remove",
        path_str,
        details,
        FileIoPlugin._original_remove,
        (path, *args),
        kwargs,
    )


def _intercepted_unlink(path: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for os.unlink."""
    path_str = str(path)
    details = {"path": path_str}

    return _intercept_operation(
        "unlink",
        path_str,
        details,
        FileIoPlugin._original_unlink,
        (path, *args),
        kwargs,
    )


def _intercepted_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for os.rename."""
    src_str = str(src)
    dst_str = str(dst)
    details = {"src": src_str, "dst": dst_str}

    return _intercept_operation(
        "rename",
        src_str,
        details,
        FileIoPlugin._original_rename,
        (src, dst, *args),
        kwargs,
    )


def _intercepted_replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for os.replace."""
    src_str = str(src)
    dst_str = str(dst)
    details = {"src": src_str, "dst": dst_str}

    return _intercept_operation(
        "replace",
        src_str,
        details,
        FileIoPlugin._original_replace,
        (src, dst, *args),
        kwargs,
    )


def _intercepted_makedirs(name: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for os.makedirs."""
    path_str = str(name)
    exist_ok = kwargs.get("exist_ok", False)
    # Check positional args for exist_ok (os.makedirs(name, mode=0o777, exist_ok=False))
    # mode is the second positional arg, exist_ok is the third
    if len(args) >= 2:
        exist_ok = args[1]
    details = {"path": path_str, "exist_ok": exist_ok}

    return _intercept_operation(
        "makedirs",
        path_str,
        details,
        FileIoPlugin._original_makedirs,
        (name, *args),
        kwargs,
    )


def _intercepted_mkdir(path: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for os.mkdir."""
    path_str = str(path)
    details = {"path": path_str}

    return _intercept_operation(
        "mkdir",
        path_str,
        details,
        FileIoPlugin._original_mkdir,
        (path, *args),
        kwargs,
    )


def _intercepted_copy(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for shutil.copy."""
    src_str = str(src)
    dst_str = str(dst)
    details = {"src": src_str, "dst": dst_str}

    return _intercept_operation(
        "copy",
        src_str,
        details,
        FileIoPlugin._original_copy,
        (src, dst, *args),
        kwargs,
    )


def _intercepted_copy2(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for shutil.copy2."""
    src_str = str(src)
    dst_str = str(dst)
    details = {"src": src_str, "dst": dst_str}

    return _intercept_operation(
        "copy2",
        src_str,
        details,
        FileIoPlugin._original_copy2,
        (src, dst, *args),
        kwargs,
    )


def _intercepted_copytree(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for shutil.copytree."""
    src_str = str(src)
    dst_str = str(dst)
    details = {"src": src_str, "dst": dst_str}

    return _intercept_operation(
        "copytree",
        src_str,
        details,
        FileIoPlugin._original_copytree,
        (src, dst, *args),
        kwargs,
    )


def _intercepted_rmtree(path: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Interceptor for shutil.rmtree."""
    path_str = str(path)
    details = {"path": path_str}

    return _intercept_operation(
        "rmtree",
        path_str,
        details,
        FileIoPlugin._original_rmtree,
        (path, *args),
        kwargs,
    )


# ---------------------------------------------------------------------------
# FileIoPlugin
# ---------------------------------------------------------------------------


class FileIoPlugin(BasePlugin):
    """File I/O interception plugin.

    Patches builtins.open, pathlib.Path read/write, os file ops, and shutil
    copy/remove at the module/class level. Uses reference counting so nested
    sandboxes work correctly.

    Each operation+path_pattern combination has its own FIFO deque of
    FileIoMockConfig objects.

    NOT default enabled: requires explicit enabled_plugins = ["file_io"].
    """

    supports_guard: ClassVar[bool] = False

    # Saved originals, restored when count reaches 0
    _original_open: ClassVar[Any] = None
    _original_read_text: ClassVar[Any] = None
    _original_read_bytes: ClassVar[Any] = None
    _original_write_text: ClassVar[Any] = None
    _original_write_bytes: ClassVar[Any] = None
    _original_remove: ClassVar[Any] = None
    _original_unlink: ClassVar[Any] = None
    _original_rename: ClassVar[Any] = None
    _original_replace: ClassVar[Any] = None
    _original_makedirs: ClassVar[Any] = None
    _original_mkdir: ClassVar[Any] = None
    _original_copy: ClassVar[Any] = None
    _original_copy2: ClassVar[Any] = None
    _original_copytree: ClassVar[Any] = None
    _original_rmtree: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[FileIoMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API: register mock operations
    # ------------------------------------------------------------------

    def mock_operation(
        self,
        operation: str,
        path_pattern: str,
        *,
        returns: Any = None,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a single file I/O operation invocation.

        Args:
            operation: The file operation name (e.g., "open", "read_text", "remove").
            path_pattern: The path pattern to match against.
            returns: Value to return when this mock is consumed.
            raises: If provided, this exception is raised instead of returning.
            required: If False, the mock is not reported as unused at teardown.
        """
        normalized_path = os.path.normpath(path_pattern)
        config = FileIoMockConfig(
            operation=operation,
            path_pattern=normalized_path,
            returns=returns,
            raises=raises,
            required=required,
        )
        queue_key = f"{operation}:{normalized_path}"
        with self._registry_lock:
            if queue_key not in self._queues:
                self._queues[queue_key] = deque()
            self._queues[queue_key].append(config)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def _check_conflicts(self) -> None:
        """Verify builtins.open has not been patched by a third party."""
        current_open = builtins.open
        if hasattr(current_open, "__module__") and current_open.__module__ not in (
            "builtins",
            "_io",
            "io",
            None,
        ):
            mod = current_open.__module__
            if "unittest.mock" in mod:
                patcher = "unittest.mock"
            elif "pytest_mock" in mod:
                patcher = "pytest-mock"
            else:
                patcher = "an unknown library"
            raise ConflictError(target="builtins.open", patcher=patcher)

    def _install_patches(self) -> None:
        """Install file I/O interceptors."""
        # Save originals
        FileIoPlugin._original_open = builtins.open
        FileIoPlugin._original_read_text = pathlib.Path.read_text
        FileIoPlugin._original_read_bytes = pathlib.Path.read_bytes
        FileIoPlugin._original_write_text = pathlib.Path.write_text
        FileIoPlugin._original_write_bytes = pathlib.Path.write_bytes
        FileIoPlugin._original_remove = os.remove
        FileIoPlugin._original_unlink = os.unlink
        FileIoPlugin._original_rename = os.rename
        FileIoPlugin._original_replace = os.replace
        FileIoPlugin._original_makedirs = os.makedirs
        FileIoPlugin._original_mkdir = os.mkdir
        FileIoPlugin._original_copy = shutil.copy
        FileIoPlugin._original_copy2 = shutil.copy2
        FileIoPlugin._original_copytree = shutil.copytree
        FileIoPlugin._original_rmtree = shutil.rmtree

        # Install interceptors
        builtins.open = _intercepted_open
        pathlib.Path.read_text = _intercepted_read_text  # type: ignore[assignment, method-assign]
        pathlib.Path.read_bytes = _intercepted_read_bytes  # type: ignore[assignment, method-assign]
        pathlib.Path.write_text = _intercepted_write_text  # type: ignore[assignment, method-assign]
        pathlib.Path.write_bytes = _intercepted_write_bytes  # type: ignore[assignment, method-assign]
        os.remove = _intercepted_remove
        os.unlink = _intercepted_unlink
        os.rename = _intercepted_rename
        os.replace = _intercepted_replace
        os.makedirs = _intercepted_makedirs
        os.mkdir = _intercepted_mkdir
        shutil.copy = _intercepted_copy
        shutil.copy2 = _intercepted_copy2
        shutil.copytree = _intercepted_copytree
        shutil.rmtree = _intercepted_rmtree  # type: ignore[assignment]

    def _restore_patches(self) -> None:
        """Restore original file I/O functions."""
        if FileIoPlugin._original_open is not None:
            builtins.open = FileIoPlugin._original_open
            FileIoPlugin._original_open = None
        if FileIoPlugin._original_read_text is not None:
            pathlib.Path.read_text = FileIoPlugin._original_read_text  # type: ignore[method-assign]
            FileIoPlugin._original_read_text = None
        if FileIoPlugin._original_read_bytes is not None:
            pathlib.Path.read_bytes = FileIoPlugin._original_read_bytes  # type: ignore[method-assign]
            FileIoPlugin._original_read_bytes = None
        if FileIoPlugin._original_write_text is not None:
            pathlib.Path.write_text = FileIoPlugin._original_write_text  # type: ignore[method-assign]
            FileIoPlugin._original_write_text = None
        if FileIoPlugin._original_write_bytes is not None:
            pathlib.Path.write_bytes = FileIoPlugin._original_write_bytes  # type: ignore[method-assign]
            FileIoPlugin._original_write_bytes = None
        if FileIoPlugin._original_remove is not None:
            os.remove = FileIoPlugin._original_remove
            FileIoPlugin._original_remove = None
        if FileIoPlugin._original_unlink is not None:
            os.unlink = FileIoPlugin._original_unlink
            FileIoPlugin._original_unlink = None
        if FileIoPlugin._original_rename is not None:
            os.rename = FileIoPlugin._original_rename
            FileIoPlugin._original_rename = None
        if FileIoPlugin._original_replace is not None:
            os.replace = FileIoPlugin._original_replace
            FileIoPlugin._original_replace = None
        if FileIoPlugin._original_makedirs is not None:
            os.makedirs = FileIoPlugin._original_makedirs
            FileIoPlugin._original_makedirs = None
        if FileIoPlugin._original_mkdir is not None:
            os.mkdir = FileIoPlugin._original_mkdir
            FileIoPlugin._original_mkdir = None
        if FileIoPlugin._original_copy is not None:
            shutil.copy = FileIoPlugin._original_copy
            FileIoPlugin._original_copy = None
        if FileIoPlugin._original_copy2 is not None:
            shutil.copy2 = FileIoPlugin._original_copy2
            FileIoPlugin._original_copy2 = None
        if FileIoPlugin._original_copytree is not None:
            shutil.copytree = FileIoPlugin._original_copytree
            FileIoPlugin._original_copytree = None
        if FileIoPlugin._original_rmtree is not None:
            shutil.rmtree = FileIoPlugin._original_rmtree
            FileIoPlugin._original_rmtree = None

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

    def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
        """All detail fields are required in assert_interaction()."""
        return frozenset(interaction.details.keys())

    def get_unused_mocks(self) -> list[FileIoMockConfig]:
        """Return all FileIoMockConfig with required=True still in any queue."""
        unused: list[FileIoMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else source_id

        details = interaction.details
        display = _OP_DISPLAY.get(operation, operation)

        if operation in ("open",):
            path = details.get("path", "?")
            mode = details.get("mode", "?")
            return f"[FileIoPlugin] {display}('{path}', mode='{mode}')"
        elif operation in ("rename", "replace"):
            src = details.get("src", "?")
            dst = details.get("dst", "?")
            return f"[FileIoPlugin] {display}('{src}', '{dst}')"
        elif operation in ("copy", "copy2", "copytree"):
            src = details.get("src", "?")
            dst = details.get("dst", "?")
            return f"[FileIoPlugin] {display}('{src}', '{dst}')"
        elif operation in ("makedirs", "mkdir"):
            path = details.get("path", "?")
            exist_ok = details.get("exist_ok", False)
            return f"[FileIoPlugin] {display}('{path}', exist_ok={exist_ok})"
        else:
            # read_text, read_bytes, write_text, write_bytes, remove, unlink, rmtree
            path = details.get("path", "?")
            return f"[FileIoPlugin] {display}('{path}')"

    def format_mock_hint(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        path = interaction.details.get("path", interaction.details.get("src", "?"))
        return f"    bigfoot.file_io_mock.mock_operation('{operation}', '{path}', returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        operation = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        path = args[0] if args else "?"
        display = _OP_DISPLAY.get(operation, operation)
        return (
            f"{display}('{path}', ...) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    bigfoot.file_io_mock.mock_operation('{operation}', '{path}', returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        helper = _OP_ASSERT_HELPER.get(operation, f"assert_{operation}")

        lines = [f"    bigfoot.file_io_mock.{helper}("]
        for key, val in interaction.details.items():
            lines.append(f"        {key}={val!r},")
        lines.append("    )")
        return "\n".join(lines)

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config: FileIoMockConfig = mock_config  # type: ignore[assignment]
        operation = getattr(config, "operation", "?")
        path_pattern = getattr(config, "path_pattern", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"file_io:{operation}('{path_pattern}') was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def assert_open(
        self,
        **expected: Any,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next file_io:open interaction.

        All three fields (path, mode, encoding) are required by
        assertable_fields, but this helper accepts **kwargs so that
        the verifier can enforce completeness via MissingAssertionFieldsError.
        """
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = "file_io:open"
        sentinel = _FileIoSentinel(source_id)
        if "path" in expected:
            expected["path"] = os.path.normpath(expected["path"])
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            **expected,
        )

    def assert_read_text(self, path: str) -> None:
        """Typed helper: assert the next file_io:read_text interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = "file_io:read_text"
        sentinel = _FileIoSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            path=os.path.normpath(path),
        )

    def assert_read_bytes(self, path: str) -> None:
        """Typed helper: assert the next file_io:read_bytes interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = "file_io:read_bytes"
        sentinel = _FileIoSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            path=os.path.normpath(path),
        )

    def assert_write_text(self, path: str, data: str) -> None:
        """Typed helper: assert the next file_io:write_text interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = "file_io:write_text"
        sentinel = _FileIoSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            path=os.path.normpath(path),
            data=data,
        )

    def assert_write_bytes(self, path: str, data: bytes) -> None:
        """Typed helper: assert the next file_io:write_bytes interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = "file_io:write_bytes"
        sentinel = _FileIoSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            path=os.path.normpath(path),
            data=data,
        )

    def assert_remove(self, path: str) -> None:
        """Typed helper: assert the next file_io:remove or file_io:unlink interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        verifier = _get_test_verifier_or_raise()
        # Try both remove and unlink source_ids
        timeline = verifier._timeline
        for interaction in timeline.all_unasserted():
            if interaction.source_id in ("file_io:remove", "file_io:unlink"):
                sentinel = _FileIoSentinel(interaction.source_id)
                verifier.assert_interaction(
                    sentinel, path=os.path.normpath(path),
                )
                return
        # Fall back to remove
        sentinel = _FileIoSentinel("file_io:remove")
        verifier.assert_interaction(sentinel, path=os.path.normpath(path))

    def assert_rename(self, src: str, dst: str) -> None:
        """Typed helper: assert the next file_io:rename or file_io:replace interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        verifier = _get_test_verifier_or_raise()
        nsrc, ndst = os.path.normpath(src), os.path.normpath(dst)
        # Try both rename and replace source_ids
        timeline = verifier._timeline
        for interaction in timeline.all_unasserted():
            if interaction.source_id in ("file_io:rename", "file_io:replace"):
                sentinel = _FileIoSentinel(interaction.source_id)
                verifier.assert_interaction(sentinel, src=nsrc, dst=ndst)
                return
        # Fall back to rename
        sentinel = _FileIoSentinel("file_io:rename")
        verifier.assert_interaction(sentinel, src=nsrc, dst=ndst)

    def assert_makedirs(self, path: str, exist_ok: bool) -> None:
        """Typed helper: assert the next file_io:makedirs interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _FileIoSentinel("file_io:makedirs")
        _get_test_verifier_or_raise().assert_interaction(
            sentinel, path=os.path.normpath(path), exist_ok=exist_ok,
        )

    def assert_mkdir(self, path: str) -> None:
        """Typed helper: assert the next file_io:mkdir interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _FileIoSentinel("file_io:mkdir")
        _get_test_verifier_or_raise().assert_interaction(
            sentinel, path=os.path.normpath(path),
        )

    def assert_copy(self, src: str, dst: str) -> None:
        """Typed helper: assert the next file_io:copy or file_io:copy2 interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        verifier = _get_test_verifier_or_raise()
        nsrc, ndst = os.path.normpath(src), os.path.normpath(dst)
        timeline = verifier._timeline
        for interaction in timeline.all_unasserted():
            if interaction.source_id in ("file_io:copy", "file_io:copy2"):
                sentinel = _FileIoSentinel(interaction.source_id)
                verifier.assert_interaction(sentinel, src=nsrc, dst=ndst)
                return
        sentinel = _FileIoSentinel("file_io:copy")
        verifier.assert_interaction(sentinel, src=nsrc, dst=ndst)

    def assert_copytree(self, src: str, dst: str) -> None:
        """Typed helper: assert the next file_io:copytree interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = "file_io:copytree"
        sentinel = _FileIoSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            src=os.path.normpath(src),
            dst=os.path.normpath(dst),
        )

    def assert_rmtree(self, path: str) -> None:
        """Typed helper: assert the next file_io:rmtree interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = "file_io:rmtree"
        sentinel = _FileIoSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            path=os.path.normpath(path),
        )
