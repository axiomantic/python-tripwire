"""Unit tests for JwtPlugin."""

from __future__ import annotations

import jwt  # noqa: F401
import pytest

from tripwire._context import _current_test_verifier
from tripwire._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from tripwire._timeline import Interaction
from tripwire._verifier import StrictVerifier
from tripwire.plugins.jwt_plugin import (
    _JWT_AVAILABLE,
    JwtMockConfig,
    JwtPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, JwtPlugin]:
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, JwtPlugin):
            return v, p
    p = JwtPlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    with JwtPlugin._install_lock:
        JwtPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        JwtPlugin.__new__(JwtPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_jwt_available_flag() -> None:
    assert _JWT_AVAILABLE is True


def test_activate_raises_when_jwt_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import tripwire.plugins.jwt_plugin as _jp

    v, p = _make_verifier_with_plugin()
    monkeypatch.setattr(_jp, "_JWT_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install pytest-tripwire[jwt] to use JwtPlugin: pip install pytest-tripwire[jwt]"
    )


# ---------------------------------------------------------------------------
# JwtMockConfig dataclass
# ---------------------------------------------------------------------------


def test_jwt_mock_config_fields() -> None:
    config = JwtMockConfig(
        operation="encode", returns="token123", raises=None, required=False
    )
    assert config.operation == "encode"
    assert config.returns == "token123"
    assert config.raises is None
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


def test_jwt_mock_config_defaults() -> None:
    config = JwtMockConfig(operation="decode", returns={"sub": "1234"})
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patch() -> None:
    import jwt as jwt_mod

    original_encode = jwt_mod.encode
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert jwt_mod.encode is not original_encode
    p.deactivate()


def test_deactivate_restores_patch() -> None:
    import jwt as jwt_mod

    original_encode = jwt_mod.encode
    original_decode = jwt_mod.decode
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert jwt_mod.encode is original_encode
    assert jwt_mod.decode is original_decode


def test_reference_counting_nested() -> None:
    import jwt as jwt_mod

    original_encode = jwt_mod.encode
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert JwtPlugin._install_count == 2

    p.deactivate()
    assert JwtPlugin._install_count == 1
    assert jwt_mod.encode is not original_encode

    p.deactivate()
    assert JwtPlugin._install_count == 0
    assert jwt_mod.encode is original_encode


# ---------------------------------------------------------------------------
# Basic interception: mock_encode / mock_decode
# ---------------------------------------------------------------------------


def test_mock_encode_returns_value() -> None:
    import jwt as jwt_mod

    v, p = _make_verifier_with_plugin()
    p.mock_encode(returns="mocked.jwt.token")

    with v.sandbox():
        result = jwt_mod.encode({"sub": "1234"}, "secret", algorithm="HS256")

    assert result == "mocked.jwt.token"


def test_mock_decode_returns_value() -> None:
    import jwt as jwt_mod

    v, p = _make_verifier_with_plugin()
    p.mock_decode(returns={"sub": "1234", "name": "Test"})

    with v.sandbox():
        result = jwt_mod.decode("some.jwt.token", "secret", algorithms=["HS256"])

    assert result == {"sub": "1234", "name": "Test"}


# ---------------------------------------------------------------------------
# FIFO ordering
# ---------------------------------------------------------------------------


def test_mock_encode_fifo() -> None:
    import jwt as jwt_mod

    v, p = _make_verifier_with_plugin()
    p.mock_encode(returns="token1")
    p.mock_encode(returns="token2")

    with v.sandbox():
        first = jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")
        second = jwt_mod.encode({"sub": "2"}, "secret", algorithm="HS256")

    assert first == "token1"
    assert second == "token2"


# ---------------------------------------------------------------------------
# Separate queues per operation
# ---------------------------------------------------------------------------


def test_mock_separate_queues() -> None:
    import jwt as jwt_mod

    v, p = _make_verifier_with_plugin()
    p.mock_encode(returns="encoded_token")
    p.mock_decode(returns={"sub": "decoded"})

    with v.sandbox():
        encoded = jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")
        decoded = jwt_mod.decode("tok", "secret", algorithms=["HS256"])

    assert encoded == "encoded_token"
    assert decoded == {"sub": "decoded"}


# ---------------------------------------------------------------------------
# raises parameter
# ---------------------------------------------------------------------------


def test_mock_encode_raises_exception() -> None:
    import jwt as jwt_mod

    v, p = _make_verifier_with_plugin()
    p.mock_encode(returns=None, raises=ValueError("encode failed"))

    with v.sandbox():
        with pytest.raises(ValueError, match="encode failed"):
            jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")


# ---------------------------------------------------------------------------
# get_unused_mocks
# ---------------------------------------------------------------------------


def test_get_unused_mocks_returns_unconsumed_required() -> None:
    import jwt as jwt_mod

    v, p = _make_verifier_with_plugin()
    p.mock_encode(returns="token1")
    p.mock_encode(returns="token2")

    with v.sandbox():
        jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].returns == "token2"


