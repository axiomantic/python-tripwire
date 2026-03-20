"""JwtPlugin: intercepts jwt.encode and jwt.decode with a per-operation FIFO queue."""

from __future__ import annotations

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
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import jwt as jwt_lib

    _JWT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JWT_AVAILABLE = False


# ---------------------------------------------------------------------------
# JwtMockConfig
# ---------------------------------------------------------------------------


@dataclass
class JwtMockConfig:
    """Configuration for a single mocked JWT operation invocation.

    Attributes:
        operation: "encode" or "decode".
        returns: The value to return when this mock is consumed.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the mock is reported as unused if never triggered.
        registration_traceback: Captured automatically at creation time.
    """

    operation: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def _get_jwt_plugin() -> JwtPlugin:
    verifier = _get_verifier_or_raise("jwt:operation")
    for plugin in verifier._plugins:
        if isinstance(plugin, JwtPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot JwtPlugin interceptor is active but no "
        "JwtPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _JwtSentinel:
    """Opaque handle for a JWT operation."""

    def __init__(self, operation: str) -> None:
        self.source_id = f"jwt:{operation}"


# ---------------------------------------------------------------------------
# Patched functions
# ---------------------------------------------------------------------------


def _patched_encode(
    payload: dict[str, Any], key: Any, algorithm: str | None = None,  # noqa: ANN401
    **kwargs: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    plugin = _get_jwt_plugin()
    source_id = "jwt:encode"

    with plugin._registry_lock:
        queue = plugin._queues.get("encode")
        if not queue:
            hint = plugin.format_unmocked_hint(source_id, (), {})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(),
                kwargs={"payload": payload, "algorithm": algorithm},
                hint=hint,
            )
        config = queue.popleft()

    # SECURITY: key is intentionally excluded from details
    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details={"payload": payload, "algorithm": algorithm, "extra_kwargs": kwargs},
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


def _patched_decode(
    token: str | bytes, key: Any = "", algorithms: Any = None,  # noqa: ANN401
    options: Any = None, **kwargs: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    plugin = _get_jwt_plugin()
    source_id = "jwt:decode"

    with plugin._registry_lock:
        queue = plugin._queues.get("decode")
        if not queue:
            hint = plugin.format_unmocked_hint(source_id, (), {})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(),
                kwargs={"token": token, "algorithms": algorithms},
                hint=hint,
            )
        config = queue.popleft()

    # SECURITY: key is intentionally excluded from details
    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details={"token": token, "algorithms": algorithms, "options": options},
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


# ---------------------------------------------------------------------------
# JwtPlugin
# ---------------------------------------------------------------------------


class JwtPlugin(BasePlugin):
    """JWT interception plugin.

    Patches jwt.encode and jwt.decode at the module level.
    Uses reference counting so nested sandboxes work correctly.

    SECURITY: The ``key`` parameter is intentionally excluded from
    interaction details to prevent secret keys from appearing in test
    assertion output.
    """

    supports_guard: ClassVar[bool] = False

    _install_count: ClassVar[int] = 0
    _install_lock: ClassVar[threading.Lock] = threading.Lock()
    _original_encode: ClassVar[Any] = None
    _original_decode: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[JwtMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mock_encode(
        self,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for jwt.encode()."""
        config = JwtMockConfig(
            operation="encode", returns=returns, raises=raises, required=required
        )
        with self._registry_lock:
            if "encode" not in self._queues:
                self._queues["encode"] = deque()
            self._queues["encode"].append(config)

    def mock_decode(
        self,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for jwt.decode()."""
        config = JwtMockConfig(
            operation="decode", returns=returns, raises=raises, required=required
        )
        with self._registry_lock:
            if "decode" not in self._queues:
                self._queues["decode"] = deque()
            self._queues["decode"].append(config)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        if not _JWT_AVAILABLE:
            raise ImportError(
                "Install bigfoot[jwt] to use JwtPlugin: pip install bigfoot[jwt]"
            )
        with JwtPlugin._install_lock:
            if JwtPlugin._install_count == 0:
                JwtPlugin._original_encode = jwt_lib.encode
                JwtPlugin._original_decode = jwt_lib.decode
                jwt_lib.encode = _patched_encode  # type: ignore[assignment]
                jwt_lib.decode = _patched_decode  # type: ignore[assignment]
            JwtPlugin._install_count += 1

    def deactivate(self) -> None:
        with JwtPlugin._install_lock:
            JwtPlugin._install_count = max(0, JwtPlugin._install_count - 1)
            if JwtPlugin._install_count == 0:
                if JwtPlugin._original_encode is not None:
                    jwt_lib.encode = JwtPlugin._original_encode
                    JwtPlugin._original_encode = None
                if JwtPlugin._original_decode is not None:
                    jwt_lib.decode = JwtPlugin._original_decode
                    JwtPlugin._original_decode = None

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

    def get_unused_mocks(self) -> list[JwtMockConfig]:
        unused: list[JwtMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else "?"
        algorithm = interaction.details.get("algorithm")
        algo_str = f"algorithm={algorithm!r}" if algorithm else ""
        return f"[JwtPlugin] jwt.{operation}({algo_str})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else "?"
        return f"    bigfoot.jwt_mock.mock_{operation}(returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        operation = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        return (
            f"jwt.{operation}(...) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    bigfoot.jwt_mock.mock_{operation}(returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else "?"
        details = interaction.details
        parts = [f"        {k}={v!r}," for k, v in details.items()]
        lines = "\n".join(parts)
        return (
            f"    bigfoot.jwt_mock.assert_{operation}(\n"
            f"{lines}\n"
            f"    )"
        )

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config: JwtMockConfig = mock_config  # type: ignore[assignment]
        operation = getattr(config, "operation", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"jwt.{operation}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def assert_encode(
        self, *, payload: dict[str, Any], algorithm: str | None,
        extra_kwargs: dict[str, Any] | None = None,
        **extra: Any,  # noqa: ANN401
    ) -> None:
        """Assert the next jwt.encode() interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _JwtSentinel("encode")
        actual_extra_kwargs = extra_kwargs if extra_kwargs is not None else {}
        _get_test_verifier_or_raise().assert_interaction(
            sentinel, payload=payload, algorithm=algorithm,
            extra_kwargs=actual_extra_kwargs, **extra,
        )

    def assert_decode(  # noqa: ANN401
        self, *, token: str | bytes, algorithms: Any,  # noqa: ANN401
        options: Any = None, **extra: Any,  # noqa: ANN401
    ) -> None:
        """Assert the next jwt.decode() interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _JwtSentinel("decode")
        _get_test_verifier_or_raise().assert_interaction(
            sentinel, token=token, algorithms=algorithms, options=options, **extra
        )
