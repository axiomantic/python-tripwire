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
from typing import Any, ClassVar

from bigfoot._context import _get_verifier_or_raise
from bigfoot._errors import ConflictError
from bigfoot._state_machine_plugin import StateMachinePlugin
from bigfoot._timeline import Interaction

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_INIT = "subprocess:popen:init"
_SOURCE_STDIN_WRITE = "subprocess:popen:stdin.write"
_SOURCE_STDOUT_READ = "subprocess:popen:stdout.read"
_SOURCE_STDERR_READ = "subprocess:popen:stderr.read"
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
    verifier = _get_verifier_or_raise(_SOURCE_INIT)
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
    """Fake file-like object delegating read/write to PopenPlugin._execute_step."""

    def __init__(
        self,
        plugin: "PopenPlugin",
        popen_instance: "_FakePopen",
        read_method: str | None,
        write_method: str | None,
    ) -> None:
        self._plugin = plugin
        self._popen = popen_instance
        self._read_method = read_method
        self._write_method = write_method

    def write(self, data: bytes) -> Any:  # noqa: ANN401
        if self._write_method is None:
            raise io_error("write")
        handle = self._plugin._lookup_session(self._popen)
        return self._plugin._execute_step(
            handle,
            self._write_method,
            (data,),
            {},
            f"subprocess:popen:{self._write_method}",
        )

    def read(self, size: int = -1) -> Any:  # noqa: ANN401
        if self._read_method is None:
            raise io_error("read")
        handle = self._plugin._lookup_session(self._popen)
        return self._plugin._execute_step(
            handle,
            self._read_method,
            (size,),
            {},
            f"subprocess:popen:{self._read_method}",
        )

    def readline(self) -> Any:  # noqa: ANN401
        if self._read_method is None:
            raise io_error("readline")
        handle = self._plugin._lookup_session(self._popen)
        return self._plugin._execute_step(
            handle,
            self._read_method,
            (),
            {},
            f"subprocess:popen:{self._read_method}.readline",
        )


def io_error(op: str) -> OSError:
    return OSError(f"_FakeStream does not support {op}")


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
        plugin._bind_connection(self)  # partial init
        plugin._execute_step(plugin._lookup_session(self), "init", (args,), {}, _SOURCE_INIT)
        self.stdin: _FakeStream | None = _FakeStream(plugin, self, None, "stdin.write")
        self.stdout: _FakeStream | None = _FakeStream(plugin, self, "stdout.read", None)
        self.stderr: _FakeStream | None = _FakeStream(plugin, self, "stderr.read", None)
        self.returncode: int | None = None
        self.pid: int = 12345  # fake PID

    def communicate(
        self,
        input: bytes | None = None,  # noqa: A002
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        plugin = _find_popen_plugin()
        handle = plugin._lookup_session(self)
        result = plugin._execute_step(handle, "communicate", (), {}, _SOURCE_COMMUNICATE)
        # result is (stdout: bytes, stderr: bytes, returncode: int) 3-tuple
        out_bytes, err_bytes, returncode = result
        self.returncode = returncode
        return out_bytes, err_bytes

    def wait(self, timeout: float | None = None) -> int:
        plugin = _find_popen_plugin()
        handle = plugin._lookup_session(self)
        result = plugin._execute_step(handle, "wait", (), {}, _SOURCE_WAIT)
        # result is returncode int
        self.returncode = int(result)
        plugin._release_session(self)
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

    # ------------------------------------------------------------------
    # StateMachinePlugin abstract methods
    # ------------------------------------------------------------------

    def _initial_state(self) -> str:
        return "created"

    def _transitions(self) -> dict[str, dict[str, str]]:
        return {
            "init": {"created": "running"},
            "stdin.write": {"running": "running"},
            "stdout.read": {"running": "running"},
            "stderr.read": {"running": "running"},
            "communicate": {"running": "terminated"},
            "wait": {"running": "terminated"},
        }

    def _unmocked_source_id(self) -> str:
        return "subprocess:popen:init"

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
        method = interaction.details.get("method", "?")
        args = interaction.details.get("args", ())
        parts = [repr(a) for a in args]
        return f"[PopenPlugin] popen.{method}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"    bigfoot.popen_mock.new_session().expect({method!r}, returns=...)"

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
        method = interaction.details.get("method", "?")
        return f"    # {pm}: session step '{method}' recorded (state-machine, auto-asserted)"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config
        method = getattr(step, "method", "?")
        return (
            f"subprocess.Popen.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