def test_get_unused_mocks_excludes_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_encode(returns="token", required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# UnmockedInteractionError
# ---------------------------------------------------------------------------


def test_unmocked_error_when_queue_empty() -> None:
    import jwt as jwt_mod

    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")

    assert exc_info.value.source_id == "jwt:encode"


def test_unmocked_error_after_queue_exhausted() -> None:
    import jwt as jwt_mod

    v, p = _make_verifier_with_plugin()
    p.mock_encode(returns="token1")

    with v.sandbox():
        first = jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")

        with pytest.raises(UnmockedInteractionError) as exc_info:
            jwt_mod.encode({"sub": "2"}, "secret", algorithm="HS256")

    assert first == "token1"
    assert exc_info.value.source_id == "jwt:encode"


# ---------------------------------------------------------------------------
# matches() and assertable_fields()
# ---------------------------------------------------------------------------


def test_matches_field_comparison() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="jwt:encode",
        sequence=0,
        details={"payload": {"sub": "1234"}, "algorithm": "HS256", "extra_kwargs": {}},
        plugin=p,
    )
    assert p.matches(interaction, {}) is True
    assert p.matches(interaction, {"algorithm": "HS256"}) is True
    assert p.matches(interaction, {"algorithm": "RS256"}) is False


def test_assertable_fields_encode() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="jwt:encode",
        sequence=0,
        details={"payload": {"sub": "1234"}, "algorithm": "HS256", "extra_kwargs": {}},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"payload", "algorithm", "extra_kwargs"})


def test_assertable_fields_decode() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="jwt:decode",
        sequence=0,
        details={"token": "tok", "algorithms": ["HS256"], "options": None},
        plugin=p,
    )
    # key is excluded, options is None but still in details
    fields = p.assertable_fields(interaction)
    assert fields == frozenset({"token", "algorithms", "options"})


# ---------------------------------------------------------------------------
# SECURITY: key is NOT in details
# ---------------------------------------------------------------------------


def test_key_excluded_from_encode_details() -> None:
    """SECURITY: jwt.encode key must NOT appear in interaction details."""
    import jwt as jwt_mod

    v, p = _make_verifier_with_plugin()
    p.mock_encode(returns="token")

    with v.sandbox():
        jwt_mod.encode({"sub": "1"}, "super_secret_key", algorithm="HS256")

    timeline = v._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert "key" not in interactions[0].details
    assert "super_secret_key" not in str(interactions[0].details)


def test_key_excluded_from_decode_details() -> None:
    """SECURITY: jwt.decode key must NOT appear in interaction details."""
    import jwt as jwt_mod

    v, p = _make_verifier_with_plugin()
    p.mock_decode(returns={"sub": "1"})

    with v.sandbox():
        jwt_mod.decode("tok", "super_secret_key", algorithms=["HS256"])

    timeline = v._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert "key" not in interactions[0].details
    assert "super_secret_key" not in str(interactions[0].details)


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


def test_format_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="jwt:encode",
        sequence=0,
        details={"payload": {"sub": "1"}, "algorithm": "HS256", "extra_kwargs": {}},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[JwtPlugin] jwt.encode(algorithm='HS256')"


