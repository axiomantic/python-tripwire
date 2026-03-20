"""LoggingPlugin: intercepts Python's logging module."""

import logging
import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import _get_verifier_or_raise
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Source ID constant
# ---------------------------------------------------------------------------

_SOURCE_LOG = "logging:log"

# ---------------------------------------------------------------------------
# Import-time constant — captured BEFORE any patches are installed.
# Used by _check_conflicts() to detect foreign patchers.
# ---------------------------------------------------------------------------

_LOGGER_LOG_ORIGINAL: Any = logging.Logger._log

# ---------------------------------------------------------------------------
# Module-level reference to our own interceptor.
# Set during _install_patches so _check_conflicts can distinguish bigfoot
# patches from foreign patches during nested sandbox activations.
# ---------------------------------------------------------------------------

_bigfoot_logger_log: Any = None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class LogMockConfig:
    """Internal record of a registered log mock."""

    level: str
    message: str
    logger_name: str | None
    required: bool
    registration_traceback: str = field(
        default_factory=lambda: "".join(traceback.format_stack()[:-2])
    )


# ---------------------------------------------------------------------------
# Sentinel (proxy handle)
# ---------------------------------------------------------------------------


class LogSentinel:
    """Opaque handle used as source filter in assert_interaction for logging."""

    source_id = _SOURCE_LOG

    def __init__(self, plugin: "LoggingPlugin") -> None:
        self._plugin = plugin


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_LEVEL_NAMES: dict[int, str] = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


def _find_logging_plugin(verifier: "StrictVerifier") -> "LoggingPlugin":
    for plugin in verifier._plugins:
        if isinstance(plugin, LoggingPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot LoggingPlugin interceptor is active but no "
        "LoggingPlugin is registered on the current verifier."
    )


def _identify_logging_patcher(method: object) -> str:
    mod = getattr(method, "__module__", None) or ""
    qualname = getattr(method, "__qualname__", None) or ""
    if "unittest.mock" in mod or "MagicMock" in qualname:
        return "unittest.mock"
    if "pytest_mock" in mod:
        return "pytest-mock"
    return "an unknown library"


# ---------------------------------------------------------------------------
# LoggingPlugin
# ---------------------------------------------------------------------------


