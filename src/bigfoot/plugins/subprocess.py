"""SubprocessPlugin: intercepts subprocess.run and shutil.which."""

import shutil
import subprocess
import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import _get_verifier_or_raise, _guard_allowlist, _GuardPassThrough
from bigfoot._errors import ConflictError, UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_RUN = "subprocess:run"
_SOURCE_WHICH = "subprocess:which"

# ---------------------------------------------------------------------------
# Import-time constants — captured BEFORE any patches are installed.
# Used by _check_conflicts() to detect foreign patchers.
# ---------------------------------------------------------------------------

_SUBPROCESS_RUN_ORIGINAL: Any = subprocess.run
_SHUTIL_WHICH_ORIGINAL: Any = shutil.which

# ---------------------------------------------------------------------------
# Module-level references to our own interceptors.
# Set during _install_patches so _check_conflicts can distinguish bigfoot
# patches from foreign patches during nested sandbox activations.
# ---------------------------------------------------------------------------

_bigfoot_subprocess_run: Any = None
_bigfoot_shutil_which: Any = None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RunMockConfig:
    """Internal record of a registered subprocess.run mock."""

    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    raises: BaseException | None
    required: bool
    registration_traceback: str = field(
        default_factory=lambda: "".join(traceback.format_stack()[:-2])
    )


@dataclass
class WhichMockConfig:
    """Internal record of a registered shutil.which mock."""

    name: str
    returns: str | None
    required: bool
    registration_traceback: str = field(
        default_factory=lambda: "".join(traceback.format_stack()[:-2])
    )


# ---------------------------------------------------------------------------
# Sentinels (proxy handles)
# ---------------------------------------------------------------------------


class SubprocessRunSentinel:
    """Opaque handle used as source filter in assert_interaction for subprocess.run."""

    source_id = _SOURCE_RUN

    def __init__(self, plugin: "SubprocessPlugin") -> None:
        self._plugin = plugin


class SubprocessWhichSentinel:
    """Opaque handle used as source filter in assert_interaction for shutil.which."""

    source_id = _SOURCE_WHICH

    def __init__(self, plugin: "SubprocessPlugin") -> None:
        self._plugin = plugin


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_subprocess_plugin(verifier: "StrictVerifier") -> "SubprocessPlugin":
    try:
        return next(p for p in verifier._plugins if isinstance(p, SubprocessPlugin))
    except StopIteration:
        raise RuntimeError(
            "BUG: bigfoot SubprocessPlugin interceptor is active but no "
            "SubprocessPlugin is registered on the current verifier."
        ) from None


def _identify_subprocess_patcher(method: object) -> str:
    mod = getattr(method, "__module__", None) or ""
    qualname = getattr(method, "__qualname__", None) or ""
    if "unittest.mock" in mod or "MagicMock" in qualname:
        return "unittest.mock"
    if "pytest_mock" in mod:
        return "pytest-mock"
    return "an unknown library"


# ---------------------------------------------------------------------------
# SubprocessPlugin
# ---------------------------------------------------------------------------