def test_format_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="jwt:encode",
        sequence=0,
        details={"payload": {"sub": "1"}, "algorithm": "HS256", "extra_kwargs": {}},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    tripwire.jwt.mock_encode(returns=...)"


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("jwt:encode", (), {})
    assert result == (
        "jwt.encode(...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    tripwire.jwt.mock_encode(returns=...)"
    )


def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = JwtMockConfig(operation="encode", returns="token")
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "jwt.encode(...) was mocked (required=True) but never called.\nRegistered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# Module-level proxy: tripwire.jwt
# ---------------------------------------------------------------------------


def test_jwt_mock_proxy_mock_encode(tripwire_verifier: StrictVerifier) -> None:
    import jwt as jwt_mod

    import tripwire

    tripwire.jwt.mock_encode(returns="proxied_token")

    with tripwire.sandbox():
        result = jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")

    assert result == "proxied_token"
    tripwire.jwt.assert_encode(payload={"sub": "1"}, algorithm="HS256", extra_kwargs={})


def test_jwt_mock_proxy_raises_outside_context() -> None:
    import tripwire
    from tripwire._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = tripwire.jwt.mock_encode
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# JwtPlugin in __all__
# ---------------------------------------------------------------------------


def test_jwt_plugin_in_all() -> None:
    import tripwire
    from tripwire.plugins.jwt_plugin import JwtPlugin as _JwtPlugin

    assert tripwire.JwtPlugin is _JwtPlugin
    assert type(tripwire.jwt).__name__ == "_JwtProxy"


# ---------------------------------------------------------------------------
# No auto-assert, typed assertion helpers
# ---------------------------------------------------------------------------


def test_jwt_interactions_not_auto_asserted(tripwire_verifier: StrictVerifier) -> None:
    import jwt as jwt_mod

    import tripwire

    tripwire.jwt.mock_encode(returns="token")
    with tripwire.sandbox():
        jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")

    timeline = tripwire_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "jwt:encode"
    tripwire.jwt.assert_encode(payload={"sub": "1"}, algorithm="HS256", extra_kwargs={})


def test_assert_encode_typed_helper(tripwire_verifier: StrictVerifier) -> None:
    import jwt as jwt_mod

    import tripwire

    tripwire.jwt.mock_encode(returns="token")
    with tripwire.sandbox():
        jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")
    tripwire.jwt.assert_encode(payload={"sub": "1"}, algorithm="HS256", extra_kwargs={})


def test_assert_decode_typed_helper(tripwire_verifier: StrictVerifier) -> None:
    import jwt as jwt_mod

    import tripwire

    tripwire.jwt.mock_decode(returns={"sub": "1"})
    with tripwire.sandbox():
        jwt_mod.decode("tok", "secret", algorithms=["HS256"])
    tripwire.jwt.assert_decode(token="tok", algorithms=["HS256"], options=None)


def test_assert_encode_wrong_params_raises(tripwire_verifier: StrictVerifier) -> None:
    import jwt as jwt_mod

    import tripwire

    tripwire.jwt.mock_encode(returns="token")
    with tripwire.sandbox():
        jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")
    with pytest.raises(InteractionMismatchError):
        tripwire.jwt.assert_encode(payload={"sub": "wrong"}, algorithm="HS256", extra_kwargs={})
    tripwire.jwt.assert_encode(payload={"sub": "1"}, algorithm="HS256", extra_kwargs={})


def test_missing_assertion_fields_raises(tripwire_verifier: StrictVerifier) -> None:
    import jwt as jwt_mod

    import tripwire

    tripwire.jwt.mock_encode(returns="token")
    with tripwire.sandbox():
        jwt_mod.encode({"sub": "1"}, "secret", algorithm="HS256")

    from tripwire.plugins.jwt_plugin import _JwtSentinel

    sentinel = _JwtSentinel("encode")
    with pytest.raises(MissingAssertionFieldsError):
        tripwire.assert_interaction(sentinel, payload={"sub": "1"})
    tripwire.jwt.assert_encode(payload={"sub": "1"}, algorithm="HS256", extra_kwargs={})
