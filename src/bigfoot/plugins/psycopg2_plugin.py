"""Psycopg2Plugin: intercepts psycopg2.connect() and returns _FakePsycopg2Connection."""

import threading
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._context import _get_verifier_or_raise, _guard_allowlist, _GuardPassThrough
from bigfoot._state_machine_plugin import SessionHandle, StateMachinePlugin, _StepSentinel
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import psycopg2  # type: ignore[import-untyped]

    _PSYCOPG2_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSYCOPG2_AVAILABLE = False

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_CONNECT = "psycopg2:connect"
_SOURCE_EXECUTE = "psycopg2:execute"
_SOURCE_COMMIT = "psycopg2:commit"
_SOURCE_ROLLBACK = "psycopg2:rollback"
_SOURCE_CLOSE = "psycopg2:close"


# ---------------------------------------------------------------------------
# Module-level helper: find the Psycopg2Plugin on the active verifier
# ---------------------------------------------------------------------------


def _get_psycopg2_plugin() -> "Psycopg2Plugin":
    verifier = _get_verifier_or_raise(_SOURCE_CONNECT)
    for plugin in verifier._plugins:
        if isinstance(plugin, Psycopg2Plugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot Psycopg2Plugin interceptor is active but no "
        "Psycopg2Plugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# _FakePsycopg2Cursor
# ---------------------------------------------------------------------------


class _FakePsycopg2Cursor:
    def __init__(self, rows: list[Any]) -> None:  # noqa: ANN401
        self._rows: list[Any] = rows if rows is not None else []  # noqa: ANN401
        self._pos: int = 0

    def fetchone(self) -> Any:  # noqa: ANN401
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list[Any]:  # noqa: ANN401
        rows = self._rows[self._pos :]
        self._pos = len(self._rows)
        return rows

    def fetchmany(self, size: int | None = None) -> list[Any]:  # noqa: ANN401
        if size is None:
            size = 1  # default arraysize
        rows = self._rows[self._pos : self._pos + size]
        self._pos += len(rows)
        return rows

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# _FakePsycopg2CursorProxy
# ---------------------------------------------------------------------------


class _FakePsycopg2CursorProxy:
    def __init__(self, connection: "_FakePsycopg2Connection") -> None:
        self._connection = connection

    def execute(self, sql: str, params: object = None) -> "_FakePsycopg2CursorProxy":
        handle = self._connection._plugin._lookup_session(self._connection)
        result = self._connection._plugin._execute_step(
            handle, "execute", (sql,), {"params": params}, _SOURCE_EXECUTE,
            details={"sql": sql, "parameters": params},
        )
        self._connection._last_cursor = _FakePsycopg2Cursor(result)
        return self

    def fetchone(self) -> Any:  # noqa: ANN401
        if self._connection._last_cursor is None:
            raise RuntimeError("no results to fetch")
        return self._connection._last_cursor.fetchone()

    def fetchall(self) -> list[Any]:  # noqa: ANN401
        if self._connection._last_cursor is None:
            return []
        return self._connection._last_cursor.fetchall()

    def fetchmany(self, size: int | None = None) -> list[Any]:  # noqa: ANN401
        if self._connection._last_cursor is None:
            return []
        return self._connection._last_cursor.fetchmany(size)

    def close(self) -> None:
        pass

    def __iter__(self) -> Any:  # noqa: ANN401
        if self._connection._last_cursor is None:
            return iter([])
        return iter(
            self._connection._last_cursor._rows[self._connection._last_cursor._pos :]
        )


# ---------------------------------------------------------------------------
# _FakePsycopg2Connection
# ---------------------------------------------------------------------------


class _FakePsycopg2Connection:
    def __init__(self, plugin: "Psycopg2Plugin") -> None:
        self._plugin = plugin
        self._handle: SessionHandle | None = None  # set by _bind_connection
        self._last_cursor: _FakePsycopg2Cursor | None = None

    def cursor(self) -> _FakePsycopg2CursorProxy:
        return _FakePsycopg2CursorProxy(self)

    def commit(self) -> None:
        handle = self._plugin._lookup_session(self)
        self._plugin._execute_step(handle, "commit", (), {}, _SOURCE_COMMIT, details={})

    def rollback(self) -> None:
        handle = self._plugin._lookup_session(self)
        self._plugin._execute_step(handle, "rollback", (), {}, _SOURCE_ROLLBACK, details={})

    def close(self) -> None:
        handle = self._plugin._lookup_session(self)
        self._plugin._execute_step(handle, "close", (), {}, _SOURCE_CLOSE, details={})
        self._plugin._release_session(self)


# ---------------------------------------------------------------------------
# Psycopg2Plugin
# ---------------------------------------------------------------------------


def _patched_psycopg2_connect(
    dsn: str = "", **kwargs: object
) -> _FakePsycopg2Connection:
    # Check allowlist FIRST - bypasses both guard and sandbox
    if "psycopg2" in _guard_allowlist.get():
        return Psycopg2Plugin._original_connect(dsn, **kwargs)  # type: ignore[no-any-return]
    try:
        plugin = _get_psycopg2_plugin()
    except _GuardPassThrough:
        return Psycopg2Plugin._original_connect(dsn, **kwargs)  # type: ignore[no-any-return]
    fake_conn = _FakePsycopg2Connection(plugin)
    plugin._bind_connection(fake_conn)
    handle = plugin._lookup_session(fake_conn)

    # Build connection details from dsn and/or kwargs
    details: dict[str, Any] = {}
    if dsn:
        details["dsn"] = dsn
    for key in ("host", "port", "dbname", "user"):
        if key in kwargs:
            details[key] = kwargs[key]
    # If only dsn was provided with no other details, always record dsn
    if not details:
        details["dsn"] = dsn

    plugin._execute_step(
        handle, "connect", (dsn,), kwargs, _SOURCE_CONNECT,
        details=details,
    )
    return fake_conn


class Psycopg2Plugin(StateMachinePlugin):
    """Psycopg2 interception plugin.

    Patches psycopg2.connect at module level.
    Uses reference counting so nested sandboxes work correctly.

    States: disconnected -> connected -> in_transaction -> connected/closed
    """

    # Class-level reference counting -- shared across all instances/verifiers.
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    # Saved original, restored when count reaches 0.
    _original_connect: ClassVar[Any] = None  # noqa: ANN401

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        self._connect_sentinel = _StepSentinel(_SOURCE_CONNECT)
        self._execute_sentinel = _StepSentinel(_SOURCE_EXECUTE)
        self._commit_sentinel = _StepSentinel(_SOURCE_COMMIT)
        self._rollback_sentinel = _StepSentinel(_SOURCE_ROLLBACK)
        self._close_sentinel = _StepSentinel(_SOURCE_CLOSE)

    @property
    def connect(self) -> _StepSentinel:
        return self._connect_sentinel

    @property
    def execute(self) -> _StepSentinel:
        return self._execute_sentinel

    @property
    def commit(self) -> _StepSentinel:
        return self._commit_sentinel

    @property
    def rollback(self) -> _StepSentinel:
        return self._rollback_sentinel

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
            "execute": {"connected": "in_transaction", "in_transaction": "in_transaction"},
            "commit": {"in_transaction": "connected"},
            "rollback": {"in_transaction": "connected"},
            "close": {"connected": "closed", "in_transaction": "closed"},
        }

    def _unmocked_source_id(self) -> str:
        return "psycopg2:connect"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted module-level patch installation."""
        with Psycopg2Plugin._install_lock:
            if Psycopg2Plugin._install_count == 0:
                self._install_patches()
            Psycopg2Plugin._install_count += 1

    def deactivate(self) -> None:
        with Psycopg2Plugin._install_lock:
            Psycopg2Plugin._install_count = max(0, Psycopg2Plugin._install_count - 1)
            if Psycopg2Plugin._install_count == 0:
                self._restore_patches()

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        if not _PSYCOPG2_AVAILABLE:  # pragma: no cover
            return
        Psycopg2Plugin._original_connect = psycopg2.connect
        psycopg2.connect = _patched_psycopg2_connect

    def _restore_patches(self) -> None:
        if not _PSYCOPG2_AVAILABLE:  # pragma: no cover
            return
        if Psycopg2Plugin._original_connect is not None:
            psycopg2.connect = Psycopg2Plugin._original_connect
            Psycopg2Plugin._original_connect = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        args = interaction.details.get("args", ())
        kwargs = interaction.details.get("kwargs", {})
        parts = [repr(a) for a in args]
        parts += [f"{k}={v!r}" for k, v in kwargs.items()]
        return f"[Psycopg2Plugin] psycopg2.{method}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"    bigfoot.psycopg2_mock.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        return (
            f"psycopg2.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.psycopg2_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.psycopg2_mock"
        sid = interaction.source_id
        if sid == _SOURCE_CONNECT:
            # Show whichever connect fields were recorded
            parts = []
            for key in ("dsn", "host", "port", "dbname", "user"):
                if key in interaction.details:
                    parts.append(f"{key}={interaction.details[key]!r}")
            return f"    {sm}.assert_connect({', '.join(parts)})"
        if sid == _SOURCE_EXECUTE:
            sql = interaction.details.get("sql", "?")
            params = interaction.details.get("parameters", None)
            return f"    {sm}.assert_execute(sql={sql!r}, parameters={params!r})"
        if sid == _SOURCE_COMMIT:
            return f"    {sm}.assert_commit()"
        if sid == _SOURCE_ROLLBACK:
            return f"    {sm}.assert_rollback()"
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
        """Return assertable fields for each step type."""
        if interaction.source_id == _SOURCE_CONNECT:
            return frozenset(interaction.details.keys())
        if interaction.source_id == _SOURCE_EXECUTE:
            return frozenset({"sql", "parameters"})
        if interaction.source_id in (_SOURCE_COMMIT, _SOURCE_ROLLBACK, _SOURCE_CLOSE):
            return frozenset()
        return frozenset(interaction.details.keys())

    def assert_connect(self, **kwargs: object) -> None:
        """Assert the next psycopg2 connect interaction.

        Pass whichever connection fields were used: dsn, host, port, dbname, user.
        """
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._connect_sentinel, **kwargs
        )

    def assert_execute(self, *, sql: str, parameters: object) -> None:
        """Assert the next psycopg2 execute interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._execute_sentinel, sql=sql, parameters=parameters
        )

    def assert_commit(self) -> None:
        """Assert the next psycopg2 commit interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._commit_sentinel)

    def assert_rollback(self) -> None:
        """Assert the next psycopg2 rollback interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._rollback_sentinel)

    def assert_close(self) -> None:
        """Assert the next psycopg2 close interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._close_sentinel)

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config  # noqa: ANN401
        method = getattr(step, "method", "?")
        return (
            f"psycopg2.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
