"""CeleryPlugin: intercepts celery.app.task.Task.delay and apply_async."""

from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import _get_verifier_or_raise
from bigfoot._errors import UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import celery.app.task  # noqa: F401

    _CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CELERY_AVAILABLE = False


# ---------------------------------------------------------------------------
# CeleryMockConfig
# ---------------------------------------------------------------------------


@dataclass
class CeleryMockConfig:
    """Configuration for a single mocked Celery task dispatch.

    Attributes:
        task_name: The Celery task name (e.g., "myapp.tasks.add").
        dispatch_method: Either "delay" or "apply_async".
        returns: The value to return when this mock is consumed.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the mock is reported as unused if never triggered.
        registration_traceback: Captured automatically at creation time.
    """

    task_name: str
    dispatch_method: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _get_celery_plugin() -> CeleryPlugin:
    verifier = _get_verifier_or_raise("celery:dispatch")
    for plugin in verifier._plugins:
        if isinstance(plugin, CeleryPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot CeleryPlugin interceptor is active but no "
        "CeleryPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _CelerySentinel:
    """Opaque handle for a Celery dispatch; used as source filter in assert_interaction."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


# ---------------------------------------------------------------------------
# Patched methods
# ---------------------------------------------------------------------------


def _patched_delay(task_self: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    plugin = _get_celery_plugin()
    task_name = task_self.name
    queue_key = f"{task_name}:delay"

    with plugin._registry_lock:
        queue = plugin._queues.get(queue_key)
        if not queue:
            source_id = f"celery:{task_name}:delay"
            hint = plugin.format_unmocked_hint(source_id, args, kwargs)
            raise UnmockedInteractionError(
                source_id=source_id,
                args=args,
                kwargs=kwargs,
                hint=hint,
            )
        config = queue.popleft()

    details: dict[str, Any] = {
        "task_name": task_name,
        "dispatch_method": "delay",
        "args": args,
        "kwargs": kwargs,
        "options": {},
    }
    if config.raises is not None:
        details["raised"] = config.raises
    interaction = Interaction(
        source_id=f"celery:{task_name}:delay",
        sequence=0,
        details=details,
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


def _patched_apply_async(
    task_self: Any,  # noqa: ANN401
    args: tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
    task_id: str | None = None,
    producer: Any = None,  # noqa: ANN401
    link: Any = None,  # noqa: ANN401
    link_error: Any = None,  # noqa: ANN401
    shadow: str | None = None,
    **options: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    plugin = _get_celery_plugin()
    task_name = task_self.name
    queue_key = f"{task_name}:apply_async"

    actual_args = args if args is not None else ()
    actual_kwargs = kwargs if kwargs is not None else {}

    # Collect all options into a single dict
    all_options: dict[str, Any] = {}
    if task_id is not None:
        all_options["task_id"] = task_id
    if link is not None:
        all_options["link"] = link
    if link_error is not None:
        all_options["link_error"] = link_error
    if shadow is not None:
        all_options["shadow"] = shadow
    all_options.update(options)

    with plugin._registry_lock:
        queue = plugin._queues.get(queue_key)
        if not queue:
            source_id = f"celery:{task_name}:apply_async"
            hint = plugin.format_unmocked_hint(source_id, actual_args, actual_kwargs)
            raise UnmockedInteractionError(
                source_id=source_id,
                args=actual_args,
                kwargs=actual_kwargs,
                hint=hint,
            )
        config = queue.popleft()

    details_apply: dict[str, Any] = {
        "task_name": task_name,
        "dispatch_method": "apply_async",
        "args": actual_args,
        "kwargs": actual_kwargs,
        "options": all_options,
    }
    if config.raises is not None:
        details_apply["raised"] = config.raises
    interaction = Interaction(
        source_id=f"celery:{task_name}:apply_async",
        sequence=0,
        details=details_apply,
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


# ---------------------------------------------------------------------------
# CeleryPlugin
# ---------------------------------------------------------------------------


class CeleryPlugin(BasePlugin):
    """Celery interception plugin.

    Patches celery.app.task.Task.delay and Task.apply_async at the class level.
    Uses reference counting so nested sandboxes work correctly.
    """

    supports_guard: ClassVar[bool] = False

    _original_delay: ClassVar[Any] = None
    _original_apply_async: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[CeleryMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mock_delay(
        self,
        task_name: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for task.delay() dispatch."""
        config = CeleryMockConfig(
            task_name=task_name,
            dispatch_method="delay",
            returns=returns,
            raises=raises,
            required=required,
        )
        queue_key = f"{task_name}:delay"
        with self._registry_lock:
            if queue_key not in self._queues:
                self._queues[queue_key] = deque()
            self._queues[queue_key].append(config)

    def mock_apply_async(
        self,
        task_name: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for task.apply_async() dispatch."""
        config = CeleryMockConfig(
            task_name=task_name,
            dispatch_method="apply_async",
            returns=returns,
            raises=raises,
            required=required,
        )
        queue_key = f"{task_name}:apply_async"
        with self._registry_lock:
            if queue_key not in self._queues:
                self._queues[queue_key] = deque()
            self._queues[queue_key].append(config)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        """Install Celery Task.delay and Task.apply_async patches."""
        if not _CELERY_AVAILABLE:
            raise ImportError(
                "Install bigfoot[celery] to use CeleryPlugin: pip install bigfoot[celery]"
            )
        from celery.app.task import Task

        CeleryPlugin._original_delay = Task.delay
        CeleryPlugin._original_apply_async = Task.apply_async
        Task.delay = _patched_delay
        Task.apply_async = _patched_apply_async

    def _restore_patches(self) -> None:
        """Restore original Celery Task methods."""
        from celery.app.task import Task

        if CeleryPlugin._original_delay is not None:
            Task.delay = CeleryPlugin._original_delay
            CeleryPlugin._original_delay = None
        if CeleryPlugin._original_apply_async is not None:
            Task.apply_async = CeleryPlugin._original_apply_async
            CeleryPlugin._original_apply_async = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
        try:
            for key, expected_val in expected.items():
                actual_val = interaction.details.get(key)
                if expected_val != actual_val:
                    return False
            return True
        except Exception:
            return False

    def get_unused_mocks(self) -> list[CeleryMockConfig]:
        unused: list[CeleryMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        task_name = interaction.details.get("task_name", "?")
        dispatch = interaction.details.get("dispatch_method", "?")
        args = interaction.details.get("args", ())
        return f"[CeleryPlugin] celery.{dispatch}({task_name!r}, args={args!r})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        task_name = interaction.details.get("task_name", "?")
        dispatch = interaction.details.get("dispatch_method", "?")
        return f"    bigfoot.celery_mock.mock_{dispatch}({task_name!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        # source_id format: "celery:<task_name>:<dispatch_method>"
        parts = source_id.split(":", 2)
        task_name = parts[1] if len(parts) > 1 else "?"
        dispatch = parts[2] if len(parts) > 2 else "?"
        return (
            f"celery.{dispatch}({task_name!r}, ...) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    bigfoot.celery_mock.mock_{dispatch}({task_name!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.celery_mock"
        dispatch = interaction.details.get("dispatch_method", "?")
        parts = []
        for k, v in interaction.details.items():
            parts.append(f"        {k}={v!r},")
        body = "\n".join(parts)
        return f"    {sm}.assert_{dispatch}(\n{body}\n    )"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config: CeleryMockConfig = mock_config  # type: ignore[assignment]
        task_name = getattr(config, "task_name", "?")
        dispatch = getattr(config, "dispatch_method", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"celery.{dispatch}({task_name!r}) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def assert_delay(
        self,
        task_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        options: dict[str, Any],
    ) -> None:
        """Typed helper: assert the next delay interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"celery:{task_name}:delay"
        sentinel = _CelerySentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            task_name=task_name,
            dispatch_method="delay",
            args=args,
            kwargs=kwargs,
            options=options,
        )

    def assert_apply_async(
        self,
        task_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        options: dict[str, Any],
    ) -> None:
        """Typed helper: assert the next apply_async interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"celery:{task_name}:apply_async"
        sentinel = _CelerySentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            task_name=task_name,
            dispatch_method="apply_async",
            args=args,
            kwargs=kwargs,
            options=options,
        )
