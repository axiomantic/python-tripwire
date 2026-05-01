"""Boto3Plugin: intercepts botocore BaseClient._make_api_call.

Uses a per-service:operation FIFO queue.
"""

from __future__ import annotations

import os
import threading
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

from tripwire._base_plugin import BasePlugin
from tripwire._context import GuardPassThrough, get_verifier_or_raise
from tripwire._errors import UnmockedInteractionError
from tripwire._firewall_request import Boto3FirewallRequest
from tripwire._timeline import Interaction

if TYPE_CHECKING:
    from tripwire._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import botocore.client  # noqa: F401

    _BOTO3_AVAILABLE = True
except ImportError:  # pragma: no cover
    _BOTO3_AVAILABLE = False


# ---------------------------------------------------------------------------
# Boto3MockConfig
# ---------------------------------------------------------------------------


@dataclass
class Boto3MockConfig:
    """Configuration for a single mocked boto3 API call invocation.

    Attributes:
        service: The AWS service name (e.g., "s3", "sqs", "dynamodb").
        operation: The API operation name in PascalCase (e.g., "GetObject").
        returns: The value to return when this mock is consumed.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the mock is reported as unused if never triggered.
        registration_traceback: Captured automatically at creation time.
    """

    service: str
    operation: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper: find the Boto3Plugin on the active verifier
# ---------------------------------------------------------------------------


def _get_boto3_plugin(
    firewall_request: Boto3FirewallRequest | None = None,
) -> Boto3Plugin | None:
    verifier = get_verifier_or_raise("boto3:_make_api_call", firewall_request=firewall_request)
    for plugin in verifier._plugins:
        if isinstance(plugin, Boto3Plugin):
            return plugin
    return None


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _Boto3Sentinel:
    """Opaque handle for a boto3 service:operation; used as source filter in assert_interaction."""

    def __init__(self, service: str, operation: str) -> None:
        self.source_id = f"boto3:{service}:{operation}"


# ---------------------------------------------------------------------------
# _ServiceProxy: dynamic sentinel access (plugin.s3.GetObject)
# ---------------------------------------------------------------------------


class _ServiceProxy:
    """Intermediate proxy for ``plugin.<service>.<operation>`` access."""

    def __init__(self, service_name: str, plugin: Boto3Plugin) -> None:
        self._service_name = service_name
        self._plugin = plugin

    def __getattr__(self, operation: str) -> _Boto3Sentinel:
        return _Boto3Sentinel(self._service_name, operation)


# ---------------------------------------------------------------------------
# Patched _make_api_call
# ---------------------------------------------------------------------------


def _patched_make_api_call(
    client_self: object, operation_name: str, api_params: dict[str, Any],
) -> Any:  # noqa: ANN401
    _original = Boto3Plugin._original_make_api_call
    assert _original is not None
    meta = getattr(client_self, "meta", None)
    service_model = getattr(meta, "service_model", None) if meta else None
    service_name_fw: str = (
        getattr(service_model, "service_name", "unknown") if service_model else "unknown"
    )
    fw_request = Boto3FirewallRequest(service=service_name_fw, operation=operation_name)
    try:
        plugin = _get_boto3_plugin(firewall_request=fw_request)
    except GuardPassThrough:
        return _original(client_self, operation_name, api_params)
    if plugin is None:
        return _original(client_self, operation_name, api_params)
    service_name = service_name_fw
    queue_key = f"{service_name}:{operation_name}"
    source_id = f"boto3:{queue_key}"

    with plugin._registry_lock:
        queue = plugin._queues.get(queue_key)
        if not queue:
            hint = plugin.format_unmocked_hint(source_id, (), api_params)
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(),
                kwargs=api_params,
                hint=hint,
            )
        config = queue.popleft()

    details: dict[str, Any] = {
        "service": service_name, "operation": operation_name, "params": api_params,
    }
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


# ---------------------------------------------------------------------------
# Boto3Plugin
# ---------------------------------------------------------------------------


