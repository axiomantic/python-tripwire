"""MongoPlugin: intercepts pymongo.collection.Collection methods with per-operation FIFO queues."""

from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast
from weakref import WeakKeyDictionary

from tripwire._base_plugin import BasePlugin
from tripwire._context import GuardPassThrough, get_verifier_or_raise
from tripwire._errors import UnmockedInteractionError
from tripwire._firewall_request import MongoFirewallRequest
from tripwire._normalize import normalize_host
from tripwire._timeline import Interaction

if TYPE_CHECKING:
    from tripwire._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import pymongo
    import pymongo.collection

    _PYMONGO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYMONGO_AVAILABLE = False

# Connection metadata: maps MongoClient instance -> (host, port)
_mongo_conn_meta: WeakKeyDictionary[object, tuple[str, int]] = WeakKeyDictionary()


# ---------------------------------------------------------------------------
# MongoMockConfig
# ---------------------------------------------------------------------------


@dataclass
class MongoMockConfig:
    """Configuration for a single mocked MongoDB operation invocation.

    Attributes:
        operation: The MongoDB operation name (e.g., "find_one", "insert_one").
        returns: The value to return when this mock is consumed.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the mock is reported as unused if never triggered.
        registration_traceback: Captured automatically at creation time
            for use in error messages.
    """

    operation: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper: find the MongoPlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_mongo_plugin(
    firewall_request: MongoFirewallRequest | None = None,
) -> MongoPlugin | None:
    verifier = get_verifier_or_raise("mongo:operation", firewall_request=firewall_request)
    for plugin in verifier._plugins:
        if isinstance(plugin, MongoPlugin):
            return plugin
    return None


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _MongoSentinel:
    """Opaque handle for a MongoDB operation; used as source filter in assert_interaction."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


# ---------------------------------------------------------------------------
# Intercepted operations and their detail schemas
# ---------------------------------------------------------------------------

# Maps operation name -> list of extra detail field names beyond (database, collection, operation)
_OPERATION_FIELDS: dict[str, list[str]] = {
    "find": ["filter", "projection"],
    "find_one": ["filter", "projection"],
    "insert_one": ["document"],
    "insert_many": ["documents"],
    "update_one": ["filter", "update"],
    "update_many": ["filter", "update"],
    "delete_one": ["filter"],
    "delete_many": ["filter"],
    "aggregate": ["pipeline"],
    "count_documents": ["filter"],
}

# Maps operation name -> the name of the typed assertion helper
_ASSERT_HELPER_NAMES: dict[str, str] = {
    "find": "assert_find",
    "find_one": "assert_find_one",
    "insert_one": "assert_insert_one",
    "insert_many": "assert_insert_many",
    "update_one": "assert_update_one",
    "update_many": "assert_update_many",
    "delete_one": "assert_delete_one",
    "delete_many": "assert_delete_many",
    "aggregate": "assert_aggregate",
    "count_documents": "assert_count_documents",
}


def _extract_details(
    operation: str,
    collection_self: Any,  # noqa: ANN401
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Extract interaction detail fields from the call arguments.

    Each operation defines which positional args map to which detail fields.
    """
    database = collection_self.database.name
    collection = collection_self.name
    details: dict[str, Any] = {
        "database": database,
        "collection": collection,
        "operation": operation,
    }

    if operation in ("find", "find_one"):
        details["filter"] = args[0] if len(args) > 0 else kwargs.get("filter")
        details["projection"] = args[1] if len(args) > 1 else kwargs.get("projection")
    elif operation == "insert_one":
        details["document"] = args[0] if len(args) > 0 else kwargs.get("document")
    elif operation == "insert_many":
        details["documents"] = args[0] if len(args) > 0 else kwargs.get("documents")
    elif operation in ("update_one", "update_many"):
        details["filter"] = args[0] if len(args) > 0 else kwargs.get("filter")
        details["update"] = args[1] if len(args) > 1 else kwargs.get("update")
    elif operation in ("delete_one", "delete_many"):
        details["filter"] = args[0] if len(args) > 0 else kwargs.get("filter")
    elif operation == "aggregate":
        details["pipeline"] = args[0] if len(args) > 0 else kwargs.get("pipeline")
    elif operation == "count_documents":
        details["filter"] = args[0] if len(args) > 0 else kwargs.get("filter")

    return details


# ---------------------------------------------------------------------------
# Patched method factory
# ---------------------------------------------------------------------------


