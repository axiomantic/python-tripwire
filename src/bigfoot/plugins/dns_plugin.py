"""DnsPlugin: intercepts socket.getaddrinfo/gethostbyname and dns.resolver.resolve."""

from __future__ import annotations

import socket
import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import _get_verifier_or_raise
from bigfoot._errors import UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard for dnspython
# ---------------------------------------------------------------------------

try:
    import dns.resolver

    _DNSPYTHON_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DNSPYTHON_AVAILABLE = False


# ---------------------------------------------------------------------------
# DnsMockConfig
# ---------------------------------------------------------------------------


@dataclass
class DnsMockConfig:
    """Configuration for a single mocked DNS operation.

    Attributes:
        operation: The DNS operation name (getaddrinfo, gethostbyname, resolve).
        hostname: The hostname being resolved.
        returns: The value to return when this mock is consumed.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the mock is reported as unused if never triggered.
        registration_traceback: Captured automatically at creation time.
    """

    operation: str
    hostname: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper: find the DnsPlugin on the active verifier
# ---------------------------------------------------------------------------


def _get_dns_plugin() -> DnsPlugin:
    verifier = _get_verifier_or_raise("dns:lookup")
    for plugin in verifier._plugins:
        if isinstance(plugin, DnsPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot DnsPlugin interceptor is active but no "
        "DnsPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _DnsSentinel:
    """Opaque handle for a DNS operation; used as source filter in assert_interaction."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


# ---------------------------------------------------------------------------
# Patched functions
# ---------------------------------------------------------------------------


def _patched_getaddrinfo(
    host: str,
    port: Any,  # noqa: ANN401
    family: int = 0,
    type: int = 0,  # noqa: A002
    proto: int = 0,
    flags: int = 0,
) -> Any:  # noqa: ANN401
    plugin = _get_dns_plugin()
    queue_key = f"getaddrinfo:{host}"
    with plugin._registry_lock:
        queue = plugin._queues.get(queue_key)
        if not queue:
            source_id = f"dns:getaddrinfo:{host}"
            hint = plugin.format_unmocked_hint(source_id, (host, port), {})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(host, port),
                kwargs={},
                hint=hint,
            )
        config = queue.popleft()

    interaction = Interaction(
        source_id=f"dns:getaddrinfo:{host}",
        sequence=0,
        details={
            "host": host,
            "port": port,
            "family": family,
            "type": type,
            "proto": proto,
        },
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


def _patched_gethostbyname(hostname: str) -> Any:  # noqa: ANN401
    plugin = _get_dns_plugin()
    queue_key = f"gethostbyname:{hostname}"
    with plugin._registry_lock:
        queue = plugin._queues.get(queue_key)
        if not queue:
            source_id = f"dns:gethostbyname:{hostname}"
            hint = plugin.format_unmocked_hint(source_id, (hostname,), {})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(hostname,),
                kwargs={},
                hint=hint,
            )
        config = queue.popleft()

    interaction = Interaction(
        source_id=f"dns:gethostbyname:{hostname}",
        sequence=0,
        details={"hostname": hostname},
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


def _patched_resolver_resolve(
    self: Any,  # noqa: ANN401
    qname: str,
    rdtype: str = "A",
    *args: Any,  # noqa: ANN401
    **kwargs: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    """Instance method: Resolver().resolve(qname, rdtype)."""
    plugin = _get_dns_plugin()
    actual_qname = str(qname)
    actual_rdtype = str(rdtype)

    queue_key = f"resolve:{actual_qname}"
    with plugin._registry_lock:
        queue = plugin._queues.get(queue_key)
        if not queue:
            source_id = f"dns:resolve:{actual_qname}"
            hint = plugin.format_unmocked_hint(source_id, (actual_qname, actual_rdtype), {})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(actual_qname, actual_rdtype),
                kwargs={},
                hint=hint,
            )
        config = queue.popleft()

    interaction = Interaction(
        source_id=f"dns:resolve:{actual_qname}",
        sequence=0,
        details={"qname": actual_qname, "rdtype": actual_rdtype},
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


def _patched_module_resolve(
    qname: str,
    rdtype: str = "A",
    *args: Any,  # noqa: ANN401
    **kwargs: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    """Module-level: dns.resolver.resolve(qname, rdtype)."""
    plugin = _get_dns_plugin()
    actual_qname = str(qname)
    actual_rdtype = str(rdtype)

    queue_key = f"resolve:{actual_qname}"
    with plugin._registry_lock:
        queue = plugin._queues.get(queue_key)
        if not queue:
            source_id = f"dns:resolve:{actual_qname}"
            hint = plugin.format_unmocked_hint(source_id, (actual_qname, actual_rdtype), {})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(actual_qname, actual_rdtype),
                kwargs={},
                hint=hint,
            )
        config = queue.popleft()

    interaction = Interaction(
        source_id=f"dns:resolve:{actual_qname}",
        sequence=0,
        details={"qname": actual_qname, "rdtype": actual_rdtype},
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


# ---------------------------------------------------------------------------
# DnsPlugin
# ---------------------------------------------------------------------------


class DnsPlugin(BasePlugin):
    """DNS interception plugin.

    Patches socket.getaddrinfo, socket.gethostbyname at the module level.
    When dnspython is available, also patches dns.resolver.resolve and
    dns.resolver.Resolver.resolve.

    Uses reference counting so nested sandboxes work correctly.
    """

    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()

    _original_getaddrinfo: ClassVar[Any] = None
    _original_gethostbyname: ClassVar[Any] = None
    _original_resolve: ClassVar[Any] = None
    _original_resolver_resolve: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[DnsMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API: register mocks
    # ------------------------------------------------------------------

    def mock_getaddrinfo(
        self,
        hostname: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for socket.getaddrinfo for the given hostname."""
        config = DnsMockConfig(
            operation="getaddrinfo",
            hostname=hostname,
            returns=returns,
            raises=raises,
            required=required,
        )
        queue_key = f"getaddrinfo:{hostname}"
        with self._registry_lock:
            if queue_key not in self._queues:
                self._queues[queue_key] = deque()
            self._queues[queue_key].append(config)

    def mock_gethostbyname(
        self,
        hostname: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for socket.gethostbyname for the given hostname."""
        config = DnsMockConfig(
            operation="gethostbyname",
            hostname=hostname,
            returns=returns,
            raises=raises,
            required=required,
        )
        queue_key = f"gethostbyname:{hostname}"
        with self._registry_lock:
            if queue_key not in self._queues:
                self._queues[queue_key] = deque()
            self._queues[queue_key].append(config)

    def mock_resolve(
        self,
        qname: str,
        rdtype: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for dns.resolver.resolve for the given qname/rdtype.

        Only available when dnspython is installed.
        """
        config = DnsMockConfig(
            operation="resolve",
            hostname=qname,
            returns=returns,
            raises=raises,
            required=required,
        )
        queue_key = f"resolve:{qname}"
        with self._registry_lock:
            if queue_key not in self._queues:
                self._queues[queue_key] = deque()
            self._queues[queue_key].append(config)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Reference-counted class-level patch installation."""
        with DnsPlugin._install_lock:
            if DnsPlugin._install_count == 0:
                DnsPlugin._original_getaddrinfo = socket.getaddrinfo
                DnsPlugin._original_gethostbyname = socket.gethostbyname
                socket.getaddrinfo = _patched_getaddrinfo  # type: ignore[assignment]
                socket.gethostbyname = _patched_gethostbyname

                if _DNSPYTHON_AVAILABLE:
                    DnsPlugin._original_resolve = dns.resolver.resolve
                    DnsPlugin._original_resolver_resolve = dns.resolver.Resolver.resolve
                    dns.resolver.resolve = _patched_module_resolve  # type: ignore[assignment]
                    dns.resolver.Resolver.resolve = _patched_resolver_resolve  # type: ignore[assignment, method-assign]

            DnsPlugin._install_count += 1

    def deactivate(self) -> None:
        with DnsPlugin._install_lock:
            DnsPlugin._install_count = max(0, DnsPlugin._install_count - 1)
            if DnsPlugin._install_count == 0:
                if DnsPlugin._original_getaddrinfo is not None:
                    socket.getaddrinfo = DnsPlugin._original_getaddrinfo
                    DnsPlugin._original_getaddrinfo = None
                if DnsPlugin._original_gethostbyname is not None:
                    socket.gethostbyname = DnsPlugin._original_gethostbyname
                    DnsPlugin._original_gethostbyname = None
                if DnsPlugin._original_resolve is not None and _DNSPYTHON_AVAILABLE:
                    dns.resolver.resolve = DnsPlugin._original_resolve
                    DnsPlugin._original_resolve = None
                if DnsPlugin._original_resolver_resolve is not None and _DNSPYTHON_AVAILABLE:
                    dns.resolver.Resolver.resolve = DnsPlugin._original_resolver_resolve  # type: ignore[method-assign]
                    DnsPlugin._original_resolver_resolve = None

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

    def get_unused_mocks(self) -> list[DnsMockConfig]:
        """Return all DnsMockConfig with required=True still in any queue."""
        unused: list[DnsMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        # source_id format: "dns:<operation>:<hostname>"
        parts = source_id.split(":", 2)
        operation = parts[1] if len(parts) > 1 else "?"
        hostname = parts[2] if len(parts) > 2 else "?"

        if operation == "getaddrinfo":
            port = interaction.details.get("port", "?")
            return f"[DnsPlugin] dns.getaddrinfo({hostname!r}, {port})"
        elif operation == "gethostbyname":
            return f"[DnsPlugin] dns.gethostbyname({hostname!r})"
        elif operation == "resolve":
            rdtype = interaction.details.get("rdtype", "?")
            return f"[DnsPlugin] dns.resolve({hostname!r}, {rdtype!r})"
        return f"[DnsPlugin] dns.{operation}({hostname!r})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        parts = source_id.split(":", 2)
        operation = parts[1] if len(parts) > 1 else "?"
        hostname = parts[2] if len(parts) > 2 else "?"

        if operation == "getaddrinfo":
            return f"    bigfoot.dns_mock.mock_getaddrinfo({hostname!r}, returns=...)"
        elif operation == "gethostbyname":
            return f"    bigfoot.dns_mock.mock_gethostbyname({hostname!r}, returns=...)"
        elif operation == "resolve":
            rdtype = interaction.details.get("rdtype", "A")
            return f"    bigfoot.dns_mock.mock_resolve({hostname!r}, {rdtype!r}, returns=...)"
        return f"    bigfoot.dns_mock.mock_{operation}({hostname!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        parts = source_id.split(":", 2)
        operation = parts[1] if len(parts) > 1 else "?"
        hostname = parts[2] if len(parts) > 2 else "?"

        if operation == "getaddrinfo":
            return (
                f"socket.getaddrinfo({hostname!r}, ...) was called but no mock was registered.\n"
                f"Register a mock with:\n"
                f"    bigfoot.dns_mock.mock_getaddrinfo({hostname!r}, returns=...)"
            )
        elif operation == "gethostbyname":
            return (
                f"socket.gethostbyname({hostname!r}) was called but no mock was registered.\n"
                f"Register a mock with:\n"
                f"    bigfoot.dns_mock.mock_gethostbyname({hostname!r}, returns=...)"
            )
        elif operation == "resolve":
            return (
                f"dns.resolver.resolve({hostname!r}, ...) was called but no mock was registered.\n"
                f"Register a mock with:\n"
                f"    bigfoot.dns_mock.mock_resolve({hostname!r}, 'A', returns=...)"
            )
        return (
            f"dns.{operation}({hostname!r}) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    bigfoot.dns_mock.mock_{operation}({hostname!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "bigfoot.dns_mock"
        source_id = interaction.source_id
        parts = source_id.split(":", 2)
        operation = parts[1] if len(parts) > 1 else "?"

        if operation == "getaddrinfo":
            host = interaction.details.get("host", "?")
            port = interaction.details.get("port", 0)
            family = interaction.details.get("family", 0)
            type_ = interaction.details.get("type", 0)
            proto = interaction.details.get("proto", 0)
            return (
                f"    {sm}.assert_getaddrinfo(\n"
                f"        host={host!r},\n"
                f"        port={port!r},\n"
                f"        family={family!r},\n"
                f"        type={type_!r},\n"
                f"        proto={proto!r},\n"
                f"    )"
            )
        elif operation == "gethostbyname":
            hostname = interaction.details.get("hostname", "?")
            return (
                f"    {sm}.assert_gethostbyname(\n"
                f"        hostname={hostname!r},\n"
                f"    )"
            )
        elif operation == "resolve":
            qname = interaction.details.get("qname", "?")
            rdtype = interaction.details.get("rdtype", "A")
            return (
                f"    {sm}.assert_resolve(\n"
                f"        qname={qname!r},\n"
                f"        rdtype={rdtype!r},\n"
                f"    )"
            )
        return f"    {sm}.assert_interaction(...)"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config: DnsMockConfig = mock_config  # type: ignore[assignment]
        operation = getattr(config, "operation", "?")
        hostname = getattr(config, "hostname", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"dns.{operation}({hostname!r}) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def assert_getaddrinfo(
        self,
        host: str,
        port: Any,  # noqa: ANN401
        family: int,
        type: int,  # noqa: A002
        proto: int,
    ) -> None:
        """Typed helper: assert the next getaddrinfo interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"dns:getaddrinfo:{host}"
        sentinel = _DnsSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            host=host,
            port=port,
            family=family,
            type=type,
            proto=proto,
        )

    def assert_gethostbyname(
        self,
        hostname: str,
    ) -> None:
        """Typed helper: assert the next gethostbyname interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"dns:gethostbyname:{hostname}"
        sentinel = _DnsSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            hostname=hostname,
        )

    def assert_resolve(
        self,
        qname: str,
        rdtype: str,
    ) -> None:
        """Typed helper: assert the next resolve interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"dns:resolve:{qname}"
        sentinel = _DnsSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            qname=qname,
            rdtype=rdtype,
        )
