"""SshPlugin: intercepts paramiko.SSHClient via class replacement."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._context import _get_verifier_or_raise, _guard_allowlist, _GuardPassThrough
from bigfoot._state_machine_plugin import StateMachinePlugin, _StepSentinel
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import paramiko as paramiko_lib

    _PARAMIKO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PARAMIKO_AVAILABLE = False

# ---------------------------------------------------------------------------
# Import-time constant -- captured BEFORE any patches are installed.
# ---------------------------------------------------------------------------

_ORIGINAL_SSH_CLIENT: Any = paramiko_lib.SSHClient if _PARAMIKO_AVAILABLE else None

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_CONNECT = "ssh:connect"
_SOURCE_EXEC_COMMAND = "ssh:exec_command"
_SOURCE_OPEN_SFTP = "ssh:open_sftp"
_SOURCE_SFTP_GET = "ssh:sftp_get"
_SOURCE_SFTP_PUT = "ssh:sftp_put"
_SOURCE_SFTP_LISTDIR = "ssh:sftp_listdir"
_SOURCE_SFTP_STAT = "ssh:sftp_stat"
_SOURCE_SFTP_MKDIR = "ssh:sftp_mkdir"
_SOURCE_SFTP_REMOVE = "ssh:sftp_remove"
_SOURCE_CLOSE = "ssh:close"

# ---------------------------------------------------------------------------
# Module-level helper: find the SshPlugin on the active verifier
# ---------------------------------------------------------------------------


def _find_ssh_plugin() -> SshPlugin:
    verifier = _get_verifier_or_raise("ssh:connect")
    for plugin in verifier._plugins:
        if isinstance(plugin, SshPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot SshPlugin interceptor is active but no "
        "SshPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# _FakeSFTPClient
# ---------------------------------------------------------------------------


class _FakeSFTPClient:
    """Fake paramiko SFTPClient that routes all operations through SshPlugin.

    In pass-through mode (``_real_sftp`` is set), ``__getattr__`` delegates
    all attribute access to the real SFTPClient. Since ``__getattr__`` is
    only invoked when normal lookup fails, the explicitly-defined mock
    methods below are found first in sandbox mode -- but in pass-through
    mode, ``__init__`` does not set up mock state so callers go straight
    through the real client.
    """

    def __init__(self, client: _FakeSSHClient, real_sftp: Any = None) -> None:  # noqa: ANN401
        self._client = client
        self._real_sftp = real_sftp

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        """Delegate to the real SFTPClient when in pass-through mode."""
        real = self.__dict__.get("_real_sftp")
        if real is not None:
            return getattr(real, name)
        raise AttributeError(f"'_FakeSFTPClient' object has no attribute {name!r}")

    def _step(
        self,
        method: str,
        source: str,
        details: dict[str, Any],
        real_args: tuple[Any, ...] = (),
        real_kwargs: dict[str, Any] | None = None,
    ) -> Any:  # noqa: ANN401
        """Shared implementation for all SFTP operations.

        In pass-through mode (``_real_sftp`` is set), delegates to the
        corresponding method on the real SFTPClient using *real_args* and
        *real_kwargs*.  In sandbox mode, routes through the SshPlugin state
        machine.
        """
        if self._real_sftp is not None:
            # method is e.g. "sftp_get" -- strip the "sftp_" prefix to get
            # the real paramiko SFTPClient method name.
            real_method = method.removeprefix("sftp_")
            return getattr(self._real_sftp, real_method)(*real_args, **(real_kwargs or {}))
        plugin = _find_ssh_plugin()
        handle = plugin._lookup_session(self._client)
        return plugin._execute_step(handle, method, (), {}, source, details=details)

    def get(self, remotepath: str, localpath: str, **kwargs: Any) -> Any:  # noqa: ANN401
        return self._step(
            "sftp_get", _SOURCE_SFTP_GET,
            {"remotepath": remotepath, "localpath": localpath},
            real_args=(remotepath, localpath), real_kwargs=kwargs,
        )

    def put(self, localpath: str, remotepath: str, **kwargs: Any) -> Any:  # noqa: ANN401
        return self._step(
            "sftp_put", _SOURCE_SFTP_PUT,
            {"localpath": localpath, "remotepath": remotepath},
            real_args=(localpath, remotepath), real_kwargs=kwargs,
        )

    def listdir(self, path: str = ".", **kwargs: Any) -> Any:  # noqa: ANN401
        return self._step(
            "sftp_listdir", _SOURCE_SFTP_LISTDIR,
            {"path": path},
            real_args=(path,), real_kwargs=kwargs,
        )

    def stat(self, path: str, **kwargs: Any) -> Any:  # noqa: ANN401
        return self._step(
            "sftp_stat", _SOURCE_SFTP_STAT,
            {"path": path},
            real_args=(path,), real_kwargs=kwargs,
        )

    def mkdir(self, path: str, mode: int = 0o777, **kwargs: Any) -> Any:  # noqa: ANN401
        return self._step(
            "sftp_mkdir", _SOURCE_SFTP_MKDIR,
            {"path": path},
            real_args=(path, mode), real_kwargs=kwargs,
        )

    def remove(self, path: str, **kwargs: Any) -> Any:  # noqa: ANN401
        return self._step(
            "sftp_remove", _SOURCE_SFTP_REMOVE,
            {"path": path},
            real_args=(path,), real_kwargs=kwargs,
        )


# ---------------------------------------------------------------------------
# _FakeSSHClient
# ---------------------------------------------------------------------------


class _FakeSSHClient:
    """Fake paramiko.SSHClient that routes all operations through SshPlugin."""

    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
        # SSHClient() does NOT connect on construction -- connect() is separate.
        self._real_client: Any = None

    def set_missing_host_key_policy(self, policy: Any) -> None:  # noqa: ANN401
        """No-op (or delegate to real client if in pass-through mode)."""
        if self._real_client is not None:
            self._real_client.set_missing_host_key_policy(policy)

    def connect(
        self,
        hostname: str,
        port: int = 22,
        username: str | None = None,
        password: str | None = None,
        pkey: Any = None,  # noqa: ANN401
        key_filename: Any = None,  # noqa: ANN401
        **kwargs: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        # Check allowlist FIRST - bypasses both guard and sandbox
        if "ssh" in _guard_allowlist.get():
            self._real_client = SshPlugin._original_ssh_client()
            return self._real_client.connect(
                hostname, port=port, username=username, password=password,
                pkey=pkey, key_filename=key_filename, **kwargs,
            )
        try:
            plugin = _find_ssh_plugin()
        except _GuardPassThrough:
            self._real_client = SshPlugin._original_ssh_client()
            return self._real_client.connect(
                hostname, port=port, username=username, password=password,
                pkey=pkey, key_filename=key_filename, **kwargs,
            )
        plugin._bind_connection(self)
        handle = plugin._lookup_session(self)

        # Determine auth_method from explicit parameters
        auth_method = "key" if (pkey is not None or key_filename is not None) else "password"

        return plugin._execute_step(
            handle, "connect", (hostname,), {"port": port}, _SOURCE_CONNECT,
            details={
                "hostname": hostname,
                "port": port,
                "username": username,
                "auth_method": auth_method,
            },
        )

    def exec_command(
        self,
        command: str,
        **kwargs: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        if self._real_client is not None:
            return self._real_client.exec_command(command, **kwargs)
        plugin = _find_ssh_plugin()
        handle = plugin._lookup_session(self)
        return plugin._execute_step(
            handle, "exec_command", (command,), {}, _SOURCE_EXEC_COMMAND,
            details={"command": command},
        )

    def open_sftp(self) -> _FakeSFTPClient:
        if self._real_client is not None:
            real_sftp = self._real_client.open_sftp()
            return _FakeSFTPClient(self, real_sftp=real_sftp)
        plugin = _find_ssh_plugin()
        handle = plugin._lookup_session(self)
        plugin._execute_step(
            handle, "open_sftp", (), {}, _SOURCE_OPEN_SFTP,
            details={},
        )
        return _FakeSFTPClient(self)

    def close(self, **kwargs: Any) -> Any:  # noqa: ANN401
        if self._real_client is not None:
            return self._real_client.close(**kwargs)
        plugin = _find_ssh_plugin()
        handle = plugin._lookup_session(self)
        result = plugin._execute_step(
            handle, "close", (), {}, _SOURCE_CLOSE,
            details={},
        )
        plugin._release_session(self)
        return result


# ---------------------------------------------------------------------------
# SshPlugin
# ---------------------------------------------------------------------------


class SshPlugin(StateMachinePlugin):
    """SSH (paramiko) interception plugin.

    Replaces paramiko.SSHClient with _FakeSSHClient at activate() time and
    restores the original at deactivate() time. Uses reference counting so
    nested sandboxes work correctly.

    States: disconnected -> connected -> closed
    exec_command, open_sftp, and sftp_* are self-transitions on connected.
    """

    # Class-level reference counting -- shared across all instances/verifiers.
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    # Saved original, restored when count reaches 0.
    _original_ssh_client: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._connect_sentinel = _StepSentinel(_SOURCE_CONNECT)
        self._exec_command_sentinel = _StepSentinel(_SOURCE_EXEC_COMMAND)
        self._open_sftp_sentinel = _StepSentinel(_SOURCE_OPEN_SFTP)
        self._sftp_get_sentinel = _StepSentinel(_SOURCE_SFTP_GET)
        self._sftp_put_sentinel = _StepSentinel(_SOURCE_SFTP_PUT)
        self._sftp_listdir_sentinel = _StepSentinel(_SOURCE_SFTP_LISTDIR)
        self._sftp_stat_sentinel = _StepSentinel(_SOURCE_SFTP_STAT)
        self._sftp_mkdir_sentinel = _StepSentinel(_SOURCE_SFTP_MKDIR)
        self._sftp_remove_sentinel = _StepSentinel(_SOURCE_SFTP_REMOVE)
        self._close_sentinel = _StepSentinel(_SOURCE_CLOSE)

    @property
    def connect(self) -> _StepSentinel:
        return self._connect_sentinel

    @property
    def exec_command(self) -> _StepSentinel:
        return self._exec_command_sentinel

    @property
    def open_sftp(self) -> _StepSentinel:
        return self._open_sftp_sentinel

    @property
    def sftp_get(self) -> _StepSentinel:
        return self._sftp_get_sentinel

    @property
    def sftp_put(self) -> _StepSentinel:
        return self._sftp_put_sentinel

    @property
    def sftp_listdir(self) -> _StepSentinel:
        return self._sftp_listdir_sentinel

    @property
    def sftp_stat(self) -> _StepSentinel:
        return self._sftp_stat_sentinel

    @property
    def sftp_mkdir(self) -> _StepSentinel:
        return self._sftp_mkdir_sentinel

    @property
    def sftp_remove(self) -> _StepSentinel:
        return self._sftp_remove_sentinel

    @property
    def close(self) -> _StepSentinel:
        return self._close_sentinel

    # ------------------------------------------------------------------
    # StateMachinePlugin abstract methods
    # ------------------------------------------------------------------

    def _initial_state(self) -> str:
        return "disconnected"

    def _transitions(self) -> dict[str, dict[str, str]]:
        return {
            "connect": {"disconnected": "connected"},
            "exec_command": {"connected": "connected"},
            "open_sftp": {"connected": "connected"},
            "sftp_get": {"connected": "connected"},
            "sftp_put": {"connected": "connected"},
            "sftp_listdir": {"connected": "connected"},
            "sftp_stat": {"connected": "connected"},
            "sftp_mkdir": {"connected": "connected"},
            "sftp_remove": {"connected": "connected"},
            "close": {"connected": "closed"},
        }

    def _unmocked_source_id(self) -> str:
        return "ssh:connect"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        if not _PARAMIKO_AVAILABLE:  # pragma: no cover
            return
        with SshPlugin._install_lock:
            if SshPlugin._install_count == 0:
                SshPlugin._original_ssh_client = paramiko_lib.SSHClient
                paramiko_lib.SSHClient = _FakeSSHClient
            SshPlugin._install_count += 1

    def deactivate(self) -> None:
        if not _PARAMIKO_AVAILABLE:  # pragma: no cover
            return
        with SshPlugin._install_lock:
            SshPlugin._install_count = max(0, SshPlugin._install_count - 1)
            if SshPlugin._install_count == 0:
                if SshPlugin._original_ssh_client is not None:
                    paramiko_lib.SSHClient = SshPlugin._original_ssh_client
                    SshPlugin._original_ssh_client = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        sid = interaction.source_id
        method = sid.split(":", 1)[-1] if ":" in sid else sid
        details = interaction.details
        if sid == _SOURCE_CONNECT:
            return (
                f"[SshPlugin] ssh.connect("
                f"hostname={details.get('hostname', '?')!r}, "
                f"port={details.get('port', 22)!r}, "
                f"username={details.get('username', '?')!r})"
            )
        if sid == _SOURCE_EXEC_COMMAND:
            return f"[SshPlugin] ssh.exec_command(command={details.get('command', '?')!r})"
        if sid == _SOURCE_OPEN_SFTP:
            return "[SshPlugin] ssh.open_sftp()"
        if sid == _SOURCE_SFTP_GET:
            return (
                f"[SshPlugin] sftp.get("
                f"remotepath={details.get('remotepath', '?')!r}, "
                f"localpath={details.get('localpath', '?')!r})"
            )
        if sid == _SOURCE_SFTP_PUT:
            return (
                f"[SshPlugin] sftp.put("
                f"localpath={details.get('localpath', '?')!r}, "
                f"remotepath={details.get('remotepath', '?')!r})"
            )
        if sid == _SOURCE_SFTP_LISTDIR:
            return f"[SshPlugin] sftp.listdir(path={details.get('path', '?')!r})"
        if sid == _SOURCE_SFTP_STAT:
            return f"[SshPlugin] sftp.stat(path={details.get('path', '?')!r})"
        if sid == _SOURCE_SFTP_MKDIR:
            return f"[SshPlugin] sftp.mkdir(path={details.get('path', '?')!r})"
        if sid == _SOURCE_SFTP_REMOVE:
            return f"[SshPlugin] sftp.remove(path={details.get('path', '?')!r})"
        if sid == _SOURCE_CLOSE:
            return "[SshPlugin] ssh.close()"
        return f"[SshPlugin] ssh.{method}(...)"

    def format_mock_hint(self, interaction: Interaction) -> str:
        sid = interaction.source_id
        method = sid.split(":")[-1] if ":" in sid else sid
        return f"    bigfoot.ssh_mock.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":")[-1] if ":" in source_id else source_id
        return (
            f"paramiko.SSHClient.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.ssh_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.ssh_mock"
        sid = interaction.source_id
        if sid == _SOURCE_CONNECT:
            hostname = interaction.details.get("hostname", "?")
            port = interaction.details.get("port", 22)
            username = interaction.details.get("username", "?")
            auth_method = interaction.details.get("auth_method", "?")
            return (
                f"    {sm}.assert_connect("
                f"hostname={hostname!r}, port={port!r}, "
                f"username={username!r}, auth_method={auth_method!r})"
            )
        if sid == _SOURCE_EXEC_COMMAND:
            command = interaction.details.get("command", "?")
            return f"    {sm}.assert_exec_command(command={command!r})"
        if sid == _SOURCE_OPEN_SFTP:
            return f"    {sm}.assert_open_sftp()"
        if sid == _SOURCE_SFTP_GET:
            remotepath = interaction.details.get("remotepath", "?")
            localpath = interaction.details.get("localpath", "?")
            return (
                f"    {sm}.assert_sftp_get("
                f"remotepath={remotepath!r}, localpath={localpath!r})"
            )
        if sid == _SOURCE_SFTP_PUT:
            localpath = interaction.details.get("localpath", "?")
            remotepath = interaction.details.get("remotepath", "?")
            return (
                f"    {sm}.assert_sftp_put("
                f"localpath={localpath!r}, remotepath={remotepath!r})"
            )
        if sid == _SOURCE_SFTP_LISTDIR:
            path = interaction.details.get("path", "?")
            return f"    {sm}.assert_sftp_listdir(path={path!r})"
        if sid == _SOURCE_SFTP_STAT:
            path = interaction.details.get("path", "?")
            return f"    {sm}.assert_sftp_stat(path={path!r})"
        if sid == _SOURCE_SFTP_MKDIR:
            path = interaction.details.get("path", "?")
            return f"    {sm}.assert_sftp_mkdir(path={path!r})"
        if sid == _SOURCE_SFTP_REMOVE:
            path = interaction.details.get("path", "?")
            return f"    {sm}.assert_sftp_remove(path={path!r})"
        if sid == _SOURCE_CLOSE:
            return f"    {sm}.assert_close()"
        return f"    # {sm}: unknown source_id={sid!r}"

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
        """Return all detail keys as assertable fields.

        Steps with no data fields (open_sftp, close) record empty details,
        so this naturally returns frozenset() for those steps.
        """
        return frozenset(interaction.details.keys())

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def assert_connect(
        self, *, hostname: str, port: int, username: str | None, auth_method: str,
    ) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._connect_sentinel,
            hostname=hostname, port=port, username=username, auth_method=auth_method,
        )

    def assert_exec_command(self, *, command: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._exec_command_sentinel, command=command
        )

    def assert_open_sftp(self) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._open_sftp_sentinel)

    def assert_sftp_get(self, *, remotepath: str, localpath: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._sftp_get_sentinel, remotepath=remotepath, localpath=localpath
        )

    def assert_sftp_put(self, *, localpath: str, remotepath: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._sftp_put_sentinel, localpath=localpath, remotepath=remotepath
        )

    def assert_sftp_listdir(self, *, path: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._sftp_listdir_sentinel, path=path
        )

    def assert_sftp_stat(self, *, path: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._sftp_stat_sentinel, path=path
        )

    def assert_sftp_mkdir(self, *, path: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._sftp_mkdir_sentinel, path=path
        )

    def assert_sftp_remove(self, *, path: str) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._sftp_remove_sentinel, path=path
        )

    def assert_close(self) -> None:
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._close_sentinel)

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config
        method = getattr(step, "method", "?")
        return (
            f"paramiko.SSHClient.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
