"""AsyncSubprocessPlugin: intercepts asyncio.create_subprocess_exec/shell.

The async complement to PopenPlugin, which handles synchronous subprocess.Popen.
Both plugins target independent names in the asyncio/subprocess modules and do
not interfere with each other. Uses reference-counted class-level locks and
restores the original functions correctly when deactivated.
"""

import asyncio
import asyncio.subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, cast

from bigfoot._context import GuardPassThrough, _guard_allowlist, get_verifier_or_raise
from bigfoot._errors import ConflictError
from bigfoot._state_machine_plugin import StateMachinePlugin, _StepSentinel
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_SPAWN = "asyncio:subprocess:spawn"
_SOURCE_COMMUNICATE = "asyncio:subprocess:communicate"
_SOURCE_WAIT = "asyncio:subprocess:wait"

# ---------------------------------------------------------------------------
# Import-time constants -- captured BEFORE any patches are installed.
# ---------------------------------------------------------------------------

_ORIGINAL_CREATE_SUBPROCESS_EXEC: Callable[..., Any] = asyncio.create_subprocess_exec
_ORIGINAL_CREATE_SUBPROCESS_SHELL: Callable[..., Any] = asyncio.create_subprocess_shell

# ---------------------------------------------------------------------------
# Module-level references to our own interceptor functions.
# Set during _install_patches so _check_conflicts can distinguish bigfoot's
# interceptors from foreign patchers during nested sandbox activations.
# ---------------------------------------------------------------------------

_bigfoot_create_subprocess_exec: Callable[..., Any] | None = None
_bigfoot_create_subprocess_shell: Callable[..., Any] | None = None


# ---------------------------------------------------------------------------
# Module-level helper: find the AsyncSubprocessPlugin on the active verifier
# ---------------------------------------------------------------------------