def _make_patched_method(operation: str) -> Any:  # noqa: ANN401
    """Create a patched method for a specific MongoDB collection operation."""

    def _patched(collection_self: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        # Navigate from Collection -> Database -> MongoClient for connection metadata
        client = getattr(getattr(collection_self, "database", None), "client", None)
        host, port = (
            _mongo_conn_meta.get(client, ("unknown", 0))
            if client is not None
            else ("unknown", 0)
        )
        database = (
            getattr(collection_self.database, "name", "")
            if hasattr(collection_self, "database")
            else ""
        )
        collection_name = getattr(collection_self, "name", "")
        fw_request = MongoFirewallRequest(
            host=host, port=port, database=database,
            collection=collection_name, operation=operation,
        )
        try:
            plugin = _get_mongo_plugin(firewall_request=fw_request)
        except GuardPassThrough:
            original = MongoPlugin._original_methods
            if original is not None and operation in original:
                return original[operation](collection_self, *args, **kwargs)
            raise
        if plugin is None:
            original = MongoPlugin._original_methods
            if original is not None and operation in original:
                return original[operation](collection_self, *args, **kwargs)
            return None
        source_id = f"mongo:{operation}"

        with plugin._registry_lock:
            queue = plugin._queues.get(operation)
            if not queue:
                hint = plugin.format_unmocked_hint(source_id, args, kwargs)
                raise UnmockedInteractionError(
                    source_id=source_id,
                    args=args,
                    kwargs=kwargs,
                    hint=hint,
                )
            config = queue.popleft()

        details = _extract_details(operation, collection_self, args, kwargs)
        if config.raises is not None:
            details["raised"] = config.raises

        interaction = Interaction(
            source_id=source_id,
            sequence=0,
            details=details,
            plugin=plugin,
        )
        plugin.record(interaction)
        # No mark_asserted() -- test authors must call assert_interaction() or typed helpers

        if config.raises is not None:
            raise config.raises
        return config.returns

    return _patched


# ---------------------------------------------------------------------------
# MongoPlugin
# ---------------------------------------------------------------------------


class MongoPlugin(BasePlugin):
    """MongoDB interception plugin.

    Patches pymongo.collection.Collection methods at the class level.
    Uses reference counting so nested sandboxes work correctly.

    Each operation name has its own FIFO deque of MongoMockConfig objects.
    """

    # Saved originals, restored when count reaches 0.
    _original_methods: ClassVar[dict[str, Any] | None] = None
    _original_client_init: ClassVar[Any] = None

    # Operations to intercept
    _INTERCEPTED_OPERATIONS: ClassVar[tuple[str, ...]] = (
        "find",
        "find_one",
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "delete_one",
        "delete_many",
        "aggregate",
        "count_documents",
    )

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[MongoMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API: register mock operations
    # ------------------------------------------------------------------

    def mock_operation(
        self,
        operation: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a single MongoDB operation invocation.

        Args:
            operation: The operation name (e.g., "find_one", "insert_one").
            returns: Value to return when this mock is consumed (required even
                when raises is set, as it serves as the fallback value type).
            raises: If provided, this exception is raised instead of returning.
            required: If False, the mock is not reported as unused at teardown.
        """
        config = MongoMockConfig(
            operation=operation,
            returns=returns,
            raises=raises,
            required=required,
        )
        with self._registry_lock:
            if operation not in self._queues:
                self._queues[operation] = deque()
            self._queues[operation].append(config)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def install_patches(self) -> None:
        """Install pymongo Collection method patches."""
        if not _PYMONGO_AVAILABLE:
            raise ImportError(
                "Install python-tripwire[mongo] to use MongoPlugin: "
                "pip install python-tripwire[mongo]"
            )

        # Patch MongoClient.__init__ to capture connection metadata
        if MongoPlugin._original_client_init is None:
            MongoPlugin._original_client_init = pymongo.MongoClient.__init__

            def _patched_client_init(self_: object, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
                assert MongoPlugin._original_client_init is not None
                MongoPlugin._original_client_init(self_, *args, **kwargs)
                host_arg = args[0] if args else kwargs.get("host", "localhost")
                port_arg = kwargs.get("port") or (args[1] if len(args) > 1 else 27017)
                host = "localhost"
                port = int(port_arg)
                if isinstance(host_arg, list):
                    # pymongo accepts a list of host strings; use the first.
                    host_arg = host_arg[0] if host_arg else "localhost"
                if isinstance(host_arg, str):
                    if host_arg.startswith(("mongodb://", "mongodb+srv://")):
                        from urllib.parse import urlparse  # noqa: PLC0415
                        parsed = urlparse(host_arg)
                        host = parsed.hostname or "localhost"
                        if parsed.port:
                            port = parsed.port
                    else:
                        host = host_arg
                _mongo_conn_meta[self_] = (normalize_host(host), port)

            pymongo.MongoClient.__init__ = _patched_client_init  # type: ignore[assignment,method-assign]

        MongoPlugin._original_methods = {}
        for op in MongoPlugin._INTERCEPTED_OPERATIONS:
            MongoPlugin._original_methods[op] = getattr(
                pymongo.collection.Collection, op
            )
            setattr(
                pymongo.collection.Collection,
                op,
                _make_patched_method(op),
            )

    def restore_patches(self) -> None:
        """Restore original pymongo Collection methods."""
        if MongoPlugin._original_methods is not None:
            for method_name, original in MongoPlugin._original_methods.items():
                setattr(pymongo.collection.Collection, method_name, original)
            MongoPlugin._original_methods = None
        if MongoPlugin._original_client_init is not None:
            pymongo.MongoClient.__init__ = MongoPlugin._original_client_init  # type: ignore[method-assign]
            MongoPlugin._original_client_init = None

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

    def get_unused_mocks(self) -> list[MongoMockConfig]:
        """Return all MongoMockConfig with required=True still in any queue."""
        unused: list[MongoMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        operation = interaction.details.get("operation", "?")
        database = interaction.details.get("database", "?")
        collection = interaction.details.get("collection", "?")

        # Build the argument display based on operation type
        op_fields = _OPERATION_FIELDS.get(operation, [])
        parts = []
        for f in op_fields:
            val = interaction.details.get(f)
            if val is not None:
                parts.append(f"{f}={val!r}")

        args_str = ", ".join(parts)
        return f"[MongoPlugin] {database}.{collection}.{operation}({args_str})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        operation = interaction.details.get("operation", "?")
        return f"    tripwire.mongo.mock_operation({operation!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        op = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        return (
            f"mongo.{op}(...) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    tripwire.mongo.mock_operation({op!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "tripwire.mongo"
        operation = interaction.details.get("operation", "?")
        helper_name = _ASSERT_HELPER_NAMES.get(operation, f"assert_{operation}")

        # Build keyword args excluding "operation" (implicit from helper name)
        # and "database"/"collection" come first
        lines = [f"    {sm}.{helper_name}(\n"]
        detail_keys = list(interaction.details.keys())
        for key in detail_keys:
            if key == "operation":
                continue
            val = interaction.details[key]
            lines.append(f"        {key}={val!r},\n")
        lines.append("    )")
        return "".join(lines)

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config = cast(MongoMockConfig, mock_config)
        operation = getattr(config, "operation", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"mongo.{operation}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def _assert_operation(
        self,
        operation: str,
        **expected_fields: Any,  # noqa: ANN401
    ) -> None:
        """Common implementation for typed assertion helpers."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"mongo:{operation}"
        sentinel = _MongoSentinel(source_id)
        all_fields = {"operation": operation, **expected_fields}
        _get_test_verifier_or_raise().assert_interaction(sentinel, **all_fields)

    def assert_find(
        self,
        database: str,
        collection: str,
        filter: Any,  # noqa: A002, ANN401
        projection: Any = None,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next find interaction."""
        self._assert_operation(
            "find",
            database=database,
            collection=collection,
            filter=filter,
            projection=projection,
        )

    def assert_find_one(
        self,
        database: str,
        collection: str,
        filter: Any,  # noqa: A002, ANN401
        projection: Any = None,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next find_one interaction."""
        self._assert_operation(
            "find_one",
            database=database,
            collection=collection,
            filter=filter,
            projection=projection,
        )

    def assert_insert_one(
        self,
        database: str,
        collection: str,
        document: Any,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next insert_one interaction."""
        self._assert_operation(
            "insert_one",
            database=database,
            collection=collection,
            document=document,
        )

    def assert_insert_many(
        self,
        database: str,
        collection: str,
        documents: Any,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next insert_many interaction."""
        self._assert_operation(
            "insert_many",
            database=database,
            collection=collection,
            documents=documents,
        )

    def assert_update_one(
        self,
        database: str,
        collection: str,
        filter: Any,  # noqa: A002, ANN401
        update: Any,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next update_one interaction."""
        self._assert_operation(
            "update_one",
            database=database,
            collection=collection,
            filter=filter,
            update=update,
        )

    def assert_update_many(
        self,
        database: str,
        collection: str,
        filter: Any,  # noqa: A002, ANN401
        update: Any,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next update_many interaction."""
        self._assert_operation(
            "update_many",
            database=database,
            collection=collection,
            filter=filter,
            update=update,
        )

    def assert_delete_one(
        self,
        database: str,
        collection: str,
        filter: Any,  # noqa: A002, ANN401
    ) -> None:
        """Typed helper: assert the next delete_one interaction."""
        self._assert_operation(
            "delete_one",
            database=database,
            collection=collection,
            filter=filter,
        )

    def assert_delete_many(
        self,
        database: str,
        collection: str,
        filter: Any,  # noqa: A002, ANN401
    ) -> None:
        """Typed helper: assert the next delete_many interaction."""
        self._assert_operation(
            "delete_many",
            database=database,
            collection=collection,
            filter=filter,
        )

    def assert_aggregate(
        self,
        database: str,
        collection: str,
        pipeline: Any,  # noqa: ANN401
    ) -> None:
        """Typed helper: assert the next aggregate interaction."""
        self._assert_operation(
            "aggregate",
            database=database,
            collection=collection,
            pipeline=pipeline,
        )

    def assert_count_documents(
        self,
        database: str,
        collection: str,
        filter: Any,  # noqa: A002, ANN401
    ) -> None:
        """Typed helper: assert the next count_documents interaction."""
        self._assert_operation(
            "count_documents",
            database=database,
            collection=collection,
            filter=filter,
        )
