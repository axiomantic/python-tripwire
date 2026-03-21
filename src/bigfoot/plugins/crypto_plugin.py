"""CryptoPlugin: intercepts cryptography Fernet encrypt/decrypt and RSA key generation."""

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
    from cryptography.fernet import Fernet as _Fernet
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa_mod

    _CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _CRYPTOGRAPHY_AVAILABLE = False

# Map operation names to user-friendly mock method names
_OPERATION_MOCK_NAMES: dict[str, str] = {
    "fernet_encrypt": "mock_encrypt",
    "fernet_decrypt": "mock_decrypt",
    "generate_key": "mock_generate_key",
}


# ---------------------------------------------------------------------------
# CryptoMockConfig
# ---------------------------------------------------------------------------


@dataclass
class CryptoMockConfig:
    """Configuration for a single mocked cryptography operation invocation.

    Attributes:
        operation: The operation name (e.g., "fernet_encrypt", "fernet_decrypt", "generate_key").
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


def _get_crypto_plugin() -> CryptoPlugin:
    verifier = _get_verifier_or_raise("crypto:operation")
    for plugin in verifier._plugins:
        if isinstance(plugin, CryptoPlugin):
            return plugin
    raise RuntimeError(
        "BUG: bigfoot CryptoPlugin interceptor is active but no "
        "CryptoPlugin is registered on the current verifier."
    )


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


class _CryptoSentinel:
    """Opaque handle for a cryptography operation."""

    def __init__(self, operation: str) -> None:
        self.source_id = f"crypto:{operation}"


# ---------------------------------------------------------------------------
# Patched functions
# ---------------------------------------------------------------------------


def _patched_fernet_encrypt(fernet_self: object, data: bytes) -> Any:  # noqa: ANN401
    plugin = _get_crypto_plugin()
    source_id = "crypto:fernet_encrypt"

    with plugin._registry_lock:
        queue = plugin._queues.get("fernet_encrypt")
        if not queue:
            hint = plugin.format_unmocked_hint(source_id, (), {})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(),
                kwargs={"plaintext_length": len(data)},
                hint=hint,
            )
        config = queue.popleft()

    # SECURITY: actual plaintext NOT stored, only length
    details_enc: dict[str, Any] = {"plaintext_length": len(data)}
    if config.raises is not None:
        details_enc["raised"] = config.raises
    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details=details_enc,
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


def _patched_fernet_decrypt(fernet_self: object, token: bytes | str, ttl: int | None = None) -> Any:  # noqa: ANN401
    plugin = _get_crypto_plugin()
    source_id = "crypto:fernet_decrypt"

    with plugin._registry_lock:
        queue = plugin._queues.get("fernet_decrypt")
        if not queue:
            hint = plugin.format_unmocked_hint(source_id, (), {})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(),
                kwargs={"token": token},
                hint=hint,
            )
        config = queue.popleft()

    # Token is safe to store (it's ciphertext, not secret)
    details_dec: dict[str, Any] = {"token": token, "ttl": ttl}
    if config.raises is not None:
        details_dec["raised"] = config.raises
    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details=details_dec,
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


def _patched_generate_private_key(
    public_exponent: int = 65537, key_size: int = 2048,
    backend: Any = None,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    plugin = _get_crypto_plugin()
    source_id = "crypto:generate_key"

    with plugin._registry_lock:
        queue = plugin._queues.get("generate_key")
        if not queue:
            hint = plugin.format_unmocked_hint(source_id, (), {})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(),
                kwargs={"algorithm": "RSA", "key_size": key_size},
                hint=hint,
            )
        config = queue.popleft()

    # SECURITY: no actual key data stored, only metadata
    details_gen: dict[str, Any] = {"algorithm": "RSA", "key_size": key_size}
    if config.raises is not None:
        details_gen["raised"] = config.raises
    interaction = Interaction(
        source_id=source_id,
        sequence=0,
        details=details_gen,
        plugin=plugin,
    )
    plugin.record(interaction)

    if config.raises is not None:
        raise config.raises
    return config.returns


# ---------------------------------------------------------------------------
# CryptoPlugin
# ---------------------------------------------------------------------------


class CryptoPlugin(BasePlugin):
    """Cryptography interception plugin.

    Patches Fernet.encrypt, Fernet.decrypt, and rsa.generate_private_key.
    Uses reference counting so nested sandboxes work correctly.

    SECURITY: Actual plaintext, keys, and signatures are NOT stored in
    interaction details. Only metadata (lengths, algorithm names, key sizes)
    is recorded.
    """

    supports_guard: ClassVar[bool] = False

    _original_encrypt: ClassVar[Any] = None
    _original_decrypt: ClassVar[Any] = None
    _original_generate_private_key: ClassVar[Any] = None

    def __init__(self, verifier: StrictVerifier) -> None:
        super().__init__(verifier)
        self._queues: dict[str, deque[CryptoMockConfig]] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mock_encrypt(
        self,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for Fernet.encrypt()."""
        config = CryptoMockConfig(
            operation="fernet_encrypt", returns=returns, raises=raises, required=required
        )
        with self._registry_lock:
            if "fernet_encrypt" not in self._queues:
                self._queues["fernet_encrypt"] = deque()
            self._queues["fernet_encrypt"].append(config)

    def mock_decrypt(
        self,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for Fernet.decrypt()."""
        config = CryptoMockConfig(
            operation="fernet_decrypt", returns=returns, raises=raises, required=required
        )
        with self._registry_lock:
            if "fernet_decrypt" not in self._queues:
                self._queues["fernet_decrypt"] = deque()
            self._queues["fernet_decrypt"].append(config)

    def mock_generate_key(
        self,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> None:
        """Register a mock for rsa.generate_private_key()."""
        config = CryptoMockConfig(
            operation="generate_key", returns=returns, raises=raises, required=required
        )
        with self._registry_lock:
            if "generate_key" not in self._queues:
                self._queues["generate_key"] = deque()
            self._queues["generate_key"].append(config)

    # ------------------------------------------------------------------
    # BasePlugin lifecycle
    # ------------------------------------------------------------------

    def _install_patches(self) -> None:
        """Install cryptography Fernet and RSA patches."""
        if not _CRYPTOGRAPHY_AVAILABLE:
            raise ImportError(
                "Install bigfoot[crypto] to use CryptoPlugin: pip install bigfoot[crypto]"
            )
        CryptoPlugin._original_encrypt = _Fernet.encrypt
        CryptoPlugin._original_decrypt = _Fernet.decrypt
        CryptoPlugin._original_generate_private_key = _rsa_mod.generate_private_key
        _Fernet.encrypt = _patched_fernet_encrypt  # type: ignore[assignment]
        _Fernet.decrypt = _patched_fernet_decrypt  # type: ignore[assignment]
        _rsa_mod.generate_private_key = _patched_generate_private_key

    def _restore_patches(self) -> None:
        """Restore original cryptography functions."""
        if CryptoPlugin._original_encrypt is not None:
            _Fernet.encrypt = CryptoPlugin._original_encrypt  # type: ignore[method-assign]
            CryptoPlugin._original_encrypt = None
        if CryptoPlugin._original_decrypt is not None:
            _Fernet.decrypt = CryptoPlugin._original_decrypt  # type: ignore[method-assign]
            CryptoPlugin._original_decrypt = None
        if CryptoPlugin._original_generate_private_key is not None:
            _rsa_mod.generate_private_key = CryptoPlugin._original_generate_private_key
            CryptoPlugin._original_generate_private_key = None

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

    def get_unused_mocks(self) -> list[CryptoMockConfig]:
        unused: list[CryptoMockConfig] = []
        with self._registry_lock:
            for queue in self._queues.values():
                for config in queue:
                    if config.required:
                        unused.append(config)
        return unused

    def format_interaction(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else "?"
        details = interaction.details
        parts = [f"{k}={v!r}" for k, v in details.items()]
        return f"[CryptoPlugin] crypto.{operation}({', '.join(parts)})"

    def format_mock_hint(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else "?"
        mock_name = _OPERATION_MOCK_NAMES.get(operation, f"mock_{operation}")
        return f"    bigfoot.crypto_mock.{mock_name}(returns=...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        operation = source_id.split(":", 1)[-1] if ":" in source_id else source_id
        mock_name = _OPERATION_MOCK_NAMES.get(operation, f"mock_{operation}")
        return (
            f"crypto.{operation}(...) was called but no mock was registered.\n"
            f"Register a mock with:\n"
            f"    bigfoot.crypto_mock.{mock_name}(returns=...)"
        )

    def format_assert_hint(self, interaction: Interaction) -> str:
        source_id = interaction.source_id
        operation = source_id.split(":", 1)[-1] if ":" in source_id else "?"
        details = interaction.details
        parts = [f"        {k}={v!r}," for k, v in details.items()]
        lines = "\n".join(parts)
        helper_name = {
            "fernet_encrypt": "assert_encrypt",
            "fernet_decrypt": "assert_decrypt",
            "generate_key": "assert_generate_key",
        }.get(operation, f"assert_{operation}")
        return (
            f"    bigfoot.crypto_mock.{helper_name}(\n"
            f"{lines}\n"
            f"    )"
        )

    def format_unused_mock_hint(self, mock_config: object) -> str:
        config: CryptoMockConfig = mock_config  # type: ignore[assignment]
        operation = getattr(config, "operation", "?")
        tb = getattr(config, "registration_traceback", "")
        return (
            f"crypto.{operation}(...) was mocked (required=True) but never called.\n"
            f"Registered at:\n{tb}"
        )

    # ------------------------------------------------------------------
    # Typed assertion helpers
    # ------------------------------------------------------------------

    def assert_encrypt(self, *, plaintext_length: int, **extra: Any) -> None:  # noqa: ANN401
        """Assert the next Fernet.encrypt() interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _CryptoSentinel("fernet_encrypt")
        _get_test_verifier_or_raise().assert_interaction(
            sentinel, plaintext_length=plaintext_length, **extra
        )

    def assert_decrypt(self, *, token: bytes | str, ttl: int | None = None, **extra: Any) -> None:  # noqa: ANN401
        """Assert the next Fernet.decrypt() interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _CryptoSentinel("fernet_decrypt")
        _get_test_verifier_or_raise().assert_interaction(
            sentinel, token=token, ttl=ttl, **extra
        )

    def assert_generate_key(self, *, algorithm: str, key_size: int, **extra: Any) -> None:  # noqa: ANN401
        """Assert the next rsa.generate_private_key() interaction."""
        from bigfoot._context import _get_test_verifier_or_raise  # noqa: PLC0415

        sentinel = _CryptoSentinel("generate_key")
        _get_test_verifier_or_raise().assert_interaction(
            sentinel, algorithm=algorithm, key_size=key_size, **extra
        )