class Boto3Plugin(BasePlugin):
    """boto3/botocore interception plugin.

    Patches botocore.client.BaseClient._make_api_call at the class level.
    Uses reference counting so nested sandboxes work correctly.

    Each service:operation pair has its own FIFO deque of Boto3MockConfig objects.
    """

    _original_make_api_call: ClassVar[Callable[..., Any] | None] = None
    _saved_env: ClassVar[dict[str, str | None]] = {}

    # Env vars that must be set to prevent botocore's credential provider
    # from hitting the EC2 metadata service (169.254.169.254).
    _CREDENTIAL_ENV_VARS: ClassVar[dict[str, str]] = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[Boto3MockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Dynamic sentinel access: plugin.s3 -> _ServiceProxy("s3", self)
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> _ServiceProxy:
        # Only create service proxies for names that don't start with _
        if name.startswith("_"):
            raise AttributeError(name)
        return _ServiceProxy(name, self)

    # ------------------------------------------------------------------
    # Public API: register mock calls
    # ------------------------------------------------------------------

    def mock_call(
        self,
        service: str,
        operation: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for a single boto3 API call invocation.

        Args:
            service: The AWS service name (e.g., "s3").
            operation: The API operation name in PascalCase (e.g., "GetObject").
            returns: Value to return when this mock is consumed.
            raises: If provided, this exception is raised instead of returning.
            required: If False, the mock is not reported as unused at teardown.
        """
        config = Boto3MockConfig(
            service=service,
            operation=operation,
            returns=returns,
            raises=raises,
            required=required,
        )
        queue_key = f"{service}:{operation}"
        with self._registry_lock:
            if queue_key not in self._queues:
                self._queues[queue_key] = deque()
            self._queues[queue_key].append(config)

    # ------------------------------------------------------------------
    # Sentinel factory (for assertion helpers)
    # ------------------------------------------------------------------

    def sentinel(self, service: str, operation: str) -> _Boto3Sentinel:
        """Return a sentinel for a specific service:operation pair."""
        return _Boto3Sentinel(service, operation)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def install_patches(self) -> None:
        """Install botocore._make_api_call patch and set dummy AWS credentials.

        Setting dummy credentials prevents botocore's credential provider from
        hitting the EC2 metadata service (169.254.169.254), which would leak DNS
        and HTTP calls to other plugin interceptors.
        """
        if not _BOTO3_AVAILABLE:
            raise ImportError(
                "Install pytest-tripwire[boto3] to use Boto3Plugin: "
                "pip install pytest-tripwire[boto3]"
            )
        # Save current env values and inject dummy credentials
        for key, value in self._CREDENTIAL_ENV_VARS.items():
            Boto3Plugin._saved_env[key] = os.environ.get(key)
            os.environ[key] = value

        Boto3Plugin._original_make_api_call = botocore.client.BaseClient._make_api_call
        botocore.client.BaseClient._make_api_call = _patched_make_api_call

    def restore_patches(self) -> None:
        """Restore original botocore._make_api_call and AWS credential env vars."""
        if Boto3Plugin._original_make_api_call is not None:
            botocore.client.BaseClient._make_api_call = Boto3Plugin._original_make_api_call
            Boto3Plugin._original_make_api_call = None

        # Restore original env values
        for key, original_value in Boto3Plugin._saved_env.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value
        Boto3Plugin._saved_env.clear()

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

    def get_unused_mocks(self) -> list[Boto3MockConfig]:
        unused: list[Boto3MockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        service = interaction.details.get("service", "?")
        operation = interaction.details.get("operation", "?")
        params = interaction.details.get("params", {})
        parts = [f"{k}={v!r}" for k, v in params.items()]
        return f"[Boto3Plugin] {service}.{operation}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        service = interaction.details.get("service", "?")
        operation = interaction.details.get("operation", "?")
        return f"    tripwire.boto3.mock_call({service!r}, {operation!r}, returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        parts = source_id.split(":", 2)
        service = parts[1] if len(parts) > 1 else "?"
        operation = parts[2] if len(parts) > 2 else "?"
        return (
            f"{service}.{operation}(...) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    tripwire.boto3.mock_call({service!r}, {operation!r}, returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        service = interaction.details.get("service", "?")
        operation = interaction.details.get("operation", "?")
        params = interaction.details.get("params", {})
        return (
            f"    tripwire.boto3.assert_boto3_call(\n"
            f"        service={service!r},\n"
            f"        operation={operation!r},\n"
            f"        params={params!r},\n"
            f"    )"
        )

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config = cast(Boto3MockConfig, mock_config)
        service = getattr(config, "service", "?")
        operation = getattr(config, "operation", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"{service}.{operation}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    def assert_boto3_call(
        self,
        service: str,
        operation: str,
        *,
        params: dict[str, Any],
    ) -> None:
        """Typed helper: assert the next boto3 API call interaction.

        Wraps assert_interaction() for ergonomic use. All three fields
        (service, operation, params) are required.
        """
        from tripwire._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _Boto3Sentinel(service, operation)
        _get_test_verifier_or_raise().assert_interaction(
            sentinel,
            service=service,
            operation=operation,
            params=params,
        )