class SubprocessPlugin(BasePlugin):
    """Subprocess interception plugin.

    Patches subprocess.run and shutil.which globally. Uses reference counting
    so nested sandboxes work correctly, following the HttpPlugin pattern exactly.
    """

    # Class-level reference counting — shared across all instances/verifiers.
    _install_count: int = 0
    _install_lock: threading.Lock = threading.Lock()

    # Saved originals, restored when count reaches 0.
    _original_subprocess_run: Any = None
    _original_shutil_which: Any = None

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        # FIFO queue for run mocks (per-plugin instance, per-verifier)
        self._run_queue: deque[RunMockConfig] = deque()
        # Dict keyed by binary name for which mocks
        self._which_mocks: dict[str, WhichMockConfig] = {}
        # Set of which() names that were actually called (for unused-mock tracking)
        self._which_called: set[str] = set()
        self._run_sentinel = SubprocessRunSentinel(self)
        self._which_sentinel = SubprocessWhichSentinel(self)

    @property
    def run(self) -> SubprocessRunSentinel:
        """Sentinel used as source argument in assert_interaction() for subprocess.run."""
        return self._run_sentinel

    @property
    def which(self) -> SubprocessWhichSentinel:
        """Sentinel used as source argument in assert_interaction() for shutil.which."""
        return self._which_sentinel

    def install(self) -> None:
        """No-op. Called to ensure plugin is registered before sandbox entry.

        Access to any attribute of subprocess_mock triggers plugin creation via
        _SubprocessProxy.__getattr__. This method exists as a named no-op so
        tests that want the bouncer active without any mocks have an explicit
        API to call.
        """

    # ------------------------------------------------------------------
    # Mock registration
    # ------------------------------------------------------------------

    def mock_run(
        self,
        command: list[str],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a FIFO subprocess.run mock.

        Calls are matched in registration order. An unmocked or out-of-order
        call raises UnmockedInteractionError immediately (bouncer guarantee).
        """
        self._run_queue.append(
            RunMockConfig(
                command=command,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                raises=raises,
                required=required,
            )
        )

    def mock_which(
        self,
        name: str,
        returns: str | None,
        *,
        required: bool = False,
    ) -> None:
        """Register a shutil.which mock keyed by binary name.

        Always-on: unregistered names are swallowed (return None) and recorded
        on the timeline, requiring assertion at teardown. Registered names
        return the configured value. required=False by default because
        tests often register more alternatives than will be hit in a given path.
        """
        self._which_mocks[name] = WhichMockConfig(
            name=name,
            returns=returns,
            required=required,
        )

    # ------------------------------------------------------------------
    # Assertion helpers
    # ------------------------------------------------------------------

    def assert_run(
        self,
        command: list[str],
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        """Assert the next subprocess.run interaction with all 4 fields."""
        self.verifier.assert_interaction(
            self._run_sentinel,
            command=command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def assert_which(
        self,
        name: str,
        returns: str | None,
    ) -> None:
        """Assert the next shutil.which interaction with all 2 fields."""
        self.verifier.assert_interaction(
            self._which_sentinel,
            name=name,
            returns=returns,
        )

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        with SubprocessPlugin._install_lock:
            if SubprocessPlugin._install_count == 0:
                self._check_conflicts()
                self._install_patches()
            SubprocessPlugin._install_count += 1

    def deactivate(self) -> None:
        with SubprocessPlugin._install_lock:
            SubprocessPlugin._install_count = max(0, SubprocessPlugin._install_count - 1)
            if SubprocessPlugin._install_count == 0:
                self._restore_patches()

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def _check_conflicts(self) -> None:
        """Verify subprocess.run and shutil.which have not been patched by a third party."""
        current_run = subprocess.run
        if (
            current_run is not _SUBPROCESS_RUN_ORIGINAL
            and current_run is not _bigfoot_subprocess_run
        ):
            patcher = _identify_subprocess_patcher(current_run)
            raise ConflictError(
                target="subprocess.run",
                patcher=patcher,
            )

        current_which = shutil.which
        if (
            current_which is not _SHUTIL_WHICH_ORIGINAL
            and current_which is not _bigfoot_shutil_which
        ):
            patcher = _identify_subprocess_patcher(current_which)
            raise ConflictError(
                target="shutil.which",
                patcher=patcher,
            )

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        global _bigfoot_subprocess_run, _bigfoot_shutil_which

        SubprocessPlugin._original_subprocess_run = subprocess.run
        SubprocessPlugin._original_shutil_which = shutil.which

        def _run_interceptor(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            # Check allowlist FIRST - bypasses both guard and sandbox
            if "subprocess" in _guard_allowlist.get():
                return SubprocessPlugin._original_subprocess_run(*args, **kwargs)
            try:
                verifier = _get_verifier_or_raise(_SOURCE_RUN)
            except _GuardPassThrough:
                return SubprocessPlugin._original_subprocess_run(*args, **kwargs)
            plugin = _find_subprocess_plugin(verifier)
            return plugin._handle_run(*args, **kwargs)

        def _which_interceptor(name: str, **kwargs: Any) -> str | None:  # noqa: ANN401
            # Check allowlist FIRST - bypasses both guard and sandbox
            if "subprocess" in _guard_allowlist.get():
                return SubprocessPlugin._original_shutil_which(name, **kwargs)  # type: ignore[no-any-return]
            try:
                verifier = _get_verifier_or_raise(_SOURCE_WHICH)
            except _GuardPassThrough:
                return SubprocessPlugin._original_shutil_which(name, **kwargs)  # type: ignore[no-any-return]
            plugin = _find_subprocess_plugin(verifier)
            return plugin._handle_which(name, **kwargs)

        _bigfoot_subprocess_run = _run_interceptor
        _bigfoot_shutil_which = _which_interceptor

        subprocess.run = _run_interceptor
        shutil.which = _which_interceptor  # type: ignore[assignment]

    def _restore_patches(self) -> None:
        global _bigfoot_subprocess_run, _bigfoot_shutil_which

        if SubprocessPlugin._original_subprocess_run is not None:
            subprocess.run = SubprocessPlugin._original_subprocess_run
            SubprocessPlugin._original_subprocess_run = None

        if SubprocessPlugin._original_shutil_which is not None:
            shutil.which = SubprocessPlugin._original_shutil_which
            SubprocessPlugin._original_shutil_which = None

        _bigfoot_subprocess_run = None
        _bigfoot_shutil_which = None

    # ------------------------------------------------------------------
    # Request handlers
    # ------------------------------------------------------------------

    def _handle_run(self, *args: Any, **kwargs: Any) -> "subprocess.CompletedProcess[str]":  # noqa: ANN401
        """FIFO interceptor for subprocess.run."""
        # Normalize: subprocess.run accepts cmd as first positional arg or via args= keyword
        if args:
            cmd = args[0]
        else:
            cmd = kwargs.get("args", [])
        if isinstance(cmd, str):
            raise TypeError(
                f"subprocess.run() was called with a string command {cmd!r}. "
                f"bigfoot requires a list of arguments, e.g. {[cmd]!r}. "
                f"Pass a list to avoid ambiguity between shell and non-shell invocations."
            )
        cmd_list = list(cmd)

        if not self._run_queue:
            hint = self.format_unmocked_hint(_SOURCE_RUN, (cmd_list,), {})
            raise UnmockedInteractionError(
                source_id=_SOURCE_RUN,
                args=(cmd_list,),
                kwargs={},
                hint=hint,
            )

        config = self._run_queue[0]

        if cmd_list != config.command:
            hint = self.format_unmocked_hint(_SOURCE_RUN, (cmd_list,), {})
            raise UnmockedInteractionError(
                source_id=_SOURCE_RUN,
                args=(cmd_list,),
                kwargs={},
                hint=hint,
            )

        self._run_queue.popleft()

        # Record on timeline BEFORE potentially raising (call still happened)
        interaction = Interaction(
            source_id=_SOURCE_RUN,
            sequence=0,
            details={
                "command": config.command,
                "returncode": config.returncode,
                "stdout": config.stdout,
                "stderr": config.stderr,
            },
            plugin=self,
        )
        self.record(interaction)

        if config.raises is not None:
            raise config.raises

        return subprocess.CompletedProcess(
            args=config.command,
            returncode=config.returncode,
            stdout=config.stdout,
            stderr=config.stderr,
        )

    def _handle_which(self, name: str, **kwargs: Any) -> str | None:  # noqa: ANN401
        """Always-on interceptor for shutil.which.

        Registered names: return configured value and record interaction.
        Unregistered names: record interaction, return None (fire-and-forget swallow).
        """
        if name in self._which_mocks:
            self._which_called.add(name)
            config = self._which_mocks[name]
            interaction = Interaction(
                source_id=_SOURCE_WHICH,
                sequence=0,
                details={"name": name, "returns": config.returns},
                plugin=self,
            )
            self.record(interaction)
            return config.returns

        # Fire-and-forget: swallow unmocked which() calls.
        # Record on timeline so test must assert. Return None (binary not found).
        interaction = Interaction(
            source_id=_SOURCE_WHICH,
            sequence=0,
            details={"name": name, "returns": None},
            plugin=self,
        )
        self.record(interaction)
        return None

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
        if interaction.source_id == _SOURCE_RUN:
            cmd = interaction.details.get("command", [])
            rc = interaction.details.get("returncode", "?")
            cmd_str = " ".join(str(c) for c in cmd)
            return f"[SubprocessPlugin] run: {cmd_str} (returncode={rc})"
        if interaction.source_id == _SOURCE_WHICH:
            name = interaction.details.get("name", "?")
            returns = interaction.details.get("returns")
            return f"[SubprocessPlugin] which({name!r}) -> {returns!r}"
        return f"[SubprocessPlugin] unknown source_id={interaction.source_id!r}"

    def format_mock_hint(self, interaction: Interaction) -> str:
        if interaction.source_id == _SOURCE_RUN:
            cmd = interaction.details.get("command", [])
            rc = interaction.details.get("returncode", 0)
            stdout = interaction.details.get("stdout", "")
            stderr = interaction.details.get("stderr", "")
            parts = [f"    bigfoot.subprocess_mock.mock_run({cmd!r}"]
            if rc != 0:
                parts.append(f", returncode={rc!r}")
            if stdout:
                parts.append(f", stdout={stdout!r}")
            if stderr:
                parts.append(f", stderr={stderr!r}")
            parts.append(")")
            return "".join(parts)
        if interaction.source_id == _SOURCE_WHICH:
            name = interaction.details.get("name", "?")
            returns = interaction.details.get("returns")
            return f"    bigfoot.subprocess_mock.mock_which({name!r}, returns={returns!r})"
        return f"    # unknown source_id={interaction.source_id!r}"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        if source_id == _SOURCE_RUN:
            cmd = args[0] if args else kwargs.get("args", [])
            return (
                f"subprocess.run({list(cmd)!r}) was called but no mock was registered.\n"
                f"Register it with:\n"
                f"    bigfoot.subprocess_mock.mock_run({list(cmd)!r})"
            )
        if source_id == _SOURCE_WHICH:
            name = args[0] if args else kwargs.get("name", "?")
            return (
                f"shutil.which({name!r}) was called but no mock was registered.\n"
                f"Register it with:\n"
                f"    bigfoot.subprocess_mock.mock_which({name!r}, returns='/path/to/{name}')"
            )
        return f"Unmocked call to source_id={source_id!r}"

    def format_assert_hint(self, interaction: "Interaction") -> str:
        sm = "bigfoot.subprocess_mock"
        if interaction.source_id == _SOURCE_RUN:
            cmd = interaction.details.get("command", [])
            rc = interaction.details.get("returncode", 0)
            stdout = interaction.details.get("stdout", "")
            stderr = interaction.details.get("stderr", "")
            return (
                f"    {sm}.assert_run(\n"
                f"        command={cmd!r},\n"
                f"        returncode={rc!r},\n"
                f"        stdout={stdout!r},\n"
                f"        stderr={stderr!r},\n"
                f"    )"
            )
        if interaction.source_id == _SOURCE_WHICH:
            name = interaction.details.get("name", "?")
            returns = interaction.details.get("returns")
            return (
                f"    {sm}.assert_which(name={name!r}, returns={returns!r})"
            )
        return f"    # unknown source_id={interaction.source_id!r}"

    def assertable_fields(self, interaction: "Interaction") -> frozenset[str]:
        if interaction.source_id == _SOURCE_RUN:
            return frozenset({"command", "returncode", "stdout", "stderr"})
        if interaction.source_id == _SOURCE_WHICH:
            return frozenset({"name", "returns"})
        return frozenset()

    def get_unused_mocks(self) -> list[tuple[str, dict[str, Any], str]]:
        unused: list[tuple[str, dict[str, Any], str]] = []

        # Unused run mocks are those still in the FIFO queue with required=True
        for config in self._run_queue:
            if config.required:
                unused.append(
                    (
                        _SOURCE_RUN,
                        {"command": config.command},
                        config.registration_traceback,
                    )
                )

        # Unused which mocks with required=True that were never called
        for wconfig in self._which_mocks.values():
            if wconfig.required and wconfig.name not in self._which_called:
                unused.append(
                    (
                        _SOURCE_WHICH,
                        {"name": wconfig.name},
                        wconfig.registration_traceback,
                    )
                )

        return unused

    def format_unused_mock_hint(self, mock_config: object) -> str:
        source_id, details, registration_traceback = cast(
            tuple[str, dict[str, Any], str], mock_config
        )
        if source_id == _SOURCE_RUN:
            cmd = details.get("command", [])
            return (
                f"subprocess.run({cmd!r}) was mocked but never called.\n"
                f"Registered at:\n{registration_traceback}"
            )
        if source_id == _SOURCE_WHICH:
            name = details.get("name", "?")
            return (
                f"shutil.which({name!r}) was mocked (required=True) but never called.\n"
                f"Registered at:\n{registration_traceback}"
            )
        return f"Unused mock for source_id={source_id!r}"
