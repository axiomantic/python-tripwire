"""Unit tests for CryptoPlugin."""

from __future__ import annotations

import cryptography  # noqa: F401
import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.crypto_plugin import (
    _CRYPTOGRAPHY_AVAILABLE,
    CryptoMockConfig,
    CryptoPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, CryptoPlugin]:
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, CryptoPlugin):
            return v, p
    p = CryptoPlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    with CryptoPlugin._install_lock:
        CryptoPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        CryptoPlugin.__new__(CryptoPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_cryptography_available_flag() -> None:
    assert _CRYPTOGRAPHY_AVAILABLE is True


def test_activate_raises_when_cryptography_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import bigfoot.plugins.crypto_plugin as _cp

    v, p = _make_verifier_with_plugin()
    monkeypatch.setattr(_cp, "_CRYPTOGRAPHY_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install bigfoot[crypto] to use CryptoPlugin: pip install bigfoot[crypto]"
    )


# ---------------------------------------------------------------------------
# CryptoMockConfig dataclass
# ---------------------------------------------------------------------------


def test_crypto_mock_config_fields() -> None:
    config = CryptoMockConfig(
        operation="fernet_encrypt", returns=b"encrypted", raises=None, required=False
    )
    assert config.operation == "fernet_encrypt"
    assert config.returns == b"encrypted"
    assert config.raises is None
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


def test_crypto_mock_config_defaults() -> None:
    config = CryptoMockConfig(operation="fernet_decrypt", returns=b"plaintext")
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patch() -> None:
    from cryptography.fernet import Fernet

    original_encrypt = Fernet.encrypt
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert Fernet.encrypt is not original_encrypt
    p.deactivate()


def test_deactivate_restores_patch() -> None:
    from cryptography.fernet import Fernet

    original_encrypt = Fernet.encrypt
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert Fernet.encrypt is original_encrypt


def test_reference_counting_nested() -> None:
    from cryptography.fernet import Fernet

    original_encrypt = Fernet.encrypt
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert CryptoPlugin._install_count == 2

    p.deactivate()
    assert CryptoPlugin._install_count == 1
    assert Fernet.encrypt is not original_encrypt

    p.deactivate()
    assert CryptoPlugin._install_count == 0
    assert Fernet.encrypt is original_encrypt


# ---------------------------------------------------------------------------
# Basic interception: Fernet encrypt/decrypt
# ---------------------------------------------------------------------------


def test_mock_fernet_encrypt_returns_value() -> None:
    from cryptography.fernet import Fernet

    v, p = _make_verifier_with_plugin()
    p.mock_encrypt(returns=b"gAAAAABmocked_ciphertext")

    with v.sandbox():
        key = Fernet.generate_key()
        f = Fernet(key)
        result = f.encrypt(b"hello world")

    assert result == b"gAAAAABmocked_ciphertext"


def test_mock_fernet_decrypt_returns_value() -> None:
    from cryptography.fernet import Fernet

    v, p = _make_verifier_with_plugin()
    p.mock_decrypt(returns=b"decrypted_plaintext")

    with v.sandbox():
        key = Fernet.generate_key()
        f = Fernet(key)
        result = f.decrypt(b"gAAAAABsome_token")

    assert result == b"decrypted_plaintext"


def test_mock_generate_key_returns_value() -> None:
    v, p = _make_verifier_with_plugin()
    # Create a mock private key object
    mock_key = object()
    p.mock_generate_key(returns=mock_key)

    with v.sandbox():
        from cryptography.hazmat.primitives.asymmetric import rsa

        result = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

    assert result is mock_key


# ---------------------------------------------------------------------------
# FIFO ordering
# ---------------------------------------------------------------------------


def test_mock_fernet_encrypt_fifo() -> None:
    from cryptography.fernet import Fernet

    v, p = _make_verifier_with_plugin()
    p.mock_encrypt(returns=b"first")
    p.mock_encrypt(returns=b"second")

    with v.sandbox():
        f = Fernet(Fernet.generate_key())
        first = f.encrypt(b"data1")
        second = f.encrypt(b"data2")

    assert first == b"first"
    assert second == b"second"


# ---------------------------------------------------------------------------
# Separate queues per operation
# ---------------------------------------------------------------------------


def test_mock_separate_queues() -> None:
    from cryptography.fernet import Fernet

    v, p = _make_verifier_with_plugin()
    p.mock_encrypt(returns=b"encrypted")
    p.mock_decrypt(returns=b"decrypted")

    with v.sandbox():
        f = Fernet(Fernet.generate_key())
        enc_result = f.encrypt(b"data")
        dec_result = f.decrypt(b"token")

    assert enc_result == b"encrypted"
    assert dec_result == b"decrypted"


# ---------------------------------------------------------------------------
# raises parameter
# ---------------------------------------------------------------------------


def test_mock_encrypt_raises_exception() -> None:
    from cryptography.fernet import Fernet

    v, p = _make_verifier_with_plugin()
    p.mock_encrypt(returns=None, raises=ValueError("encrypt failed"))

    with v.sandbox():
        f = Fernet(Fernet.generate_key())
        with pytest.raises(ValueError, match="encrypt failed"):
            f.encrypt(b"data")


# ---------------------------------------------------------------------------
# get_unused_mocks
# ---------------------------------------------------------------------------


def test_get_unused_mocks_returns_unconsumed_required() -> None:
    from cryptography.fernet import Fernet

    v, p = _make_verifier_with_plugin()
    p.mock_encrypt(returns=b"first")
    p.mock_encrypt(returns=b"second")

    with v.sandbox():
        f = Fernet(Fernet.generate_key())
        f.encrypt(b"data")

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].returns == b"second"


