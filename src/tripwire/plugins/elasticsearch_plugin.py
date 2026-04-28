"""ElasticsearchPlugin: intercepts Elasticsearch client methods with a per-operation FIFO queue."""

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
from tripwire._firewall_request import ElasticsearchFirewallRequest
from tripwire._normalize import normalize_host
from tripwire._timeline import Interaction

if TYPE_CHECKING:
    from tripwire._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import elasticsearch as es_lib

    _ELASTICSEARCH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ELASTICSEARCH_AVAILABLE = False

# Connection metadata: maps Elasticsearch client instance -> (host, port)
_es_conn_meta: WeakKeyDictionary[object, tuple[str, int]] = WeakKeyDictionary()

# Methods to intercept and their detail extraction specs.
# Each entry: (method_name, list of (kwarg_name, detail_key) pairs)
_INTERCEPTED_METHODS = (
    "index",
    "search",
    "get",
    "delete",
    "update",
    "bulk",
    "count",
    "mget",
    "msearch",
)

# Per-operation detail extraction: maps operation to the kwargs to capture.
_OPERATION_DETAILS: dict[str, tuple[str, ...]] = {
    "index": ("index", "document", "id"),
    "search": ("index", "query", "size", "from_"),
    "get": ("index", "id"),
    "delete": ("index", "id"),
    "update": ("index", "id", "doc"),
    "bulk": ("operations",),
    "count": ("index", "query"),
    "mget": ("index", "docs"),
    "msearch": ("searches",),
}


# ---------------------------------------------------------------------------
# ElasticsearchMockConfig
# ---------------------------------------------------------------------------


@dataclass
class ElasticsearchMockConfig:
    """Configuration for a single mocked Elasticsearch operation invocation."""

    operation: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _get_elasticsearch_plugin(
    firewall_request: ElasticsearchFirewallRequest | None = None,
) -> ElasticsearchPlugin | None:
    verifier = get_verifier_or_raise("elasticsearch:operation", firewall_request=firewall_request)
    for plugin in verifier._plugins:
        if isinstance(plugin, ElasticsearchPlugin):
            return plugin
    return None


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _ElasticsearchSentinel:
    """Opaque handle for an Elasticsearch operation."""

    def __init__(self, operation: str) -> None:
        self.source_id = f"elasticsearch:{operation}"


# ---------------------------------------------------------------------------
# Interceptor factory
# ---------------------------------------------------------------------------


