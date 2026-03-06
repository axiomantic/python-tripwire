"""PopenPlugin: intercepts subprocess.Popen via class replacement.

Coexistence with SubprocessPlugin
----------------------------------
SubprocessPlugin patches subprocess.run (and shutil.which) as function-level
replacements. PopenPlugin patches subprocess.Popen as a class replacement.
The two plugins target independent names in the subprocess module and do not
interfere with each other. Both use reference-counted class-level locks and
restore their respective targets correctly when deactivated.
"""

import subprocess
import threading
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._context import _get_verifier_or_raise
from bigfoot._errors import ConflictError
from bigfoot._state_machine_plugin import StateMachinePlugin, _StepSentinel
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_SPAWN = "subprocess:popen:spawn"
_SOURCE_COMMUNICATE = "subprocess:popen:communicate"
_SOURCE_WAIT = "subprocess:popen:wait"

# ---------------------------------------------------------------------------
# Import-time constant -- captured BEFORE any patches are installed.
# ---------------------------------------------------------------------------

_ORIGINAL_POPEN: Any = subprocess.Popen

# ---------------------------------------------------------------------------
# Module-level references to our own interceptor class.
# Set during _install_patches so _check_conflicts can distinguish bigfoot's
# _FakePopen from foreign patchers during nested sandbox activations.
# ---------------------------------------------------------------------------

_bigfoot_popen_class: Any = None


# ---------------------------------------------------------------------------
# Module-level helper: find the PopenPlugin on the active verifier
# ---------------------------------------------------------------------------


