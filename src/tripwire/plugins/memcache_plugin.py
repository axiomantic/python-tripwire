"""MemcachePlugin: intercepts pymemcache Client methods.

Uses a per-command FIFO queue.
"""

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
from tripwire._firewall_request import MemcacheFirewallRequest
from tripwire._normalize import normalize_host
from tripwire._timeline import Interaction

if TYPE_CHECKING:
    from tripwire._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import pymemcache.client.base  # noqa: F401

    _PYMEMCACHE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYMEMCACHE_AVAILABLE = False

# Connection metadata: maps Client instance -> (host, port)
_memcache_conn_meta: WeakKeyDictionary[object, tuple[str, int]] = WeakKeyDictionary()


# ---------------------------------------------------------------------------
# MemcacheMockConfig
# ---------------------------------------------------------------------------


@dataclass
class MemcacheMockConfig:
    """Configuration for a single mocked memcache command invocation."""

    command: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _get_memcache_plugin(
    firewall_request: MemcacheFirewallRequest | None = None,
) -> MemcachePlugin | None:
    verifier = get_verifier_or_raise("memcache:command", firewall_request=firewall_request)
    for plugin in verifier._plugins:
        if isinstance(plugin, MemcachePlugin):
            return plugin
    return None


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _MemcacheSentinel:
    """Opaque handle for a memcache command; used as source filter in assert_interaction."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id


# ---------------------------------------------------------------------------
# Intercepted methods and their detail schemas
# ---------------------------------------------------------------------------

# Methods that take (key) only for details
_READ_METHODS = frozenset({"get", "gets", "delete"})
# Methods that take (key, value, expire=...) for details
_WRITE_METHODS = frozenset({"set", "add", "replace", "cas", "append", "prepend"})
# Methods that take (key, value) for details (incr/decr)
_COUNTER_METHODS = frozenset({"incr", "decr"})
# Multi-key methods
_MULTI_READ_METHODS = frozenset({"get_multi", "get_many", "gets_many"})
_MULTI_WRITE_METHODS = frozenset({"set_multi", "set_many"})
_MULTI_DELETE_METHODS = frozenset({"delete_multi", "delete_many"})

_ALL_INTERCEPTED = (
    _READ_METHODS
    | _WRITE_METHODS
    | _COUNTER_METHODS
    | _MULTI_READ_METHODS
    | _MULTI_WRITE_METHODS
    | _MULTI_DELETE_METHODS
)


# ---------------------------------------------------------------------------
# Patched method factory
# ---------------------------------------------------------------------------


def _make_patched_method(method_name: str) -> Any:  # noqa: ANN401
    """Create a patched method for the given memcache Client method."""
    cmd_upper = method_name.upper()

    def _patched(client_self: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        host, port = _memcache_conn_meta.get(client_self, ("unknown", 0))
        fw_request = MemcacheFirewallRequest(host=host, port=port, command=cmd_upper)
        try:
            plugin = _get_memcache_plugin(firewall_request=fw_request)
        except GuardPassThrough:
            original = MemcachePlugin._originals.get(method_name)
            if original is not None:
                return original(client_self, *args, **kwargs)
            raise
        if plugin is None:
            original = MemcachePlugin._originals.get(method_name)
            if original is not None:
                return original(client_self, *args, **kwargs)
            return None
        with plugin._registry_lock:
            queue = plugin._queues.get(cmd_upper)
            if not queue:
                source_id = f"memcache:{method_name}"
                hint = plugin.format_unmocked_hint(source_id, args, kwargs)
                raise UnmockedInteractionError(
                    source_id=source_id,
                    args=args,
                    kwargs=kwargs,
                    hint=hint,
                )
            config = queue.popleft()

        # Build details based on method type
        details: dict[str, Any] = {"command": cmd_upper}

        if method_name in _READ_METHODS:
            details["key"] = args[0] if args else kwargs.get("key", "")
        elif method_name in _WRITE_METHODS:
            details["key"] = args[0] if args else kwargs.get("key", "")
            details["value"] = args[1] if len(args) > 1 else kwargs.get("value")
            details["expire"] = args[2] if len(args) > 2 else kwargs.get("expire", 0)
        elif method_name in _COUNTER_METHODS:
            details["key"] = args[0] if args else kwargs.get("key", "")
            details["value"] = args[1] if len(args) > 1 else kwargs.get("value", 1)
        elif method_name in _MULTI_READ_METHODS | _MULTI_DELETE_METHODS:
            details["keys"] = args[0] if args else kwargs.get("keys", [])
        elif method_name in _MULTI_WRITE_METHODS:
            details["keys"] = list((args[0] if args else kwargs.get("mapping", {})).keys())
            details["expire"] = args[1] if len(args) > 1 else kwargs.get("expire", 0)

        if config.raises is not None:
            details["raised"] = config.raises
        interaction = Interaction(
            source_id=f"memcache:{method_name}",
            sequence=0,
            details=details,
            plugin=plugin,
        )
        plugin.record(interaction)

        if config.raises is not None:
            raise config.raises
        return config.returns

    _patched.__name__ = method_name
    return _patched


# ---------------------------------------------------------------------------
# MemcachePlugin
# ---------------------------------------------------------------------------


class MemcachePlugin(BasePlugin):
    """pymemcache interception plugin.

    Patches pymemcache.client.base.Client methods at the class level.
    Uses reference counting so nested sandboxes work correctly.
    """

    _originals: ClassVar[dict[str, Any]] = {m: None for m in _ALL_INTERCEPTED}
    _original_init: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[MemcacheMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mock_command(
        self,
        command: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a single memcache command invocation."""
        cmd_upper = command.upper()
        config = MemcacheMockConfig(
            command=cmd_upper,
            returns=returns,
            raises=raises,
            required=required,
        )
        with self._registry_lock:
            if cmd_upper not in self._queues:
                self._queues[cmd_upper] = deque()
            self._queues[cmd_upper].append(config)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def install_patches(self) -> None:
        """Install pymemcache Client method patches."""
        if not _PYMEMCACHE_AVAILABLE:
            raise ImportError(
                "Install python-tripwire[pymemcache] to use MemcachePlugin: "
                "pip install python-tripwire[pymemcache]"
            )
        from pymemcache.client.base import Client

        # Patch __init__ to capture connection metadata
        if MemcachePlugin._original_init is None:
            MemcachePlugin._original_init = Client.__init__

            def _patched_init(self_: object, server: Any, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
                assert MemcachePlugin._original_init is not None
                MemcachePlugin._original_init(self_, server, *args, **kwargs)
                if isinstance(server, tuple):
                    host, port = str(server[0]), int(server[1]) if len(server) > 1 else 11211
                else:
                    host, port = str(server), 11211
                _memcache_conn_meta[self_] = (normalize_host(host), port)

            Client.__init__ = _patched_init

        for method_name in _ALL_INTERCEPTED:
            MemcachePlugin._originals[method_name] = getattr(Client, method_name, None)
            setattr(Client, method_name, _make_patched_method(method_name))

    def restore_patches(self) -> None:
        """Restore original pymemcache Client methods."""
        from pymemcache.client.base import Client

        for method_name, original in MemcachePlugin._originals.items():
            if original is not None:
                setattr(Client, method_name, original)
        MemcachePlugin._originals = {k: None for k in MemcachePlugin._originals}
        if MemcachePlugin._original_init is not None:
            Client.__init__ = MemcachePlugin._original_init
            MemcachePlugin._original_init = None

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

    def get_unused_mocks(self) -> list[MemcacheMockConfig]:
        unused: list[MemcacheMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        command = interaction.details.get("command", "?")
        key = interaction.details.get("key", interaction.details.get("keys", "?"))
        return f"[MemcachePlugin] memcache.{command}({key!r})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        command = interaction.details.get("command", "?")
        return f"    tripwire.memcache.mock_command({command!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        cmd = source_id.split(":", 1)[-1].upper() if ":" in source_id else source_id.upper()
        return (
            f"memcache.{cmd}(...) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    tripwire.memcache.mock_command({cmd!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        sm = "tripwire.memcache"
        command = interaction.details.get("command", "?")
        # Determine which helper to suggest
        helper = f"assert_{command.lower()}"
        if command.lower() not in {"get", "set", "delete", "incr"}:
            # Fallback to generic assert_interaction
            parts = []
            for k, v in interaction.details.items():
                parts.append(f"        {k}={v!r},")
            body = "\n".join(parts)
            return f"    {sm}.assert_interaction(\n{body}\n    )"

        parts = []
        for k, v in interaction.details.items():
            parts.append(f"        {k}={v!r},")
        body = "\n".join(parts)
        return f"    {sm}.{helper}(\n{body}\n    )"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config = cast(MemcacheMockConfig, mock_config)
        command = getattr(config, "command", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"memcache.{command}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def assert_get(self, command: str, key: str) -> None:
        """Typed helper: assert the next memcache GET interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"memcache:{command.lower()}"
        sentinel = _MemcacheSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            command=command,
            key=key,
        )

    def assert_set(
        self,
        command: str,
        key: str,
        value: Any,  # noqa: ANN401
        expire: int = 0,
    ) -> None:
        """Typed helper: assert the next memcache SET/ADD/REPLACE interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"memcache:{command.lower()}"
        sentinel = _MemcacheSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            command=command,
            key=key,
            value=value,
            expire=expire,
        )

    def assert_delete(self, command: str, key: str) -> None:
        """Typed helper: assert the next memcache DELETE interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"memcache:{command.lower()}"
        sentinel = _MemcacheSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            command=command,
            key=key,
        )

    def assert_incr(self, command: str, key: str, value: int = 1) -> None:
        """Typed helper: assert the next memcache INCR/DECR interaction."""
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        source_id = f"memcache:{command.lower()}"
        sentinel = _MemcacheSentinel(source_id)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            command=command,
            key=key,
            value=value,
        )
