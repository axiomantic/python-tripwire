"""RedisPlugin: intercepts redis.Redis.execute_command with a per-command FIFO queue."""

from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import _get_verifier_or_raise, _guard_allowlist, _GuardPassThrough
from bigfoot._errors import UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import redis as redis_lib

    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REDIS_AVAILABLE = False


# ---------------------------------------------------------------------------
# RedisMockConfig
# ---------------------------------------------------------------------------


@dataclass
class RedisMockConfig:
    """Configuration for a single mocked Redis command invocation.

    Attributes:
        command: The Redis command name, normalized to uppercase.
        returns: The value to return when this mock is consumed.
            There is no default; callers must be explicit.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the mock is reported as unused if never triggered.
        registration_traceback: Captured automatically at creation time
            for use in error messages.
    """

    command: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper: find the RedisPlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_redis_plugin() -> RedisPlugin | None:
    verifier = _get_verifier_or_raise("redis:execute_command")
    for plugin in verifier._plugins:
        if isinstance(plugin, RedisPlugin):
            return plugin
    return None


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _RedisSentinel:
    """Opaque handle for a Redis command; used as source filter in assert_interaction."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


# ---------------------------------------------------------------------------
# Patched execute_command
# ---------------------------------------------------------------------------


def _patched_execute_command(redis_self: object, command: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    # Check allowlist FIRST - bypasses both guard and sandbox
    if "redis" in _guard_allowlist.get():
        return RedisPlugin._original_execute_command(redis_self, command, *args, **kwargs)
    try:
        plugin = _get_redis_plugin()
    except _GuardPassThrough:
        return RedisPlugin._original_execute_command(redis_self, command, *args, **kwargs)
    if plugin is None:
        return RedisPlugin._original_execute_command(redis_self, command, *args, **kwargs)
    cmd_upper = command.upper()
    with plugin._registry_lock:
        queue = plugin._queues.get(cmd_upper)
        if not queue:
            source_id = f"redis:{cmd_upper.lower()}"
            hint = plugin.format_unmocked_hint(source_id, args, kwargs)
            raise UnmockedInteractionError(
                source_id=source_id,
                args=args,
                kwargs=kwargs,
                hint=hint,
            )
        config = queue.popleft()

    # Record interaction on the shared timeline
    details: dict[str, Any] = {"command": cmd_upper, "args": args, "kwargs": kwargs}
    if config.raises is not None:
        details["raised"] = config.raises
    interaction = Interaction(
        source_id=f"redis:{cmd_upper.lower()}",
        sequence=0,
        details=details,
        plugin=plugin,
    )
    plugin.record(interaction)
    # No mark_asserted() — test authors must call assert_interaction() or assert_command()

    if config.raises is not None:
        raise config.raises
    return config.returns


# ---------------------------------------------------------------------------
# RedisPlugin
# ---------------------------------------------------------------------------


class RedisPlugin(BasePlugin):
    """Redis interception plugin.

    Patches redis.Redis.execute_command at the class level.
    Uses reference counting so nested sandboxes work correctly.

    Each command name (uppercase) has its own FIFO deque of RedisMockConfig
    objects. Calls are stateless -- there are no state transitions.
    """

    # Saved original, restored when count reaches 0.
    _original_execute_command: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[RedisMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API: register mock commands
    # ------------------------------------------------------------------

    def mock_command(
        self,
        command: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a single Redis command invocation.

        Args:
            command: The Redis command name (case-insensitive).
            returns: Value to return when this mock is consumed.
            raises: If provided, this exception is raised instead of returning.
            required: If False, the mock is not reported as unused at teardown.
        """
        cmd_upper = command.upper()
        config = RedisMockConfig(
            command=cmd_upper,
            returns=returns,
            raises=raises,
            required=required,
        )
        with self._registry_lock:
            if cmd_upper not in self._queues:
                self._queues[cmd_upper] = deque()
            self._queues[cmd_upper].append(config)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        """Install Redis.execute_command patch."""
        if not _REDIS_AVAILABLE:
            raise ImportError(
                "Install bigfoot[redis] to use RedisPlugin: pip install bigfoot[redis]"
            )
        RedisPlugin._original_execute_command = redis_lib.Redis.execute_command
        redis_lib.Redis.execute_command = _patched_execute_command  # type: ignore[assignment]

    def _restore_patches(self) -> None:
        """Restore original Redis.execute_command."""
        if RedisPlugin._original_execute_command is not None:
            redis_lib.Redis.execute_command = RedisPlugin._original_execute_command  # type: ignore[method-assign]
            RedisPlugin._original_execute_command = None

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
        """All three fields (command, args, kwargs) are required in assert_interaction()."""
        return frozenset({"command", "args", "kwargs"})

    def get_unused_mocks(self) -> list[RedisMockConfig]:
        """Return all RedisMockConfig with required=True still in any queue."""
        unused: list[RedisMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        command = interaction.details.get("command", "?")
        args = interaction.details.get("args", ())
        parts = [repr(a) for a in args]
        return f"[RedisPlugin] redis.{command}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        command = interaction.details.get("command", "?")
        return f"    bigfoot.redis_mock.mock_command({command!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        # source_id is like "redis:get"; reconstruct the uppercase command name.
        cmd = source_id.split(":", 1)[-1].upper() if ":" in source_id else source_id.upper()
        return (
            f"redis.{cmd}(...) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    bigfoot.redis_mock.mock_command({cmd!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.redis_mock"
        command = interaction.details.get("command", "?")
        args = interaction.details.get("args", ())
        kwargs = interaction.details.get("kwargs", {})
        return (
            f"    {sm}.assert_command(\n"
            f"        command={command!r},\n"
            f"        args={args!r},\n"
            f"        kwargs={kwargs!r},\n"
            f"    )"
        )

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config: RedisMockConfig = mock_config  # type: ignore[assignment]
        command = getattr(config, "command", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"redis.{command}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    def assert_command(
        self,
        command: str,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Typed helper: assert the next Redis command interaction.

        Wraps assert_interaction() for ergonomic use. All three fields
        (command, args, kwargs) are required.
        """
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        kw = kwargs if kwargs is not None else {}
        cmd_upper = command.upper()
        source_id = f"redis:{cmd_upper.lower()}"
        sentinel = _RedisSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            command=cmd_upper,
            args=args,
            kwargs=kw,
        )