def _find_popen_plugin() -> "PopenPlugin":
    verifier = _get_verifier_or_raise(_SOURCE_SPAWN)
    for plugin in verifier._plugins:
        if isinstance(plugin, PopenPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot PopenPlugin interceptor is active but no "
        "PopenPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# _FakeStream
# ---------------------------------------------------------------------------


class _FakeStream:
    """Fake file-like stream. Stream I/O is not recorded by bigfoot.

    .write() returns 0 (no bytes written). .read() returns b"" (no data).
    Use communicate() to observe stdin input and stdout/stderr output via
    the named fields in the spawn interaction.
    """

    def write(self, data: bytes) -> int:
        """No-op write. Returns 0. Not recorded on the timeline."""
        return 0

    def read(self, size: int = -1) -> bytes:
        """No-op read. Returns b"". Not recorded on the timeline."""
        return b""

    def readline(self) -> bytes:
        """No-op readline. Returns b"". Not recorded on the timeline."""
        return b""


# ---------------------------------------------------------------------------
# _FakePopen
# ---------------------------------------------------------------------------


class _FakePopen:
    """Fake subprocess.Popen that routes all operations through PopenPlugin."""

    def __init__(
        self,
        args: Any,  # noqa: ANN401
        *,
        stdin: Any = None,  # noqa: ANN401
        stdout: Any = None,  # noqa: ANN401
        stderr: Any = None,  # noqa: ANN401
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        plugin = _find_popen_plugin()
        plugin._bind_connection(self)
        command = list(args) if hasattr(args, "__iter__") and not isinstance(args, str) else [args]
        plugin._execute_step(
            plugin._lookup_session(self), "spawn", (args,), {}, _SOURCE_SPAWN,
            details={
                "command": command,
                "stdin": stdin if isinstance(stdin, (bytes, type(None))) else None,
            },
        )
        self.stdin: _FakeStream = _FakeStream()
        self.stdout: _FakeStream = _FakeStream()
        self.stderr: _FakeStream = _FakeStream()
        self.returncode: int | None = None
        self.pid: int = 12345  # fake PID

    def communicate(
        self,
        input: bytes | None = None,  # noqa: A002
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        plugin = _find_popen_plugin()
        handle = plugin._lookup_session(self)
        result = plugin._execute_step(
            handle, "communicate", (), {}, _SOURCE_COMMUNICATE,
            details={"input": input},
        )
        # result is (stdout: bytes, stderr: bytes, returncode: int) 3-tuple
        out_bytes, err_bytes, returncode = result
        self.returncode = returncode
        return out_bytes, err_bytes

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        plugin = _find_popen_plugin()
        handle = plugin._lookup_session(self)
        result = plugin._execute_step(
            handle, "wait", (), {}, _SOURCE_WAIT,
            details={},
        )
        # result is returncode int
        self.returncode = int(result)
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


# ---------------------------------------------------------------------------
# PopenPlugin
# ---------------------------------------------------------------------------


class PopenPlugin(StateMachinePlugin):
    """Popen interception plugin.

    Replaces subprocess.Popen with _FakePopen at activate() time and restores
    the original at deactivate() time. Uses reference counting so nested
    sandboxes work correctly.

    States: created -> running -> terminated

    Coexists with SubprocessPlugin: SubprocessPlugin patches subprocess.run and
    shutil.which; PopenPlugin patches subprocess.Popen. Both plugins target
    independent names in the subprocess module and restore correctly.
    """

    # Class-level reference counting -- shared across all instances/verifiers.
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    # Saved original, restored when count reaches 0.
    _original_popen: ClassVar[Any] = None

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        self._spawn_sentinel = _StepSentinel(_SOURCE_SPAWN)
        self._communicate_sentinel = _StepSentinel(_SOURCE_COMMUNICATE)
        self._wait_sentinel = _StepSentinel(_SOURCE_WAIT)

    @property
    def spawn(self) -> _StepSentinel:
        return self._spawn_sentinel

    @property
    def communicate(self) -> _StepSentinel:
        return self._communicate_sentinel

    @property
    def wait(self) -> _StepSentinel:
        return self._wait_sentinel

    # ------------------------------------------------------------------
    # StateMachinePlugin abstract methods
    # ------------------------------------------------------------------

    def _initial_state(self) -> str:
        return "created"

    def _transitions(self) -> dict[str, dict[str, str]]:
        return {
            "spawn": {"created": "running"},
            "communicate": {"running": "terminated"},
            "wait": {"running": "terminated"},
        }

    def _unmocked_source_id(self) -> str:
        return "subprocess:popen:spawn"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        global _bigfoot_popen_class

        with PopenPlugin._install_lock:
            if PopenPlugin._install_count == 0:
                self._check_conflicts()
                PopenPlugin._original_popen = subprocess.Popen
                _bigfoot_popen_class = _FakePopen
                subprocess.Popen = _FakePopen  # type: ignore[assignment, misc]
            PopenPlugin._install_count += 1

    def deactivate(self) -> None:
        global _bigfoot_popen_class

        with PopenPlugin._install_lock:
            PopenPlugin._install_count = max(0, PopenPlugin._install_count - 1)
            if PopenPlugin._install_count == 0:
                if PopenPlugin._original_popen is not None:
                    subprocess.Popen = PopenPlugin._original_popen  # type: ignore[misc]
                    PopenPlugin._original_popen = None
                _bigfoot_popen_class = None

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def _check_conflicts(self) -> None:
        """Verify subprocess.Popen has not been patched by a third party."""
        current_popen: Any = subprocess.Popen
        if current_popen is not _ORIGINAL_POPEN and current_popen is not _FakePopen:
            mod = getattr(current_popen, "__module__", None) or ""
            qualname = getattr(current_popen, "__qualname__", None) or ""
            if "unittest.mock" in mod or "MagicMock" in qualname:
                patcher = "unittest.mock"
            elif "pytest_mock" in mod:
                patcher = "pytest-mock"
            else:
                patcher = "an unknown library"
            raise ConflictError(target="subprocess.Popen", patcher=patcher)

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        if interaction.source_id == _SOURCE_SPAWN:
            command = interaction.details.get("command", [])
            return f"[PopenPlugin] popen.spawn({command!r})"
        if interaction.source_id == _SOURCE_COMMUNICATE:
            inp = interaction.details.get("input")
            return f"[PopenPlugin] popen.communicate(input={inp!r})"
        if interaction.source_id == _SOURCE_WAIT:
            return "[PopenPlugin] popen.wait()"
        return f"[PopenPlugin] popen.?(source_id={interaction.source_id!r})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        if interaction.source_id == _SOURCE_SPAWN:
            return "    bigfoot.popen_mock.new_session().expect('spawn', returns=None)"
        if interaction.source_id == _SOURCE_COMMUNICATE:
            return (
                "    bigfoot.popen_mock.new_session()"
                ".expect('communicate', returns=(b'', b'', 0))"
            )
        if interaction.source_id == _SOURCE_WAIT:
            return "    bigfoot.popen_mock.new_session().expect('wait', returns=0)"
        return "    bigfoot.popen_mock.new_session().expect('?', returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":")[-1] if ":" in source_id else source_id
        return (
            f"subprocess.Popen.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.popen_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        pm = "bigfoot.popen_mock"
        sid = interaction.source_id
        if sid == _SOURCE_SPAWN:
            command = interaction.details.get("command", [])
            stdin = interaction.details.get("stdin")
            return f"    {pm}.assert_spawn(command={command!r}, stdin={stdin!r})"
        if sid == _SOURCE_COMMUNICATE:
            inp = interaction.details.get("input")
            return f"    {pm}.assert_communicate(input={inp!r})"
        if sid == _SOURCE_WAIT:
            return f"    {pm}.assert_wait()"
        return f"    # {pm}: unknown source_id={sid!r}"

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
        """Return assertable fields for each step type."""
        if interaction.source_id == _SOURCE_WAIT:
            return frozenset()
        return frozenset(interaction.details.keys())

    def assert_spawn(self, *, command: list[str], stdin: bytes | None) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._spawn_sentinel, command=command, stdin=stdin
        )

    def assert_communicate(self, *, input: bytes | None) -> None:  # noqa: A002
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._communicate_sentinel, input=input
        )

    def assert_wait(self) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._wait_sentinel)

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config
        method = getattr(step, "method", "?")
        return (
            f"subprocess.Popen.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