def _find_async_subprocess_plugin() -> "AsyncSubprocessPlugin":
    verifier = get_verifier_or_raise(_SOURCE_SPAWN)
    for plugin in verifier._plugins:
        if isinstance(plugin, AsyncSubprocessPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot AsyncSubprocessPlugin interceptor is active but no "
        "AsyncSubprocessPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# _AsyncFakeProcess
# ---------------------------------------------------------------------------


class _AsyncFakeProcess:
    """Fake asyncio.subprocess.Process that routes all operations through AsyncSubprocessPlugin."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.pid: int = 12345  # fake PID
        self._plugin: AsyncSubprocessPlugin | None = None

    async def communicate(
        self,
        input: bytes | None = None,  # noqa: A002
    ) -> tuple[bytes, bytes]:
        assert self._plugin is not None
        plugin = self._plugin
        handle = plugin._lookup_session(self)
        result = plugin._execute_step(
            handle, "communicate", (), {}, _SOURCE_COMMUNICATE,
            details={"input": input},
        )
        # result is (stdout: bytes, stderr: bytes, returncode: int) 3-tuple
        out_bytes, err_bytes, returncode = result
        self.returncode = returncode
        return out_bytes, err_bytes

    async def wait(self) -> int:
        if self.returncode is not None:
            return self.returncode
        assert self._plugin is not None
        plugin = self._plugin
        handle = plugin._lookup_session(self)
        result = plugin._execute_step(
            handle, "wait", (), {}, _SOURCE_WAIT,
            details={},
        )
        # result is returncode int
        self.returncode = int(result)
        return self.returncode


# ---------------------------------------------------------------------------
# AsyncSubprocessPlugin
# ---------------------------------------------------------------------------


class AsyncSubprocessPlugin(StateMachinePlugin):
    """Async subprocess interception plugin.

    Replaces asyncio.create_subprocess_exec and asyncio.create_subprocess_shell
    with fake implementations at activate() time and restores the originals at
    deactivate() time. Uses reference counting so nested sandboxes work correctly.

    States: created -> running -> terminated
    """

    # Saved originals, restored when count reaches 0.
    _original_exec: ClassVar[Callable[..., Any] | None] = None
    _original_shell: ClassVar[Callable[..., Any] | None] = None

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
        return "asyncio:subprocess:spawn"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def install_patches(self) -> None:
        """Install asyncio.create_subprocess_exec/shell patches."""
        global _bigfoot_create_subprocess_exec, _bigfoot_create_subprocess_shell

        AsyncSubprocessPlugin._original_exec = asyncio.create_subprocess_exec
        AsyncSubprocessPlugin._original_shell = asyncio.create_subprocess_shell

        async def _fake_create_subprocess_exec(
            program: str,
            *args: Any,  # noqa: ANN401
            **kwargs: Any,  # noqa: ANN401
        ) -> _AsyncFakeProcess:
            # Check allowlist FIRST - bypasses both guard and sandbox
            if "async_subprocess" in _guard_allowlist.get():
                return cast(
                    _AsyncFakeProcess,
                    await _ORIGINAL_CREATE_SUBPROCESS_EXEC(program, *args, **kwargs),
                )
            try:
                plugin = _find_async_subprocess_plugin()
            except GuardPassThrough:
                return cast(
                    _AsyncFakeProcess,
                    await _ORIGINAL_CREATE_SUBPROCESS_EXEC(program, *args, **kwargs),
                )
            proc = _AsyncFakeProcess()
            proc._plugin = plugin
            plugin._bind_connection(proc)
            command = [program, *[str(a) for a in args]]
            stdin = kwargs.get("stdin")
            plugin._execute_step(
                plugin._lookup_session(proc), "spawn", (program, *args), kwargs,
                _SOURCE_SPAWN,
                details={
                    "command": command,
                    "stdin": stdin if isinstance(stdin, (bytes, type(None))) else None,
                },
            )
            return proc

        async def _fake_create_subprocess_shell(
            cmd: str,
            **kwargs: Any,  # noqa: ANN401
        ) -> _AsyncFakeProcess:
            # Check allowlist FIRST - bypasses both guard and sandbox
            if "async_subprocess" in _guard_allowlist.get():
                return cast(
                    _AsyncFakeProcess,
                    await _ORIGINAL_CREATE_SUBPROCESS_SHELL(cmd, **kwargs),
                )
            try:
                plugin = _find_async_subprocess_plugin()
            except GuardPassThrough:
                return cast(
                    _AsyncFakeProcess,
                    await _ORIGINAL_CREATE_SUBPROCESS_SHELL(cmd, **kwargs),
                )
            proc = _AsyncFakeProcess()
            proc._plugin = plugin
            plugin._bind_connection(proc)
            stdin = kwargs.get("stdin")
            plugin._execute_step(
                plugin._lookup_session(proc), "spawn", (cmd,), kwargs,
                _SOURCE_SPAWN,
                details={
                    "command": cmd,
                    "stdin": stdin if isinstance(stdin, (bytes, type(None))) else None,
                },
            )
            return proc

        _bigfoot_create_subprocess_exec = _fake_create_subprocess_exec
        _bigfoot_create_subprocess_shell = _fake_create_subprocess_shell

        setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
        setattr(asyncio, "create_subprocess_shell", _fake_create_subprocess_shell)

    def restore_patches(self) -> None:
        """Restore original asyncio.create_subprocess_exec/shell."""
        global _bigfoot_create_subprocess_exec, _bigfoot_create_subprocess_shell

        if AsyncSubprocessPlugin._original_exec is not None:
            asyncio.create_subprocess_exec = AsyncSubprocessPlugin._original_exec
            AsyncSubprocessPlugin._original_exec = None
        if AsyncSubprocessPlugin._original_shell is not None:
            asyncio.create_subprocess_shell = AsyncSubprocessPlugin._original_shell
            AsyncSubprocessPlugin._original_shell = None
        _bigfoot_create_subprocess_exec = None
        _bigfoot_create_subprocess_shell = None

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def check_conflicts(self) -> None:
        """Verify asyncio.create_subprocess_exec/shell have not been patched by a third party."""
        for target_name, current, original, bigfoot_ref in [
            (
                "asyncio.create_subprocess_exec",
                asyncio.create_subprocess_exec,
                _ORIGINAL_CREATE_SUBPROCESS_EXEC,
                _bigfoot_create_subprocess_exec,
            ),
            (
                "asyncio.create_subprocess_shell",
                asyncio.create_subprocess_shell,
                _ORIGINAL_CREATE_SUBPROCESS_SHELL,
                _bigfoot_create_subprocess_shell,
            ),
        ]:
            if current is not original and current is not bigfoot_ref:
                mod = getattr(current, "__module__", None) or ""
                qualname = getattr(current, "__qualname__", None) or ""
                if "unittest.mock" in mod or "MagicMock" in qualname:
                    patcher = "unittest.mock"
                elif "pytest_mock" in mod:
                    patcher = "pytest-mock"
                else:
                    patcher = "an unknown library"
                raise ConflictError(target=target_name, patcher=patcher)

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        if interaction.source_id == _SOURCE_SPAWN:
            command = interaction.details.get("command", [])
            return f"[AsyncSubprocessPlugin] spawn({command!r})"
        if interaction.source_id == _SOURCE_COMMUNICATE:
            inp = interaction.details.get("input")
            return f"[AsyncSubprocessPlugin] communicate(input={inp!r})"
        if interaction.source_id == _SOURCE_WAIT:
            return "[AsyncSubprocessPlugin] wait()"
        return f"[AsyncSubprocessPlugin] ?(source_id={interaction.source_id!r})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        if interaction.source_id == _SOURCE_SPAWN:
            return "    bigfoot.async_subprocess_mock.new_session().expect('spawn', returns=None)"
        if interaction.source_id == _SOURCE_COMMUNICATE:
            return (
                "    bigfoot.async_subprocess_mock.new_session()"
                ".expect('communicate', returns=(b'', b'', 0))"
            )
        if interaction.source_id == _SOURCE_WAIT:
            return "    bigfoot.async_subprocess_mock.new_session().expect('wait', returns=0)"
        return "    bigfoot.async_subprocess_mock.new_session().expect('?', returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":")[-1] if ":" in source_id else source_id
        return (
            f"asyncio.create_subprocess_{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.async_subprocess_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        pm = "bigfoot.async_subprocess_mock"
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

    def assert_spawn(self, *, command: list[str] | str, stdin: bytes | None) -> None:
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
            f"asyncio.create_subprocess.{method}(...) was mocked "
            f"(required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
