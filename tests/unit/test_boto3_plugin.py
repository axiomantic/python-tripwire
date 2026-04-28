"""Unit tests for Boto3Plugin."""

from __future__ import annotations

import boto3
import botocore
import pytest

from tripwire._context import _current_test_verifier
from tripwire._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from tripwire._timeline import Interaction
from tripwire._verifier import StrictVerifier
from tripwire.plugins.boto3_plugin import (
    _BOTO3_AVAILABLE,
    Boto3MockConfig,
    Boto3Plugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, Boto3Plugin]:
    """Return (verifier, plugin) with Boto3Plugin registered but NOT activated.

    DNS and Socket calls from boto3 internals (credential provider hitting
    169.254.169.254) are prevented by the plugin's install_patches() which
    sets dummy env vars so botocore never reaches the metadata service.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, Boto3Plugin):
            return v, p
    p = Boto3Plugin(v)
    return v, p


def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with Boto3Plugin._install_lock:
        Boto3Plugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        Boto3Plugin.__new__(Boto3Plugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    """Ensure plugin install count starts and ends at 0 for every test."""
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_boto3_available_flag() -> None:
    assert _BOTO3_AVAILABLE is True


def test_activate_raises_when_boto3_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import tripwire.plugins.boto3_plugin as _bp

    v, p = _make_verifier_with_plugin()
    monkeypatch.setattr(_bp, "_BOTO3_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install python-tripwire[boto3] to use Boto3Plugin: pip install python-tripwire[boto3]"
    )


# ---------------------------------------------------------------------------
# Boto3MockConfig dataclass
# ---------------------------------------------------------------------------


def test_boto3_mock_config_fields() -> None:
    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "NoSuchBucket", "Message": "not found"}}, "GetObject"
    )
    config = Boto3MockConfig(
        service="s3", operation="GetObject", returns={"Body": b"data"}, raises=err, required=False
    )
    assert config.service == "s3"
    assert config.operation == "GetObject"
    assert config.returns == {"Body": b"data"}
    assert config.raises is err
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


def test_boto3_mock_config_defaults() -> None:
    config = Boto3MockConfig(service="s3", operation="PutObject", returns={})
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patch() -> None:
    import botocore.client

    original = botocore.client.BaseClient._make_api_call
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert botocore.client.BaseClient._make_api_call is not original
    p.deactivate()


def test_deactivate_restores_patch() -> None:
    import botocore.client

    original = botocore.client.BaseClient._make_api_call
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert botocore.client.BaseClient._make_api_call is original


def test_reference_counting_nested() -> None:
    import botocore.client

    original = botocore.client.BaseClient._make_api_call
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert Boto3Plugin._install_count == 2

    p.deactivate()
    assert Boto3Plugin._install_count == 1
    assert botocore.client.BaseClient._make_api_call is not original

    p.deactivate()
    assert Boto3Plugin._install_count == 0
    assert botocore.client.BaseClient._make_api_call is original


# ---------------------------------------------------------------------------
# Basic interception: mock_call returns value
# ---------------------------------------------------------------------------


def test_mock_call_returns_value() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("s3", "GetObject", returns={"Body": b"hello"})

    with v.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        result = client.get_object(Bucket="my-bucket", Key="my-key")

    assert result == {"Body": b"hello"}


# ---------------------------------------------------------------------------
# FIFO ordering
# ---------------------------------------------------------------------------


def test_mock_call_fifo_same_operation() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("s3", "GetObject", returns={"Body": b"first"})
    p.mock_call("s3", "GetObject", returns={"Body": b"second"})

    with v.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        first = client.get_object(Bucket="b", Key="k1")
        second = client.get_object(Bucket="b", Key="k2")

    assert first == {"Body": b"first"}
    assert second == {"Body": b"second"}


# ---------------------------------------------------------------------------
# Separate queues per service:operation
# ---------------------------------------------------------------------------


def test_mock_call_separate_queues() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("s3", "PutObject", returns={"ETag": '"abc"'})
    p.mock_call("sqs", "SendMessage", returns={"MessageId": "123"})

    with v.sandbox():
        s3 = boto3.client("s3", region_name="us-east-1")
        sqs = boto3.client("sqs", region_name="us-east-1")
        put_result = s3.put_object(Bucket="b", Key="k", Body=b"data")
        send_result = sqs.send_message(QueueUrl="http://q", MessageBody="hi")

    assert put_result == {"ETag": '"abc"'}
    assert send_result == {"MessageId": "123"}


# ---------------------------------------------------------------------------
# raises parameter
# ---------------------------------------------------------------------------


def test_mock_call_raises_exception() -> None:
    v, p = _make_verifier_with_plugin()
    err = botocore.exceptions.ClientError(
        {"Error": {"Code": "NoSuchBucket", "Message": "bucket gone"}}, "GetObject"
    )
    p.mock_call("s3", "GetObject", returns=None, raises=err)

    with v.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        with pytest.raises(botocore.exceptions.ClientError) as exc_info:
            client.get_object(Bucket="b", Key="k")

    assert "NoSuchBucket" in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_unused_mocks
# ---------------------------------------------------------------------------


def test_get_unused_mocks_returns_unconsumed_required() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("s3", "GetObject", returns={"Body": b"first"})
    p.mock_call("s3", "GetObject", returns={"Body": b"second"})

    with v.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        client.get_object(Bucket="b", Key="k")

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].operation == "GetObject"
    assert unused[0].returns == {"Body": b"second"}


def test_get_unused_mocks_excludes_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("s3", "GetObject", returns={}, required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# UnmockedInteractionError
# ---------------------------------------------------------------------------


def test_unmocked_error_when_queue_empty() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        with pytest.raises(UnmockedInteractionError) as exc_info:
            client.get_object(Bucket="b", Key="k")

    assert exc_info.value.source_id == "boto3:s3:GetObject"


def test_unmocked_error_after_queue_exhausted() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_call("s3", "GetObject", returns={"Body": b"data"})

    with v.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        first = client.get_object(Bucket="b", Key="k")

        with pytest.raises(UnmockedInteractionError) as exc_info:
            client.get_object(Bucket="b", Key="k2")

    assert first == {"Body": b"data"}
    assert exc_info.value.source_id == "boto3:s3:GetObject"


# ---------------------------------------------------------------------------
# matches() and assertable_fields()
# ---------------------------------------------------------------------------


def test_matches_field_comparison() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="boto3:s3:GetObject",
        sequence=0,
        details={"service": "s3", "operation": "GetObject", "params": {"Bucket": "b", "Key": "k"}},
        plugin=p,
    )
    assert p.matches(interaction, {}) is True
    assert p.matches(interaction, {"service": "s3"}) is True
    assert p.matches(interaction, {"service": "sqs"}) is False
    assert p.matches(interaction, {"foo": "bar"}) is False


def test_assertable_fields_all_three() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="boto3:s3:GetObject",
        sequence=0,
        details={"service": "s3", "operation": "GetObject", "params": {}},
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset({"service", "operation", "params"})


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


def test_format_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="boto3:s3:GetObject",
        sequence=0,
        details={"service": "s3", "operation": "GetObject", "params": {"Bucket": "b"}},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[Boto3Plugin] s3.GetObject(Bucket='b')"


def test_format_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="boto3:s3:GetObject",
        sequence=0,
        details={"service": "s3", "operation": "GetObject", "params": {}},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    tripwire.boto3.mock_call('s3', 'GetObject', returns=...)"


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("boto3:s3:GetObject", (), {})
    assert result == (
        "s3.GetObject(...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    tripwire.boto3.mock_call('s3', 'GetObject', returns=...)"
    )


def test_format_assert_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="boto3:s3:GetObject",
        sequence=0,
        details={"service": "s3", "operation": "GetObject", "params": {"Bucket": "b"}},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    tripwire.boto3.assert_boto3_call(\n"
        "        service='s3',\n"
        "        operation='GetObject',\n"
        "        params={'Bucket': 'b'},\n"
        "    )"
    )


def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = Boto3MockConfig(service="s3", operation="GetObject", returns={})
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "s3.GetObject(...) was mocked (required=True) but never called.\nRegistered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# Dynamic sentinel access: plugin.s3.GetObject
# ---------------------------------------------------------------------------


def test_dynamic_sentinel_access() -> None:
    v, p = _make_verifier_with_plugin()
    sentinel = p.s3.GetObject
    assert sentinel.source_id == "boto3:s3:GetObject"


def test_dynamic_sentinel_different_services() -> None:
    v, p = _make_verifier_with_plugin()
    s3_sentinel = p.s3.PutObject
    sqs_sentinel = p.sqs.SendMessage
    assert s3_sentinel.source_id == "boto3:s3:PutObject"
    assert sqs_sentinel.source_id == "boto3:sqs:SendMessage"


# ---------------------------------------------------------------------------
# Module-level proxy: tripwire.boto3
# ---------------------------------------------------------------------------


def test_boto3_mock_proxy_mock_call(tripwire_verifier: StrictVerifier) -> None:
    import tripwire

    tripwire.boto3.mock_call("s3", "GetObject", returns={"Body": b"proxied"})

    with tripwire.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        result = client.get_object(Bucket="b", Key="k")

    assert result == {"Body": b"proxied"}
    tripwire.boto3.assert_boto3_call(
        "s3", "GetObject", params={"Bucket": "b", "Key": "k"}
    )


def test_boto3_mock_proxy_raises_outside_context() -> None:
    import tripwire
    from tripwire._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = tripwire.boto3.mock_call
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# Boto3Plugin in __all__
# ---------------------------------------------------------------------------


def test_boto3_plugin_in_all() -> None:
    import tripwire
    from tripwire.plugins.boto3_plugin import Boto3Plugin as _Boto3Plugin

    assert tripwire.Boto3Plugin is _Boto3Plugin
    assert type(tripwire.boto3).__name__ == "_Boto3Proxy"


# ---------------------------------------------------------------------------
# No auto-assert, assert_boto3_call() typed helper
# ---------------------------------------------------------------------------


def test_boto3_interactions_not_auto_asserted(tripwire_verifier: StrictVerifier) -> None:
    """boto3 interactions are NOT auto-asserted."""
    import tripwire

    tripwire.boto3.mock_call("s3", "GetObject", returns={"Body": b"val"})
    with tripwire.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        client.get_object(Bucket="b", Key="k")

    timeline = tripwire_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "boto3:s3:GetObject"
    tripwire.boto3.assert_boto3_call("s3", "GetObject", params={"Bucket": "b", "Key": "k"})


def test_assert_boto3_call_typed_helper(tripwire_verifier: StrictVerifier) -> None:
    """assert_boto3_call() asserts the next boto3 interaction."""
    import tripwire

    tripwire.boto3.mock_call("s3", "PutObject", returns={"ETag": '"abc"'})
    with tripwire.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        client.put_object(Bucket="b", Key="k", Body=b"data")
    tripwire.boto3.assert_boto3_call(
        "s3", "PutObject", params={"Bucket": "b", "Key": "k", "Body": b"data"}
    )


def test_assert_boto3_call_wrong_params_raises(tripwire_verifier: StrictVerifier) -> None:
    """assert_boto3_call() with wrong params raises InteractionMismatchError."""
    import tripwire

    tripwire.boto3.mock_call("s3", "GetObject", returns={"Body": b"val"})
    with tripwire.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        client.get_object(Bucket="b", Key="k")
    with pytest.raises(InteractionMismatchError):
        tripwire.boto3.assert_boto3_call("s3", "GetObject", params={"Bucket": "wrong"})
    # Assert correctly so teardown passes
    tripwire.boto3.assert_boto3_call("s3", "GetObject", params={"Bucket": "b", "Key": "k"})


def test_missing_assertion_fields_raises(tripwire_verifier: StrictVerifier) -> None:
    """Incomplete fields in assert_interaction raises MissingAssertionFieldsError."""
    import tripwire

    tripwire.boto3.mock_call("s3", "GetObject", returns={"Body": b"val"})
    with tripwire.sandbox():
        client = boto3.client("s3", region_name="us-east-1")
        client.get_object(Bucket="b", Key="k")

    from tripwire.plugins.boto3_plugin import _Boto3Sentinel

    sentinel = _Boto3Sentinel("s3", "GetObject")
    with pytest.raises(MissingAssertionFieldsError):
        tripwire.assert_interaction(sentinel, service="s3")
    # Assert correctly so teardown passes
    tripwire.boto3.assert_boto3_call("s3", "GetObject", params={"Bucket": "b", "Key": "k"})
