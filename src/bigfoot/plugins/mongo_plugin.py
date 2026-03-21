"""MongoPlugin: intercepts pymongo.collection.Collection methods with per-operation FIFO queues."""

from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import _get_verifier_or_raise, _guard_allowlist, _GuardPassThrough
from bigfoot._errors import UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import pymongo
    import pymongo.collection

    _PYMONGO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYMONGO_AVAILABLE = False


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


def _get_mongo_plugin() -> MongoPlugin | None:
    verifier = _get_verifier_or_raise("mongo:operation")
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
        # Check allowlist FIRST - bypasses both guard and sandbox
        if "mongo" in _guard_allowlist.get():
            original = MongoPlugin._original_methods
            if original is not None and operation in original:
                return original[operation](collection_self, *args, **kwargs)
        try:
            plugin = _get_mongo_plugin()
        except _GuardPassThrough:
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

    def _install_patches(self) -> None:
        """Install pymongo Collection method patches."""
        if not _PYMONGO_AVAILABLE:
            raise ImportError(
                "Install bigfoot[mongo] to use MongoPlugin: pip install bigfoot[mongo]"
            )
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

    def _restore_patches(self) -> None:
        """Restore original pymongo Collection methods."""
        if MongoPlugin._original_methods is not None:
            for method_name, original in MongoPlugin._original_methods.items():
                setattr(pymongo.collection.Collection, method_name, original)
            MongoPlugin._original_methods = None

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
        return f"    bigfoot.mongo_mock.mock_operation({operation!r}, returns=...)"

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
            f"    bigfoot.mongo_mock.mock_operation({op!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.mongo_mock"
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
        config: MongoMockConfig = mock_config  # type: ignore[assignment]
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
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

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