def test_get_unused_mocks_excludes_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_encrypt(returns=b"data", required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# UnmockedInteractionError
# ---------------------------------------------------------------------------


def test_unmocked_error_when_queue_empty() -> None:
    from cryptography.fernet import Fernet

    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        f = Fernet(Fernet.generate_key())
        with pytest.raises(UnmockedInteractionError) as exc_info:
            f.encrypt(b"data")

    assert exc_info.value.source_id == "crypto:fernet_encrypt"


def test_unmocked_error_after_queue_exhausted() -> None:
    from cryptography.fernet import Fernet

    v, p = _make_verifier_with_plugin()
    p.mock_encrypt(returns=b"encrypted")

    with v.sandbox():
        f = Fernet(Fernet.generate_key())
        first = f.encrypt(b"data1")

        with pytest.raises(UnmockedInteractionError) as exc_info:
            f.encrypt(b"data2")

    assert first == b"encrypted"
    assert exc_info.value.source_id == "crypto:fernet_encrypt"


# ---------------------------------------------------------------------------
# matches() and assertable_fields()
# ---------------------------------------------------------------------------


def test_matches_field_comparison() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="crypto:fernet_encrypt",
        sequence=0,
        details={"plaintext_length": 11},
        plugin=p,
    )
    assert p.matches(interaction, {}) is True
    assert p.matches(interaction, {"plaintext_length": 11}) is True
    assert p.matches(interaction, {"plaintext_length": 99}) is False


def test_assertable_fields_fernet_encrypt() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="crypto:fernet_encrypt",
        sequence=0,
        details={"plaintext_length": 11},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"plaintext_length"})


def test_assertable_fields_fernet_decrypt() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="crypto:fernet_decrypt",
        sequence=0,
        details={"token": b"gAAAAABtoken", "ttl": None},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"token", "ttl"})


def test_assertable_fields_generate_key() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="crypto:generate_key",
        sequence=0,
        details={"algorithm": "RSA", "key_size": 2048},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"algorithm", "key_size"})


# ---------------------------------------------------------------------------
# SECURITY: actual plaintext and keys NOT in details
# ---------------------------------------------------------------------------


def test_plaintext_not_in_encrypt_details() -> None:
    """SECURITY: actual plaintext must NOT appear in interaction details."""
    from cryptography.fernet import Fernet

    v, p = _make_verifier_with_plugin()
    p.mock_encrypt(returns=b"encrypted")

    with v.sandbox():
        f = Fernet(Fernet.generate_key())
        f.encrypt(b"super_secret_data")

    timeline = v._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    details = interactions[0].details
    assert "super_secret_data" not in str(details)
    assert details["plaintext_length"] == len(b"super_secret_data")


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


def test_format_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="crypto:fernet_encrypt",
        sequence=0,
        details={"plaintext_length": 11},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[CryptoPlugin] crypto.fernet_encrypt(plaintext_length=11)"