class LoggingPlugin(BasePlugin):
    """Logging interception plugin.

    Patches logging.Logger._log globally. Uses reference counting
    so nested sandboxes work correctly, following the SubprocessPlugin pattern.
    """

    supports_guard: ClassVar[bool] = False

    # Class-level reference counting — shared across all instances/verifiers.
    _install_count: int = 0
    _install_lock: threading.Lock = threading.Lock()

    # Saved original, restored when count reaches 0.
    _original_logger_log: Any = None

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        # FIFO queue for log mocks (per-plugin instance, per-verifier)
        self._mock_queue: deque[LogMockConfig] = deque()
        self._mock_consumed: list[LogMockConfig] = []
        self._sentinel = LogSentinel(self)

    @property
    def log(self) -> LogSentinel:
        """Sentinel used as source argument in assert_interaction() for logging."""
        return self._sentinel

    def install(self) -> None:
        """No-op. Called to ensure plugin is registered before sandbox entry.

        Access to any attribute of log_mock triggers plugin creation via
        _LoggingProxy.__getattr__. This method exists as a named no-op so
        tests that want the interceptor active without any mocks have an
        explicit API to call.
        """

    # ------------------------------------------------------------------
    # Mock registration
    # ------------------------------------------------------------------

    def mock_log(
        self,
        level: str,
        message: str,
        logger_name: str | None = None,
        *,
        required: bool = True,
    ) -> None:
        """Register a FIFO log mock.

        Calls are matched in registration order. Unlike subprocess.run,
        unmocked log calls are swallowed (fire-and-forget) and recorded
        on the timeline, requiring assertion at teardown.
        """
        self._mock_queue.append(
            LogMockConfig(
                level=level.upper(),
                message=message,
                logger_name=logger_name,
                required=required,
            )
        )

    # ------------------------------------------------------------------
    # Assertion helpers
    # ------------------------------------------------------------------

    def assert_log(
        self,
        level: str,
        message: str,
        logger_name: str,
    ) -> None:
        """Assert the next log interaction with all 3 fields."""
        self.verifier.assert_interaction(
            self._sentinel,
            level=level.upper(),
            message=message,
            logger_name=logger_name,
        )

    def assert_debug(self, message: str, logger_name: str) -> None:
        """Assert the next log interaction is a DEBUG message."""
        self.assert_log("DEBUG", message, logger_name)

    def assert_info(self, message: str, logger_name: str) -> None:
        """Assert the next log interaction is an INFO message."""
        self.assert_log("INFO", message, logger_name)

    def assert_warning(self, message: str, logger_name: str) -> None:
        """Assert the next log interaction is a WARNING message."""
        self.assert_log("WARNING", message, logger_name)

    def assert_error(self, message: str, logger_name: str) -> None:
        """Assert the next log interaction is an ERROR message."""
        self.assert_log("ERROR", message, logger_name)

    def assert_critical(self, message: str, logger_name: str) -> None:
        """Assert the next log interaction is a CRITICAL message."""
        self.assert_log("CRITICAL", message, logger_name)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        with LoggingPlugin._install_lock:
            if LoggingPlugin._install_count == 0:
                self._check_conflicts()
                self._install_patches()
            LoggingPlugin._install_count += 1

    def deactivate(self) -> None:
        with LoggingPlugin._install_lock:
            LoggingPlugin._install_count = max(0, LoggingPlugin._install_count - 1)
            if LoggingPlugin._install_count == 0:
                self._restore_patches()

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def _check_conflicts(self) -> None:
        """Verify logging.Logger._log has not been patched by a third party."""
        from bigfoot._errors import ConflictError

        current_log = logging.Logger._log
        if (
            current_log is not _LOGGER_LOG_ORIGINAL
            and current_log is not _bigfoot_logger_log
        ):
            patcher = _identify_logging_patcher(current_log)
            raise ConflictError(
                target="logging.Logger._log",
                patcher=patcher,
            )

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        global _bigfoot_logger_log

        LoggingPlugin._original_logger_log = logging.Logger._log

        def _log_interceptor(
            logger_self: logging.Logger,
            level: int,
            msg: object,
            args: Any,  # noqa: ANN401
            **kwargs: Any,  # noqa: ANN401
        ) -> None:
            verifier = _get_verifier_or_raise(_SOURCE_LOG)
            plugin = _find_logging_plugin(verifier)
            plugin._handle_log(logger_self, level, msg, args)

        _bigfoot_logger_log = _log_interceptor

        logging.Logger._log = _log_interceptor  # type: ignore[assignment]

    def _restore_patches(self) -> None:
        global _bigfoot_logger_log

        if LoggingPlugin._original_logger_log is not None:
            logging.Logger._log = LoggingPlugin._original_logger_log  # type: ignore[method-assign]
            LoggingPlugin._original_logger_log = None

        _bigfoot_logger_log = None

    # ------------------------------------------------------------------
    # Request handler
    # ------------------------------------------------------------------

    def _handle_log(
        self,
        logger: logging.Logger,
        level: int,
        msg: object,
        args: Any,  # noqa: ANN401
    ) -> None:
        """Always-on interceptor for logging.

        Mocked log calls: consume from queue, record interaction.
        Unmocked log calls: swallow (fire-and-forget), record on timeline.
        All log calls require assertion at teardown.
        """
        level_name = _LEVEL_NAMES.get(level, f"LEVEL_{level}")

        # Format the message with args (matching logging module behavior)
        if args:
            try:
                formatted_message = str(msg) % args
            except (TypeError, ValueError):
                formatted_message = str(msg)
        else:
            formatted_message = str(msg)

        logger_name = logger.name

        # Check if there's a matching mock in the queue
        if self._mock_queue:
            config = self._mock_queue[0]
            if (
                config.level == level_name
                and config.message == formatted_message
                and (config.logger_name is None or config.logger_name == logger_name)
            ):
                self._mock_queue.popleft()
                self._mock_consumed.append(config)

        # Record on timeline (fire-and-forget: all logs are swallowed and recorded)
        interaction = Interaction(
            source_id=_SOURCE_LOG,
            sequence=0,
            details={
                "level": level_name,
                "message": formatted_message,
                "logger_name": logger_name,
            },
            plugin=self,
        )
        self.record(interaction)

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

    def format_interaction(self, interaction: Interaction) -> str:
        level = interaction.details.get("level", "?")
        message = interaction.details.get("message", "?")
        logger_name = interaction.details.get("logger_name", "?")
        return f"[LoggingPlugin] {level} {logger_name}: {message}"

    def format_mock_hint(self, interaction: Interaction) -> str:
        level = interaction.details.get("level", "INFO")
        message = interaction.details.get("message", "")
        logger_name = interaction.details.get("logger_name", "root")
        return (
            f"    bigfoot.log_mock.mock_log("
            f"{level!r}, {message!r}, logger_name={logger_name!r})"
        )

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        # This is less relevant for LoggingPlugin since unmocked calls are swallowed,
        # but we implement it for completeness.
        level = args[0] if args else "INFO"
        message = args[1] if len(args) > 1 else ""
        return (
            f"logging.{level.lower()}({message!r}) was called.\n"
            f"Register it with:\n"
            f"    bigfoot.log_mock.mock_log({level!r}, {message!r})"
        )

    def format_assert_hint(self, interaction: "Interaction") -> str:
        lm = "bigfoot.log_mock"
        level = interaction.details.get("level", "INFO")
        message = interaction.details.get("message", "")
        logger_name = interaction.details.get("logger_name", "root")
        return (
            f"    {lm}.assert_log(\n"
            f"        {level!r},\n"
            f"        {message!r},\n"
            f"        {logger_name!r},\n"
            f"    )"
        )

    def assertable_fields(self, interaction: "Interaction") -> frozenset[str]:
        return frozenset({"level", "message", "logger_name"})

    def get_unused_mocks(self) -> list[tuple[str, dict[str, Any], str]]:
        unused: list[tuple[str, dict[str, Any], str]] = []

        # Unused log mocks are those still in the FIFO queue with required=True
        for config in self._mock_queue:
            if config.required:
                unused.append(
                    (
                        _SOURCE_LOG,
                        {"level": config.level, "message": config.message},
                        config.registration_traceback,
                    )
                )

        return unused

    def format_unused_mock_hint(self, mock_config: object) -> str:
        from typing import cast

        source_id, details, registration_traceback = cast(
            tuple[str, dict[str, Any], str], mock_config
        )
        level = details.get("level", "?")
        message = details.get("message", "?")
        return (
            f"logging.{level.lower()}({message!r}) was mocked but never called.\n"
            f"Registered at:\n{registration_traceback}"
        )