def _make_interceptor(operation: str) -> Any:  # noqa: ANN401
    """Create an interceptor function for a specific ES operation."""
    detail_keys = _OPERATION_DETAILS.get(operation, ())

    def interceptor(es_self: object, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        host, port = _es_conn_meta.get(es_self, ("unknown", 0))
        index = kwargs.get("index", "")
        fw_request = ElasticsearchFirewallRequest(
            host=host, port=port,
            index=index if isinstance(index, str) else "",
            operation=operation,
        )
        try:
            plugin = _get_elasticsearch_plugin(firewall_request=fw_request)
        except GuardPassThrough:
            original = ElasticsearchPlugin._originals.get(operation)
            if original is not None:
                return original(es_self, *args, **kwargs)
            raise
        if plugin is None:
            original = ElasticsearchPlugin._originals.get(operation)
            if original is not None:
                return original(es_self, *args, **kwargs)
            return None
        source_id = f"elasticsearch:{operation}"

        with plugin._registry_lock:
            queue = plugin._queues.get(operation)
            if not queue:
                hint = plugin.format_unmocked_hint(source_id, (), kwargs)
                raise UnmockedInteractionError(
                    source_id=source_id,
                    args=(),
                    kwargs=kwargs,
                    hint=hint,
                )
            config = queue.popleft()

        # Extract relevant details from kwargs (only store fields actually provided)
        details: dict[str, Any] = {}
        for key in detail_keys:
            if key in kwargs:
                details[key] = kwargs[key]

        if config.raises is not None:
            details["raised"] = config.raises
        interaction = Interaction(
            source_id=source_id,
            sequence=0,
            details=details,
            plugin=plugin,
        )
        plugin.record(interaction)

        if config.raises is not None:
            raise config.raises
        return config.returns

    return interceptor


# ---------------------------------------------------------------------------
# ElasticsearchPlugin
# ---------------------------------------------------------------------------


class ElasticsearchPlugin(BasePlugin):
    """Elasticsearch interception plugin.

    Patches elasticsearch.Elasticsearch methods at the class level.
    Uses reference counting so nested sandboxes work correctly.
    """

    _originals: ClassVar[dict[str, Any]] = {}
    _original_init: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[ElasticsearchMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mock_operation(
        self,
        operation: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a single Elasticsearch operation invocation."""
        config = ElasticsearchMockConfig(
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
        """Install Elasticsearch method patches."""
        if not _ELASTICSEARCH_AVAILABLE:
            raise ImportError(
                "Install python-tripwire[elasticsearch] to use ElasticsearchPlugin: "
                "pip install python-tripwire[elasticsearch]"
            )
        es_cls = es_lib.Elasticsearch

        # Patch __init__ to capture connection metadata
        if ElasticsearchPlugin._original_init is None:
            ElasticsearchPlugin._original_init = es_cls.__init__

            def _patched_init(self_: object, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
                assert ElasticsearchPlugin._original_init is not None
                ElasticsearchPlugin._original_init(self_, *args, **kwargs)
                # Elasticsearch client accepts hosts as str, list of str, or list of dicts
                hosts = args[0] if args else kwargs.get("hosts", kwargs.get("host", "localhost"))
                host, port = "localhost", 9200
                if isinstance(hosts, str):
                    if ":" in hosts and not hosts.startswith("["):
                        parts = hosts.rsplit(":", 1)
                        host = parts[0]
                        try:
                            port = int(parts[1].rstrip("/"))
                        except ValueError:
                            pass
                    else:
                        host = hosts.rstrip("/")
                elif isinstance(hosts, (list, tuple)) and hosts:
                    first = hosts[0]
                    if isinstance(first, dict):
                        host = first.get("host", "localhost")
                        port = first.get("port", 9200)
                    elif isinstance(first, str):
                        if ":" in first and not first.startswith("["):
                            parts = first.rsplit(":", 1)
                            host = parts[0]
                            try:
                                port = int(parts[1].rstrip("/"))
                            except ValueError:
                                pass
                        else:
                            host = first.rstrip("/")
                # Strip scheme if present (e.g., "http://localhost")
                if "://" in str(host):
                    from urllib.parse import urlparse  # noqa: PLC0415
                    parsed = urlparse(str(host) if not str(host).endswith("/") else str(host))
                    host = parsed.hostname or "localhost"
                    if parsed.port:
                        port = parsed.port
                _es_conn_meta[self_] = (normalize_host(str(host)), int(port))

            es_cls.__init__ = _patched_init  # type: ignore[assignment,method-assign]

        for method_name in _INTERCEPTED_METHODS:
            ElasticsearchPlugin._originals[method_name] = getattr(es_cls, method_name)
            setattr(es_cls, method_name, _make_interceptor(method_name))

    def restore_patches(self) -> None:
        """Restore original Elasticsearch methods."""
        es_cls = es_lib.Elasticsearch
        for method_name, original in ElasticsearchPlugin._originals.items():
            setattr(es_cls, method_name, original)
        ElasticsearchPlugin._originals.clear()
        if ElasticsearchPlugin._original_init is not None:
            es_cls.__init__ = ElasticsearchPlugin._original_init  # type: ignore[method-assign]
            ElasticsearchPlugin._original_init = None

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

    # assertable_fields uses BasePlugin default: frozenset(interaction.details.keys())
    # Only fields actually provided in kwargs are stored in details.

    def get_unused_mocks(self) -> list[ElasticsearchMockConfig]:
        unused: list[ElasticsearchMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else "?"
        index = interaction.details.get("index")
        index_str = f"index={index!r}" if index else ""
        return f"[ElasticsearchPlugin] elasticsearch.{operation}({index_str})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else "?"
        return f"    tripwire.elasticsearch.mock_operation({operation!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        operation = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        return (
            f"elasticsearch.{operation}(...) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    tripwire.elasticsearch.mock_operation({operation!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else "?"
        details = interaction.details
        parts = [f"        {k}={v!r}," for k, v in details.items() if v is not None]
        lines = "\n".join(parts)
        return (
            f"    tripwire.elasticsearch.assert_{operation}(\n"
            f"{lines}\n"
            f"    )"
        )

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config = cast(ElasticsearchMockConfig, mock_config)
        operation = getattr(config, "operation", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"elasticsearch.{operation}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def assert_index(  # noqa: A002
        self, *, index: str, document: Any, id: str | None = None,  # noqa: ANN401
        **extra: Any,  # noqa: ANN401
    ) -> None:
        """Assert the next index interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _ElasticsearchSentinel("index")
        kwargs: dict[str, Any] = {"index": index, "document": document}
        if id is not None:
            kwargs["id"] = id
        kwargs.update(extra)
        _get_test_verifier_or_raise().assert_interaction(sentinel, **kwargs)

    def assert_search(
        self, *, index: str | None = None, query: Any = None,  # noqa: ANN401
        size: int | None = None, from_: int | None = None,
        **extra: Any,  # noqa: ANN401
    ) -> None:
        """Assert the next search interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _ElasticsearchSentinel("search")
        kwargs: dict[str, Any] = {}
        if index is not None:
            kwargs["index"] = index
        if query is not None:
            kwargs["query"] = query
        if size is not None:
            kwargs["size"] = size
        if from_ is not None:
            kwargs["from_"] = from_
        kwargs.update(extra)
        _get_test_verifier_or_raise().assert_interaction(sentinel, **kwargs)

    def assert_get(self, *, index: str, id: str, **extra: Any) -> None:  # noqa: A002, ANN401
        """Assert the next get interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _ElasticsearchSentinel("get")
        kwargs: dict[str, Any] = {"index": index, "id": id}
        kwargs.update(extra)
        _get_test_verifier_or_raise().assert_interaction(sentinel, **kwargs)

    def assert_delete(self, *, index: str, id: str, **extra: Any) -> None:  # noqa: A002, ANN401
        """Assert the next delete interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _ElasticsearchSentinel("delete")
        kwargs: dict[str, Any] = {"index": index, "id": id}
        kwargs.update(extra)
        _get_test_verifier_or_raise().assert_interaction(sentinel, **kwargs)

    def assert_bulk(self, *, operations: Any, **extra: Any) -> None:  # noqa: ANN401
        """Assert the next bulk interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _ElasticsearchSentinel("bulk")
        kwargs: dict[str, Any] = {"operations": operations}
        kwargs.update(extra)
        _get_test_verifier_or_raise().assert_interaction(sentinel, **kwargs)
