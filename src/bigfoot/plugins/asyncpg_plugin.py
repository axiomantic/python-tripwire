"""AsyncpgPlugin: intercepts asyncpg.connect() and returns _FakeAsyncpgConnection."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, cast

from bigfoot._context import GuardPassThrough, get_verifier_or_raise
from bigfoot._firewall_request import PostgresFirewallRequest
from bigfoot._state_machine_plugin import SessionHandle, StateMachinePlugin, _StepSentinel
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import asyncpg  # type: ignore[import-untyped]

    _ASYNCPG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ASYNCPG_AVAILABLE = False

# ---------------------------------------------------------------------------
# Source ID constants
# ---------------------------------------------------------------------------

_SOURCE_CONNECT = "asyncpg:connect"
_SOURCE_EXECUTE = "asyncpg:execute"
_SOURCE_FETCH = "asyncpg:fetch"
_SOURCE_FETCHROW = "asyncpg:fetchrow"
_SOURCE_FETCHVAL = "asyncpg:fetchval"
_SOURCE_CLOSE = "asyncpg:close"


# ---------------------------------------------------------------------------
# Module-level helper: find the AsyncpgPlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_asyncpg_plugin(
    firewall_request: PostgresFirewallRequest | None = None,
) -> "AsyncpgPlugin":
    verifier = get_verifier_or_raise(_SOURCE_CONNECT, firewall_request=firewall_request)
    for plugin in verifier._plugins:
        if isinstance(plugin, AsyncpgPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot AsyncpgPlugin interceptor is active but no "
        "AsyncpgPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# _FakeAsyncpgConnection
# ---------------------------------------------------------------------------


class _FakeAsyncpgConnection:
    """Fake asyncpg.Connection that routes all operations through a session script.

    asyncpg connections have methods directly on the connection (no cursor):
    execute(), fetch(), fetchrow(), fetchval(), close().
    """

    def __init__(self, plugin: "AsyncpgPlugin") -> None:
        self._plugin = plugin
        self._handle: SessionHandle | None = None  # set by _bind_connection

    async def execute(self, query: str, *args: Any) -> Any:  # noqa: ANN401
        handle = self._plugin._lookup_session(self)
        result = self._plugin._execute_step(
            handle, "execute", (query, *args), {}, _SOURCE_EXECUTE,
            details={"query": query, "args": list(args)},
        )
        return result

    async def fetch(self, query: str, *args: Any) -> list[Any]:  # noqa: ANN401
        handle = self._plugin._lookup_session(self)
        result = self._plugin._execute_step(
            handle, "fetch", (query, *args), {}, _SOURCE_FETCH,
            details={"query": query, "args": list(args)},
        )
        return cast(list[Any], result)

    async def fetchrow(self, query: str, *args: Any) -> Any:  # noqa: ANN401
        handle = self._plugin._lookup_session(self)
        result = self._plugin._execute_step(
            handle, "fetchrow", (query, *args), {}, _SOURCE_FETCHROW,
            details={"query": query, "args": list(args)},
        )
        return result

    async def fetchval(self, query: str, *args: Any) -> Any:  # noqa: ANN401
        handle = self._plugin._lookup_session(self)
        result = self._plugin._execute_step(
            handle, "fetchval", (query, *args), {}, _SOURCE_FETCHVAL,
            details={"query": query, "args": list(args)},
        )
        return result

    async def close(self) -> None:
        handle = self._plugin._lookup_session(self)
        self._plugin._execute_step(handle, "close", (), {}, _SOURCE_CLOSE, details={})
        self._plugin._release_session(self)


# ---------------------------------------------------------------------------
# AsyncpgPlugin
# ---------------------------------------------------------------------------


async def _patched_asyncpg_connect(
    dsn: str | None = None, **kwargs: object
) -> _FakeAsyncpgConnection:
    _original = AsyncpgPlugin._original_connect
    assert _original is not None
    # Parse connection parameters for firewall request
    host = str(kwargs.get("host", ""))
    port = int(kwargs.get("port", 0)) if "port" in kwargs else 0  # type: ignore[call-overload]
    dbname = str(kwargs.get("database", ""))
    fw_request = PostgresFirewallRequest(protocol="asyncpg", host=host, port=port, dbname=dbname)
    try:
        plugin = _get_asyncpg_plugin(firewall_request=fw_request)
    except GuardPassThrough:
        return cast(_FakeAsyncpgConnection, await _original(dsn, **kwargs))
    fake_conn = _FakeAsyncpgConnection(plugin)
    plugin._bind_connection(fake_conn)
    handle = plugin._lookup_session(fake_conn)

    # Build connection details from kwargs
    details: dict[str, Any] = {}
    if dsn is not None:
        details["dsn"] = dsn
    for key in ("host", "port", "database", "user"):
        if key in kwargs:
            details[key] = kwargs[key]
    # If nothing was captured, record dsn (even if None)
    if not details:
        details["dsn"] = dsn

    plugin._execute_step(
        handle, "connect", (), kwargs, _SOURCE_CONNECT,
        details=details,
    )
    return fake_conn


class AsyncpgPlugin(StateMachinePlugin):
    """asyncpg interception plugin.

    Patches asyncpg.connect at module level.
    Uses reference counting so nested sandboxes work correctly.

    States: disconnected -> connected -> closed
    """

    # Saved original, restored when count reaches 0.
    _original_connect: ClassVar[Callable[..., Any] | None] = None

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        self._connect_sentinel = _StepSentinel(_SOURCE_CONNECT)
        self._execute_sentinel = _StepSentinel(_SOURCE_EXECUTE)
        self._fetch_sentinel = _StepSentinel(_SOURCE_FETCH)
        self._fetchrow_sentinel = _StepSentinel(_SOURCE_FETCHROW)
        self._fetchval_sentinel = _StepSentinel(_SOURCE_FETCHVAL)
        self._close_sentinel = _StepSentinel(_SOURCE_CLOSE)

    @property
    def connect(self) -> _StepSentinel:
        return self._connect_sentinel

    @property
    def execute(self) -> _StepSentinel:
        return self._execute_sentinel

    @property
    def fetch(self) -> _StepSentinel:
        return self._fetch_sentinel

    @property
    def fetchrow(self) -> _StepSentinel:
        return self._fetchrow_sentinel

    @property
    def fetchval(self) -> _StepSentinel:
        return self._fetchval_sentinel

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
            "execute": {"connected": "connected"},
            "fetch": {"connected": "connected"},
            "fetchrow": {"connected": "connected"},
            "fetchval": {"connected": "connected"},
            "close": {"connected": "closed"},
        }

    def _unmocked_source_id(self) -> str:
        return "asyncpg:connect"

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Patch installation / restoration
    # ------------------------------------------------------------------

    def install_patches(self) -> None:
        if not _ASYNCPG_AVAILABLE:  # pragma: no cover
            return
        AsyncpgPlugin._original_connect = asyncpg.connect
        asyncpg.connect = _patched_asyncpg_connect

    def restore_patches(self) -> None:
        if not _ASYNCPG_AVAILABLE:  # pragma: no cover
            return
        if AsyncpgPlugin._original_connect is not None:
            asyncpg.connect = AsyncpgPlugin._original_connect
            AsyncpgPlugin._original_connect = None

    # ------------------------------------------------------------------
    # BasePlugin abstract method implementations
    # ------------------------------------------------------------------

    def format_interaction(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        args = interaction.details.get("args", ())
        kwargs = interaction.details.get("kwargs", {})
        parts = [repr(a) for a in args]
        parts += [f"{k}={v!r}" for k, v in kwargs.items()]
        return f"[AsyncpgPlugin] asyncpg.{method}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        method = interaction.details.get("method", "?")
        return f"    bigfoot.asyncpg_mock.new_session().expect({method!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        method = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        return (
            f"asyncpg.{method}(...) was called but no session was queued.\n"
            f"Register a session with:\n"
            f"    bigfoot.asyncpg_mock.new_session().expect({method!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.asyncpg_mock"
        sid = interaction.source_id
        if sid == _SOURCE_CONNECT:
            parts = []
            for key in ("dsn", "host", "port", "database", "user"):
                if key in interaction.details:
                    parts.append(f"{key}={interaction.details[key]!r}")
            return f"    {sm}.assert_connect({', '.join(parts)})"
        if sid == _SOURCE_EXECUTE:
            query = interaction.details.get("query", "?")
            args = interaction.details.get("args", [])
            return f"    {sm}.assert_execute(query={query!r}, args={args!r})"
        if sid == _SOURCE_FETCH:
            query = interaction.details.get("query", "?")
            args = interaction.details.get("args", [])
            return f"    {sm}.assert_fetch(query={query!r}, args={args!r})"
        if sid == _SOURCE_FETCHROW:
            query = interaction.details.get("query", "?")
            args = interaction.details.get("args", [])
            return f"    {sm}.assert_fetchrow(query={query!r}, args={args!r})"
        if sid == _SOURCE_FETCHVAL:
            query = interaction.details.get("query", "?")
            args = interaction.details.get("args", [])
            return f"    {sm}.assert_fetchval(query={query!r}, args={args!r})"
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
            return frozenset({"query", "args"})
        if interaction.source_id == _SOURCE_FETCH:
            return frozenset({"query", "args"})
        if interaction.source_id == _SOURCE_FETCHROW:
            return frozenset({"query", "args"})
        if interaction.source_id == _SOURCE_FETCHVAL:
            return frozenset({"query", "args"})
        if interaction.source_id == _SOURCE_CLOSE:
            return frozenset()
        return frozenset(interaction.details.keys())

    def assert_connect(self, **kwargs: object) -> None:
        """Assert the next asyncpg connect interaction.

        Pass whichever connection fields were used: dsn, host, port, database, user.
        """
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._connect_sentinel, **kwargs
        )

    def assert_execute(self, *, query: str, args: object) -> None:
        """Assert the next asyncpg execute interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._execute_sentinel, query=query, args=args
        )

    def assert_fetch(self, *, query: str, args: object) -> None:
        """Assert the next asyncpg fetch interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._fetch_sentinel, query=query, args=args
        )

    def assert_fetchrow(self, *, query: str, args: object) -> None:
        """Assert the next asyncpg fetchrow interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._fetchrow_sentinel, query=query, args=args
        )

    def assert_fetchval(self, *, query: str, args: object) -> None:
        """Assert the next asyncpg fetchval interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(
            self._fetchval_sentinel, query=query, args=args
        )

    def assert_close(self) -> None:
        """Assert the next asyncpg close interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415
        _get_test_verifier_or_raise().assert_interaction(self._close_sentinel)

    def format_unused_mock_hint(self, mock_config: object) -> str:
        step: Any = mock_config  # noqa: ANN401
        method = getattr(step, "method", "?")
        return (
            f"asyncpg.{method}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{getattr(step, 'registration_traceback', '')}"
        )
