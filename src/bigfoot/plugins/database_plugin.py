"""DatabasePlugin: intercepts sqlite3.connect() and returns _FakeConnection."""

import sqlite3
import threading
from typing import Any, ClassVar

from bigfoot._context import _get_verifier_or_raise
from bigfoot._state_machine_plugin import SessionHandle, StateMachinePlugin
from bigfoot._timeline import Interaction

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_CONNECT = "db:connect"
_SOURCE_EXECUTE = "db:execute"
_SOURCE_COMMIT = "db:commit"
_SOURCE_ROLLBACK = "db:rollback"
_SOURCE_CLOSE = "db:close"


# ---------------------------------------------------------------------------
# Module-level helper: find the DatabasePlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_database_plugin() -> "DatabasePlugin":
    verifier = _get_verifier_or_raise(_SOURCE_CONNECT)
    for plugin in verifier._plugins:
        if isinstance(plugin, DatabasePlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot DatabasePlugin interceptor is active but no "
        "DatabasePlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# _FakeCursor
# ---------------------------------------------------------------------------


class _FakeCursor:
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


# ---------------------------------------------------------------------------
# _FakeCursorProxy
# ---------------------------------------------------------------------------


class _FakeCursorProxy:
    def __init__(self, connection: "_FakeConnection") -> None:
        self._connection = connection

    def execute(self, sql: str, params: object = ()) -> "_FakeCursorProxy":
        handle = self._connection._plugin._lookup_session(self._connection)
        result = self._connection._plugin._execute_step(
            handle, "execute", (sql,), {"params": params}, _SOURCE_EXECUTE
        )
        self._connection._last_cursor = _FakeCursor(result)
        return self

    def fetchone(self) -> Any:  # noqa: ANN401
        if self._connection._last_cursor is None:
            raise sqlite3.InterfaceError("no results to fetch")
        return self._connection._last_cursor.fetchone()

    def fetchall(self) -> list[Any]:  # noqa: ANN401
        if self._connection._last_cursor is None:
            return []
        return self._connection._last_cursor.fetchall()

    def fetchmany(self, size: int | None = None) -> list[Any]:  # noqa: ANN401
        if self._connection._last_cursor is None:
            return []
        return self._connection._last_cursor.fetchmany(size)

    def __iter__(self) -> Any:  # noqa: ANN401
        if self._connection._last_cursor is None:
            return iter([])
        return iter(self._connection._last_cursor._rows[self._connection._last_cursor._pos :])


# ---------------------------------------------------------------------------
# _FakeConnection
# ---------------------------------------------------------------------------


class _FakeConnection:
    def __init__(self, plugin: "DatabasePlugin") -> None:
        self._plugin = plugin
        self._handle: SessionHandle | None = None  # set by _bind_connection
        self._last_cursor: _FakeCursor | None = None

    def execute(self, sql: str, params: object = ()) -> _FakeCursorProxy:
        handle = self._plugin._lookup_session(self)
        result = self._plugin._execute_step(
            handle, "execute", (sql,), {"params": params}, _SOURCE_EXECUTE
        )
        self._last_cursor = _FakeCursor(result)
        return _FakeCursorProxy(self)

    def cursor(self) -> _FakeCursorProxy:
        return _FakeCursorProxy(self)

    def commit(self) -> None:
        handle = self._plugin._lookup_session(self)
        self._plugin._execute_step(handle, "commit", (), {}, _SOURCE_COMMIT)

    def rollback(self) -> None:
        handle = self._plugin._lookup_session(self)
        self._plugin._execute_step(handle, "rollback", (), {}, _SOURCE_ROLLBACK)

    def close(self) -> None:
        handle = self._plugin._lookup_session(self)
        self._plugin._execute_step(handle, "close", (), {}, _SOURCE_CLOSE)
        self._plugin._release_session(self)


# ---------------------------------------------------------------------------
# DatabasePlugin
# ---------------------------------------------------------------------------


def _patched_connect(database: str, **_kwargs: object) -> _FakeConnection:
    plugin = _get_database_plugin()
    fake_conn = _FakeConnection(plugin)
    plugin._bind_connection(fake_conn)
    return fake_conn


class DatabasePlugin(StateMachinePlugin):
    """Database interception plugin.

    Patches sqlite3.connect at module level.
    Uses reference counting so nested sandboxes work correctly.

    States: connected -> in_transaction -> connected/closed
    """

    # Class-level reference counting -- shared across all instances/verifiers.
    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    # Saved original, restored when count reaches 0.
    _original_connect: ClassVar[Any] = None  # noqa: ANN401

    # ------------------------------------------------------------------
    # StateMachinePlugin abstract methods
    # ------------------------------------------------------------------

    def _initial_state(self) -> str:
        return "connected"

    def _transitions(self) -> dict[str, dict[str, str]]:
        return {
            "execute": {"connected": "in_transaction", "in_transaction": "in_transaction"},
            "commit": {"in_transaction": "connected"},
            "rollback": {"in_transaction": "connected"},
            "close": {"connected": "closed", "in_transaction": "closed"},
        }

    def _unmocked_source_id(self) -> str:
        return "db:connect"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted module-level patch installation."""
        with DatabasePlugin._install_lock:
            if DatabasePlugin._install_count == 0:
                self._install_patches()
            DatabasePlugin._install_count += 1

    def deactivate(self) -> None:
        with DatabasePlugin._install_lock:
            DatabasePlugin._install_count = max(0, DatabasePlugin._install_count - 1)
            if DatabasePlugin._install_count == 0:
                self._restore_patches()

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        DatabasePlugin._original_connect = sqlite3.connect
        sqlite3.connect = _patched_connect  # type: ignore[assignment]

    def _restore_patches(self) -> None:
        if DatabasePlugin._original_connect is not None:
            sqlite3.connect = DatabasePlugin._original_connect
            DatabasePlugin._original_connect = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        args = interaction.details.get("args", ())
        kwargs = interaction.details.get("kwargs", {})
        parts = [repr(a) for a in args]
        parts += [f"{k}={v!r}" for k, v in kwargs.items()]
        return f"[DatabasePlugin] sqlite3.{method}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"    bigfoot.db_mock.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        return (
            f"sqlite3.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.db_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.db_mock"
        method = interaction.details.get("method", "?")
        return f"    # {sm}: session step '{method}' recorded (state-machine, auto-asserted)"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config  # noqa: ANN401
        method = getattr(step, "method", "?")
        return (
            f"sqlite3.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