def test_format_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="crypto:fernet_encrypt",
        sequence=0,
        details={"plaintext_length": 11},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.crypto_mock.mock_encrypt(returns=...)"


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("crypto:fernet_encrypt", (), {})
    assert result == (
        "crypto.fernet_encrypt(...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    bigfoot.crypto_mock.mock_encrypt(returns=...)"
    )


def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = CryptoMockConfig(operation="fernet_encrypt", returns=b"data")
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "crypto.fernet_encrypt(...) was mocked (required=True) but never called.\nRegistered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.crypto_mock
# ---------------------------------------------------------------------------


def test_crypto_mock_proxy_mock_encrypt(bigfoot_verifier: StrictVerifier) -> None:
    from cryptography.fernet import Fernet

    import bigfoot

    bigfoot.crypto_mock.mock_encrypt(returns=b"proxied_encrypted")

    with bigfoot.sandbox():
        f = Fernet(Fernet.generate_key())
        result = f.encrypt(b"hello")

    assert result == b"proxied_encrypted"
    bigfoot.crypto_mock.assert_encrypt(plaintext_length=5)


def test_crypto_mock_proxy_raises_outside_context() -> None:
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.crypto_mock.mock_encrypt
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# CryptoPlugin in __all__
# ---------------------------------------------------------------------------


def test_crypto_plugin_in_all() -> None:
    import bigfoot
    from bigfoot.plugins.crypto_plugin import CryptoPlugin as _CryptoPlugin

    assert bigfoot.CryptoPlugin is _CryptoPlugin
    assert type(bigfoot.crypto_mock).__name__ == "_CryptoProxy"


# ---------------------------------------------------------------------------
# No auto-assert, typed assertion helpers
# ---------------------------------------------------------------------------


def test_crypto_interactions_not_auto_asserted(bigfoot_verifier: StrictVerifier) -> None:
    from cryptography.fernet import Fernet

    import bigfoot

    bigfoot.crypto_mock.mock_encrypt(returns=b"encrypted")
    with bigfoot.sandbox():
        f = Fernet(Fernet.generate_key())
        f.encrypt(b"data")

    timeline = bigfoot_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "crypto:fernet_encrypt"
    bigfoot.crypto_mock.assert_encrypt(plaintext_length=4)


def test_assert_encrypt_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    from cryptography.fernet import Fernet

    import bigfoot

    bigfoot.crypto_mock.mock_encrypt(returns=b"encrypted")
    with bigfoot.sandbox():
        f = Fernet(Fernet.generate_key())
        f.encrypt(b"hello")
    bigfoot.crypto_mock.assert_encrypt(plaintext_length=5)


def test_assert_decrypt_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    from cryptography.fernet import Fernet

    import bigfoot

    bigfoot.crypto_mock.mock_decrypt(returns=b"decrypted")
    with bigfoot.sandbox():
        f = Fernet(Fernet.generate_key())
        f.decrypt(b"gAAAAABtoken")
    bigfoot.crypto_mock.assert_decrypt(token=b"gAAAAABtoken", ttl=None)


def test_assert_generate_key_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    mock_key = object()
    bigfoot.crypto_mock.mock_generate_key(returns=mock_key)
    with bigfoot.sandbox():
        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bigfoot.crypto_mock.assert_generate_key(algorithm="RSA", key_size=2048)


def test_assert_encrypt_wrong_params_raises(bigfoot_verifier: StrictVerifier) -> None:
    from cryptography.fernet import Fernet

    import bigfoot

    bigfoot.crypto_mock.mock_encrypt(returns=b"encrypted")
    with bigfoot.sandbox():
        f = Fernet(Fernet.generate_key())
        f.encrypt(b"hello")
    with pytest.raises(InteractionMismatchError):
        bigfoot.crypto_mock.assert_encrypt(plaintext_length=999)
    bigfoot.crypto_mock.assert_encrypt(plaintext_length=5)


def test_missing_assertion_fields_raises(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    mock_key = object()
    bigfoot.crypto_mock.mock_generate_key(returns=mock_key)
    with bigfoot.sandbox():
        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa.generate_private_key(public_exponent=65537, key_size=2048)

    from bigfoot.plugins.crypto_plugin import _CryptoSentinel

    sentinel = _CryptoSentinel("generate_key")
    with pytest.raises(MissingAssertionFieldsError):
        bigfoot.assert_interaction(sentinel, algorithm="RSA")
    bigfoot.crypto_mock.assert_generate_key(algorithm="RSA", key_size=2048)
