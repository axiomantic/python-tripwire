"""Unit tests for GrpcPlugin."""

from __future__ import annotations

import grpc
import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.grpc_plugin import (
    _GRPC_AVAILABLE,
    GrpcMockConfig,
    GrpcPlugin,
    _MockStreamIterator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, GrpcPlugin]:
    """Return (verifier, plugin) with GrpcPlugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    GrpcPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, GrpcPlugin):
            return v, p
    p = GrpcPlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with GrpcPlugin._install_lock:
        GrpcPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        GrpcPlugin.__new__(GrpcPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    """Ensure plugin install count starts and ends at 0 for every test."""
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


# ESCAPE: test_grpc_available_flag
#   CLAIM: _GRPC_AVAILABLE is True when grpcio is importable.
#   PATH:  Module-level try/except import guard in grpc_plugin.py.
#   CHECK: _GRPC_AVAILABLE is True (since grpcio is installed).
#   MUTATION: Setting it to False when grpc IS importable fails the equality check.
#   ESCAPE: Nothing reasonable -- exact boolean equality.
def test_grpc_available_flag() -> None:
    assert _GRPC_AVAILABLE is True


# ESCAPE: test_activate_raises_when_grpc_unavailable
#   CLAIM: If _GRPC_AVAILABLE is False, calling activate() raises ImportError
#          with the exact installation hint message.
#   PATH:  activate() -> check _GRPC_AVAILABLE -> False -> raise ImportError.
#   CHECK: ImportError raised; str(exc) == exact message string.
#   MUTATION: Not checking the flag and proceeding normally would not raise.
#   ESCAPE: Raising ImportError with a different message fails the exact string check.
def test_activate_raises_when_grpc_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import bigfoot.plugins.grpc_plugin as _gp

    v, p = _make_verifier_with_plugin()
    monkeypatch.setattr(_gp, "_GRPC_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install bigfoot[grpc] to use GrpcPlugin: pip install bigfoot[grpc]"
    )


# ---------------------------------------------------------------------------
# GrpcMockConfig dataclass
# ---------------------------------------------------------------------------


# ESCAPE: test_grpc_mock_config_fields
#   CLAIM: GrpcMockConfig stores method, call_type, returns, raises, required correctly.
#   PATH:  Dataclass construction.
#   CHECK: All fields equal their expected values.
#   MUTATION: Wrong field name or default value fails equality check.
#   ESCAPE: Nothing reasonable -- exact equality on all fields.
def test_grpc_mock_config_fields() -> None:
    err = ValueError("bad request")
    config = GrpcMockConfig(
        method="/pkg.Svc/Do",
        call_type="unary_unary",
        returns=b"response",
        raises=err,
        required=False,
    )
    assert config.method == "/pkg.Svc/Do"
    assert config.call_type == "unary_unary"
    assert config.returns == b"response"
    assert config.raises is err
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


# ESCAPE: test_grpc_mock_config_defaults
#   CLAIM: GrpcMockConfig defaults: raises=None, required=True.
#   PATH:  Dataclass construction with minimal arguments.
#   CHECK: Default fields equal expected values.
#   MUTATION: Changing defaults fails the equality check.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_grpc_mock_config_defaults() -> None:
    config = GrpcMockConfig(
        method="/pkg.Svc/Do",
        call_type="unary_unary",
        returns=b"ok",
    )
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activate / deactivate
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patch
#   CLAIM: activate() replaces grpc.insecure_channel and grpc.secure_channel.
#   PATH:  activate() -> saves originals -> replaces with fakes.
#   CHECK: After activate, grpc.insecure_channel is not the original.
#   MUTATION: Not patching means grpc.insecure_channel stays original.
#   ESCAPE: Nothing reasonable -- identity check.
def test_activate_installs_patch() -> None:
    original_insecure = grpc.insecure_channel
    original_secure = grpc.secure_channel
    v, p = _make_verifier_with_plugin()
    p.activate()
    try:
        assert grpc.insecure_channel is not original_insecure
        assert grpc.secure_channel is not original_secure
    finally:
        p.deactivate()


# ESCAPE: test_deactivate_restores_patch
#   CLAIM: deactivate() restores grpc.insecure_channel and grpc.secure_channel.
#   PATH:  deactivate() -> decrement count -> restore originals at 0.
#   CHECK: After deactivate, grpc.insecure_channel is the original.
#   MUTATION: Not restoring means the fake stays.
#   ESCAPE: Nothing reasonable -- identity check.
def test_deactivate_restores_patch() -> None:
    original_insecure = grpc.insecure_channel
    original_secure = grpc.secure_channel
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert grpc.insecure_channel is original_insecure
    assert grpc.secure_channel is original_secure


# ESCAPE: test_reference_counting_nested
#   CLAIM: Multiple activate() calls require matching deactivate() calls.
#   PATH:  activate() increments count; deactivate() decrements. Restore only at 0.
#   CHECK: After 2 activate + 1 deactivate, still patched. After 2nd deactivate, restored.
#   MUTATION: Not reference counting means first deactivate restores.
#   ESCAPE: Nothing reasonable -- state transitions verified.
def test_reference_counting_nested() -> None:
    original_insecure = grpc.insecure_channel
    v1, p1 = _make_verifier_with_plugin()
    v2, p2 = _make_verifier_with_plugin()
    p1.activate()
    p2.activate()
    p1.deactivate()
    # Still patched because p2 is active
    assert grpc.insecure_channel is not original_insecure
    p2.deactivate()
    # Now restored
    assert grpc.insecure_channel is original_insecure


# ---------------------------------------------------------------------------
# Basic interception (unary-unary)
# ---------------------------------------------------------------------------


# ESCAPE: test_unary_unary_basic_interception
#   CLAIM: A unary_unary call through a fake channel records an interaction and returns the mock.
#   PATH:  grpc.insecure_channel() -> _FakeChannel -> unary_unary() -> _GrpcCallable -> __call__()
#          -> pops from FIFO -> records interaction -> returns config.returns.
#   CHECK: Return value equals the mock value; interaction on timeline has correct details.
#   MUTATION: Not recording the interaction leaves timeline empty. Wrong return value fails equality.
#   ESCAPE: Nothing reasonable -- exact equality on return value and interaction details.
def test_unary_unary_basic_interception(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Do", returns=b"response-data")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Do")
        result = stub(b"request-data")

    assert result == b"response-data"
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Do",
        request=b"request-data",
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Assertable fields (full assertion certainty)
# ---------------------------------------------------------------------------


# ESCAPE: test_assertable_fields_returns_all_detail_keys
#   CLAIM: assertable_fields() returns frozenset(interaction.details.keys()).
#   PATH:  BasePlugin default assertable_fields returns all keys.
#   CHECK: Returned frozenset equals expected fields.
#   MUTATION: Returning a subset would miss fields.
#   ESCAPE: Nothing reasonable -- exact frozenset comparison.
def test_assertable_fields_returns_all_detail_keys() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="grpc:unary_unary:/pkg.Svc/Do",
        sequence=0,
        details={
            "method": "/pkg.Svc/Do",
            "call_type": "unary_unary",
            "request": b"data",
            "metadata": None,
        },
        plugin=p,
    )
    assert p.assertable_fields(interaction) == frozenset(
        {"method", "call_type", "request", "metadata"}
    )


# ---------------------------------------------------------------------------
# Unmocked interaction error
# ---------------------------------------------------------------------------


# ESCAPE: test_unmocked_error_when_no_mock_registered
#   CLAIM: Calling a gRPC method without registering a mock raises UnmockedInteractionError.
#   PATH:  _GrpcCallable.__call__() -> queue empty -> raise UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised with correct source_id.
#   MUTATION: Not raising lets the call pass through.
#   ESCAPE: Raising a different exception fails the type check.
def test_unmocked_error_when_no_mock_registered(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Missing")
        with pytest.raises(UnmockedInteractionError) as exc_info:
            stub(b"request")
    assert exc_info.value.source_id == "grpc:unary_unary:/pkg.Svc/Missing"


# ---------------------------------------------------------------------------
# Unused mock warning
# ---------------------------------------------------------------------------


# ESCAPE: test_get_unused_mocks_returns_unconsumed_required
#   CLAIM: Mocks registered with required=True that are never consumed appear in get_unused_mocks().
#   PATH:  mock_unary_unary() -> queue -> never popped -> get_unused_mocks() iterates queues.
#   CHECK: Returned list contains exactly the unconsumed config.
#   MUTATION: Not returning unconsumed mocks means empty list.
#   ESCAPE: Nothing reasonable -- exact list contents.
def test_get_unused_mocks_returns_unconsumed_required() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_unary_unary("/pkg.Svc/Do", returns=b"val")
    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].method == "/pkg.Svc/Do"
    assert unused[0].call_type == "unary_unary"
    assert unused[0].returns == b"val"
    assert unused[0].required is True


# ESCAPE: test_get_unused_mocks_excludes_required_false
#   CLAIM: Mocks with required=False are excluded from get_unused_mocks().
#   PATH:  mock_unary_unary(required=False) -> get_unused_mocks() skips required=False.
#   CHECK: Returned list is empty.
#   MUTATION: Including required=False mocks fails the emptiness check.
#   ESCAPE: Nothing reasonable -- empty list check.
def test_get_unused_mocks_excludes_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_unary_unary("/pkg.Svc/Do", returns=b"val", required=False)
    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# Missing fields error
# ---------------------------------------------------------------------------


# ESCAPE: test_missing_fields_raises_error
#   CLAIM: Asserting with incomplete fields raises MissingAssertionFieldsError.
#   PATH:  assert_interaction() -> assertable_fields() -> compare expected keys -> raise.
#   CHECK: MissingAssertionFieldsError raised.
#   MUTATION: Not checking fields passes the test.
#   ESCAPE: Nothing reasonable -- error type check.
def test_missing_fields_raises_error(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Do", returns=b"val")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Do")
        stub(b"req")

    # Only provide method, missing call_type, request, metadata
    from bigfoot.plugins.grpc_plugin import _GrpcSentinel

    sentinel = _GrpcSentinel("grpc:unary_unary:/pkg.Svc/Do")
    with pytest.raises(MissingAssertionFieldsError):
        bigfoot_verifier.assert_interaction(
            sentinel,
            method="/pkg.Svc/Do",
        )

    # Now assert correctly so teardown passes
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Do",
        request=b"req",
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Typed assertion helpers (positive)
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_unary_unary_helper
#   CLAIM: assert_unary_unary() asserts the next unary_unary interaction with all required fields.
#   PATH:  assert_unary_unary() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_unary_unary_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Do", returns=b"ok")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Do")
        stub(b"req")

    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Do",
        request=b"req",
        metadata=None,
    )


# ESCAPE: test_assert_unary_stream_helper
#   CLAIM: assert_unary_stream() asserts the next unary_stream interaction.
#   PATH:  assert_unary_stream() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable.
def test_assert_unary_stream_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_stream("/pkg.Svc/ServerStream", returns=[b"r1", b"r2"])
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_stream("/pkg.Svc/ServerStream")
        response_iter = stub(b"req")
        responses = list(response_iter)

    assert responses == [b"r1", b"r2"]
    bigfoot.grpc_mock.assert_unary_stream(
        method="/pkg.Svc/ServerStream",
        request=b"req",
        metadata=None,
    )


# ESCAPE: test_assert_stream_unary_helper
#   CLAIM: assert_stream_unary() asserts the next stream_unary interaction.
#   PATH:  assert_stream_unary() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised; request is materialized list.
#   MUTATION: Not consuming the iterator means request would be wrong.
#   ESCAPE: Nothing reasonable.
def test_assert_stream_unary_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_stream_unary("/pkg.Svc/ClientStream", returns=b"merged")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.stream_unary("/pkg.Svc/ClientStream")
        result = stub(iter([b"c1", b"c2"]))

    assert result == b"merged"
    bigfoot.grpc_mock.assert_stream_unary(
        method="/pkg.Svc/ClientStream",
        request=[b"c1", b"c2"],
        metadata=None,
    )


# ESCAPE: test_assert_stream_stream_helper
#   CLAIM: assert_stream_stream() asserts the next stream_stream interaction.
#   PATH:  assert_stream_stream() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised; request is materialized list.
#   MUTATION: Not consuming the request iterator means request would be wrong.
#   ESCAPE: Nothing reasonable.
def test_assert_stream_stream_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_stream_stream("/pkg.Svc/Bidi", returns=[b"s1", b"s2"])
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.stream_stream("/pkg.Svc/Bidi")
        response_iter = stub(iter([b"c1"]))
        responses = list(response_iter)

    assert responses == [b"s1", b"s2"]
    bigfoot.grpc_mock.assert_stream_stream(
        method="/pkg.Svc/Bidi",
        request=[b"c1"],
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Typed assertion helpers (negative)
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_unary_unary_wrong_request_raises
#   CLAIM: assert_unary_unary() raises InteractionMismatchError when request doesn't match.
#   PATH:  assert_unary_unary() -> verifier.assert_interaction() -> matches() returns False.
#   CHECK: InteractionMismatchError raised.
#   MUTATION: Always matching would not raise.
#   ESCAPE: Nothing reasonable -- type check.
def test_assert_unary_unary_wrong_request_raises(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Do", returns=b"ok")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Do")
        stub(b"actual-request")

    with pytest.raises(InteractionMismatchError):
        bigfoot.grpc_mock.assert_unary_unary(
            method="/pkg.Svc/Do",
            request=b"WRONG-request",
            metadata=None,
        )
    # Assert correctly so teardown passes
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Do",
        request=b"actual-request",
        metadata=None,
    )


# ESCAPE: test_assert_unary_unary_wrong_method_raises
#   CLAIM: assert_unary_unary() raises InteractionMismatchError when method doesn't match.
#   PATH:  assert_unary_unary() -> verifier.assert_interaction() -> matches() returns False.
#   CHECK: InteractionMismatchError raised.
#   MUTATION: Not checking method field means wrong method passes.
#   ESCAPE: Nothing reasonable.
def test_assert_unary_unary_wrong_method_raises(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Do", returns=b"ok")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Do")
        stub(b"req")

    with pytest.raises(InteractionMismatchError):
        bigfoot.grpc_mock.assert_unary_unary(
            method="/pkg.Svc/WRONG",
            request=b"req",
            metadata=None,
        )
    # Assert correctly
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Do",
        request=b"req",
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


# ESCAPE: test_conflict_detection_double_activate
#   CLAIM: Two GrpcPlugin instances can activate without conflict due to ref counting.
#   PATH:  activate() -> reference counting handles multiple activations.
#   CHECK: Both activate without raising; both deactivate cleanly.
#   MUTATION: Not ref counting causes double-patch or error on second activate.
#   ESCAPE: Nothing reasonable -- no exception raised.
def test_conflict_detection_double_activate() -> None:
    v1, p1 = _make_verifier_with_plugin()
    v2, p2 = _make_verifier_with_plugin()
    p1.activate()
    p2.activate()
    p2.deactivate()
    p1.deactivate()
    # If we get here without error, ref counting works


# ---------------------------------------------------------------------------
# Exception propagation
# ---------------------------------------------------------------------------


# ESCAPE: test_exception_propagation
#   CLAIM: A mock with raises= propagates the exception to the caller.
#   PATH:  _GrpcCallable.__call__() -> config.raises is not None -> raise.
#   CHECK: The exact exception instance is raised.
#   MUTATION: Not checking raises means the mock return value is returned instead.
#   ESCAPE: Raising a different exception fails the identity check.
def test_exception_propagation(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    err = grpc.RpcError()
    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Fail", returns=None, raises=err)
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Fail")
        with pytest.raises(grpc.RpcError) as exc_info:
            stub(b"req")

    assert exc_info.value is err
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Fail",
        request=b"req",
        metadata=None,
        raised=err,
    )


# ---------------------------------------------------------------------------
# Server streaming
# ---------------------------------------------------------------------------


# ESCAPE: test_server_streaming_returns_iterator
#   CLAIM: unary_stream returns a _MockStreamIterator that yields configured responses.
#   PATH:  _GrpcCallable.__call__() -> _MockStreamIterator(returns) -> iter yields items.
#   CHECK: Collected list equals the configured responses.
#   MUTATION: Not returning an iterator means direct return (not iterable).
#   ESCAPE: Nothing reasonable -- exact list equality.
def test_server_streaming_returns_iterator(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_stream("/pkg.Svc/Stream", returns=[b"a", b"b", b"c"])
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_stream("/pkg.Svc/Stream")
        response_iter = stub(b"req")
        responses = list(response_iter)

    assert responses == [b"a", b"b", b"c"]
    bigfoot.grpc_mock.assert_unary_stream(
        method="/pkg.Svc/Stream",
        request=b"req",
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Client streaming
# ---------------------------------------------------------------------------


# ESCAPE: test_client_streaming_materializes_request
#   CLAIM: stream_unary eagerly consumes the request iterator into a list.
#   PATH:  _GrpcCallable.__call__() -> list(request_iterator) -> records as request.
#   CHECK: Interaction request equals the materialized list.
#   MUTATION: Not consuming the iterator means request is the iterator object.
#   ESCAPE: Nothing reasonable -- exact list equality.
def test_client_streaming_materializes_request(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_stream_unary("/pkg.Svc/Upload", returns=b"done")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.stream_unary("/pkg.Svc/Upload")
        result = stub(iter([b"chunk1", b"chunk2", b"chunk3"]))

    assert result == b"done"
    bigfoot.grpc_mock.assert_stream_unary(
        method="/pkg.Svc/Upload",
        request=[b"chunk1", b"chunk2", b"chunk3"],
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Bidi streaming
# ---------------------------------------------------------------------------


# ESCAPE: test_bidi_streaming
#   CLAIM: stream_stream consumes request iterator and returns _MockStreamIterator.
#   PATH:  _GrpcCallable.__call__() -> list(request_iter) -> records -> _MockStreamIterator.
#   CHECK: Both request and response match expected.
#   MUTATION: Not consuming request or not returning iterator fails.
#   ESCAPE: Nothing reasonable.
def test_bidi_streaming(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_stream_stream("/pkg.Svc/Chat", returns=[b"r1", b"r2"])
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.stream_stream("/pkg.Svc/Chat")
        response_iter = stub(iter([b"c1", b"c2"]))
        responses = list(response_iter)

    assert responses == [b"r1", b"r2"]
    bigfoot.grpc_mock.assert_stream_stream(
        method="/pkg.Svc/Chat",
        request=[b"c1", b"c2"],
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Empty streams
# ---------------------------------------------------------------------------


# ESCAPE: test_empty_streams
#   CLAIM: Empty request iterator and empty returns list both work.
#   PATH:  list(iter([])) -> [] for request; _MockStreamIterator([]) -> empty.
#   CHECK: Request is empty list; responses are empty list.
#   MUTATION: Failing on empty input means the test fails.
#   ESCAPE: Nothing reasonable -- exact equality on empty lists.
def test_empty_streams(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_stream_stream("/pkg.Svc/Empty", returns=[])
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.stream_stream("/pkg.Svc/Empty")
        response_iter = stub(iter([]))
        responses = list(response_iter)

    assert responses == []
    bigfoot.grpc_mock.assert_stream_stream(
        method="/pkg.Svc/Empty",
        request=[],
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Mid-stream error
# ---------------------------------------------------------------------------


# ESCAPE: test_mid_stream_error
#   CLAIM: _MockStreamIterator yields partial responses then raises the configured error.
#   PATH:  _MockStreamIterator.__next__() -> yields items -> StopIteration -> raises error.
#   CHECK: Partial responses collected; then the error is raised on next iteration.
#   MUTATION: Not raising after responses means no error on exhaustion.
#   ESCAPE: Nothing reasonable -- exact list + exception identity.
def test_mid_stream_error(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    err = grpc.RpcError()
    bigfoot.grpc_mock.mock_unary_stream("/pkg.Svc/PartialFail", returns=[b"p1", b"p2"], raises=err)
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_stream("/pkg.Svc/PartialFail")
        response_iter = stub(b"req")
        collected = []
        with pytest.raises(grpc.RpcError) as exc_info:
            for item in response_iter:
                collected.append(item)

    assert collected == [b"p1", b"p2"]
    assert exc_info.value is err
    bigfoot.grpc_mock.assert_unary_stream(
        method="/pkg.Svc/PartialFail",
        request=b"req",
        metadata=None,
        raised=err,
    )


# ---------------------------------------------------------------------------
# _MockStreamIterator unit tests
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_stream_iterator_basic
#   CLAIM: _MockStreamIterator yields items from the list and then raises StopIteration.
#   PATH:  __iter__/__next__ protocol.
#   CHECK: list() returns expected items.
#   MUTATION: Not implementing __iter__ or __next__ fails.
#   ESCAPE: Nothing reasonable.
def test_mock_stream_iterator_basic() -> None:
    it = _MockStreamIterator([1, 2, 3])
    assert list(it) == [1, 2, 3]


# ESCAPE: test_mock_stream_iterator_with_error
#   CLAIM: _MockStreamIterator raises the configured error after yielding all items.
#   PATH:  __next__ -> StopIteration caught -> raises self._raises.
#   CHECK: Items collected; error raised on exhaustion.
#   MUTATION: Not raising after exhaustion fails the error check.
#   ESCAPE: Nothing reasonable.
def test_mock_stream_iterator_with_error() -> None:
    err = RuntimeError("stream failed")
    it = _MockStreamIterator([10, 20], raises=err)
    collected = []
    with pytest.raises(RuntimeError) as exc_info:
        for item in it:
            collected.append(item)
    assert collected == [10, 20]
    assert exc_info.value is err


# ESCAPE: test_mock_stream_iterator_empty_no_error
#   CLAIM: Empty _MockStreamIterator with no raises just stops.
#   PATH:  __next__ -> StopIteration -> raises is None -> re-raise StopIteration.
#   CHECK: list() returns empty list.
#   MUTATION: Raising an error on empty fails.
#   ESCAPE: Nothing reasonable.
def test_mock_stream_iterator_empty_no_error() -> None:
    it = _MockStreamIterator([])
    assert list(it) == []


# ESCAPE: test_mock_stream_iterator_empty_with_error
#   CLAIM: Empty _MockStreamIterator with raises immediately raises on first next().
#   PATH:  __next__ -> StopIteration -> raises is set -> raises.
#   CHECK: Error raised immediately, no items collected.
#   MUTATION: Not raising means empty list returned.
#   ESCAPE: Nothing reasonable.
def test_mock_stream_iterator_empty_with_error() -> None:
    err = RuntimeError("immediate fail")
    it = _MockStreamIterator([], raises=err)
    with pytest.raises(RuntimeError) as exc_info:
        next(it)
    assert exc_info.value is err


# ---------------------------------------------------------------------------
# Interactions not auto-asserted
# ---------------------------------------------------------------------------


# ESCAPE: test_grpc_interactions_not_auto_asserted
#   CLAIM: gRPC interactions are NOT auto-asserted; they land on timeline unasserted.
#   PATH:  _GrpcCallable.__call__() -> record() called -> no mark_asserted().
#   CHECK: timeline.all_unasserted() contains the interaction.
#   MUTATION: Auto-asserting in the interceptor means all_unasserted() would be empty.
#   ESCAPE: Nothing reasonable -- exact check on unasserted list.
def test_grpc_interactions_not_auto_asserted(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Do", returns=b"ok")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Do")
        stub(b"req")

    timeline = bigfoot_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "grpc:unary_unary:/pkg.Svc/Do"
    assert interactions[0].details == {
        "method": "/pkg.Svc/Do",
        "call_type": "unary_unary",
        "request": b"req",
        "metadata": None,
    }
    # Assert it so verify_all() at teardown succeeds
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Do",
        request=b"req",
        metadata=None,
    )


# ---------------------------------------------------------------------------
# FIFO queue ordering
# ---------------------------------------------------------------------------


# ESCAPE: test_fifo_queue_ordering
#   CLAIM: Multiple mocks for the same method are consumed in FIFO order.
#   PATH:  Two mock_unary_unary calls -> two stubs -> first returns first mock, second returns second.
#   CHECK: Results match FIFO order.
#   MUTATION: LIFO or random order fails the equality checks.
#   ESCAPE: Nothing reasonable.
def test_fifo_queue_ordering(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Do", returns=b"first")
    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Do", returns=b"second")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Do")
        r1 = stub(b"req1")
        r2 = stub(b"req2")

    assert r1 == b"first"
    assert r2 == b"second"
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Do",
        request=b"req1",
        metadata=None,
    )
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Do",
        request=b"req2",
        metadata=None,
    )


# ESCAPE: test_unmocked_after_queue_exhausted
#   CLAIM: After consuming all mocks, the next call raises UnmockedInteractionError.
#   PATH:  queue empty after consumption -> raise.
#   CHECK: UnmockedInteractionError raised on the second call.
#   MUTATION: Not raising allows unlimited calls.
#   ESCAPE: Nothing reasonable.
def test_unmocked_after_queue_exhausted(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Do", returns=b"only-one")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Do")
        stub(b"req1")  # consumes the mock
        with pytest.raises(UnmockedInteractionError):
            stub(b"req2")

    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Do",
        request=b"req1",
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Metadata passed through
# ---------------------------------------------------------------------------


# ESCAPE: test_metadata_passed_through
#   CLAIM: Metadata passed to the callable is recorded in interaction details.
#   PATH:  _GrpcCallable.__call__(request, metadata=...) -> records metadata in details.
#   CHECK: Asserted metadata matches what was passed.
#   MUTATION: Not recording metadata means assertion fails.
#   ESCAPE: Nothing reasonable.
def test_metadata_passed_through(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    meta = (("authorization", "Bearer token"),)
    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Auth", returns=b"ok")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Auth")
        stub(b"req", metadata=meta)

    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Auth",
        request=b"req",
        metadata=(("authorization", "Bearer token"),),
    )


# ---------------------------------------------------------------------------
# Secure channel
# ---------------------------------------------------------------------------


# ESCAPE: test_secure_channel_interception
#   CLAIM: grpc.secure_channel is also intercepted, not just insecure_channel.
#   PATH:  activate() patches both; secure_channel returns _FakeChannel too.
#   CHECK: Call through secure_channel works identically.
#   MUTATION: Not patching secure_channel means call fails.
#   ESCAPE: Nothing reasonable.
def test_secure_channel_interception(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Secure", returns=b"secure-ok")
    with bigfoot.sandbox():
        creds = grpc.ssl_channel_credentials()
        channel = grpc.secure_channel("localhost:443", creds)
        stub = channel.unary_unary("/pkg.Svc/Secure")
        result = stub(b"req")

    assert result == b"secure-ok"
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Secure",
        request=b"req",
        metadata=None,
    )


# ---------------------------------------------------------------------------
# Format methods
# ---------------------------------------------------------------------------


# ESCAPE: test_format_interaction
#   CLAIM: format_interaction produces the expected one-line summary.
#   PATH:  format_interaction() reads details and formats string.
#   CHECK: Exact string equality.
#   MUTATION: Wrong format string fails equality.
#   ESCAPE: Nothing reasonable -- exact string comparison.
def test_format_interaction() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="grpc:unary_unary:/pkg.Svc/Do",
        sequence=0,
        details={
            "method": "/pkg.Svc/Do",
            "call_type": "unary_unary",
            "request": b"data",
            "metadata": None,
        },
        plugin=p,
    )
    assert p.format_interaction(interaction) == (
        "[GrpcPlugin] unary_unary /pkg.Svc/Do"
    )


# ESCAPE: test_format_mock_hint
#   CLAIM: format_mock_hint produces copy-pasteable mock registration code.
#   PATH:  format_mock_hint() reads details and formats string.
#   CHECK: Exact string equality.
#   MUTATION: Wrong format fails equality.
#   ESCAPE: Nothing reasonable.
def test_format_mock_hint() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="grpc:unary_unary:/pkg.Svc/Do",
        sequence=0,
        details={
            "method": "/pkg.Svc/Do",
            "call_type": "unary_unary",
            "request": b"data",
            "metadata": None,
        },
        plugin=p,
    )
    assert p.format_mock_hint(interaction) == (
        "    bigfoot.grpc_mock.mock_unary_unary('/pkg.Svc/Do', returns=...)"
    )


# ESCAPE: test_format_unmocked_hint
#   CLAIM: format_unmocked_hint produces a helpful error message with mock registration code.
#   PATH:  format_unmocked_hint() formats from source_id.
#   CHECK: Exact string equality.
#   MUTATION: Wrong format fails equality.
#   ESCAPE: Nothing reasonable.
def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint(
        "grpc:unary_unary:/pkg.Svc/Do",
        (),
        {},
    )
    assert result == (
        "grpc.unary_unary('/pkg.Svc/Do') was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    bigfoot.grpc_mock.mock_unary_unary('/pkg.Svc/Do', returns=...)"
    )


# ESCAPE: test_format_assert_hint
#   CLAIM: format_assert_hint produces copy-pasteable assertion code.
#   PATH:  format_assert_hint() reads details and formats string.
#   CHECK: Exact string equality.
#   MUTATION: Wrong format fails equality.
#   ESCAPE: Nothing reasonable.
def test_format_assert_hint() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="grpc:unary_unary:/pkg.Svc/Do",
        sequence=0,
        details={
            "method": "/pkg.Svc/Do",
            "call_type": "unary_unary",
            "request": b"data",
            "metadata": None,
        },
        plugin=p,
    )
    assert p.format_assert_hint(interaction) == (
        "    bigfoot.grpc_mock.assert_unary_unary(\n"
        "        method='/pkg.Svc/Do',\n"
        "        request=b'data',\n"
        "        metadata=None,\n"
        "    )"
    )


# ESCAPE: test_format_unused_mock_hint
#   CLAIM: format_unused_mock_hint produces a message about the unused mock.
#   PATH:  format_unused_mock_hint() reads config attributes and formats string.
#   CHECK: Output starts with expected prefix and contains traceback.
#   MUTATION: Wrong format fails equality.
#   ESCAPE: Nothing reasonable.
def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = GrpcMockConfig(
        method="/pkg.Svc/Do",
        call_type="unary_unary",
        returns=b"val",
    )
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "grpc.unary_unary('/pkg.Svc/Do') was mocked (required=True) but never called.\n"
        "Registered at:\n"
    )
    # The traceback part is dynamic, but the prefix is deterministic
    assert result.startswith(expected_prefix)
    # Traceback lines follow
    remaining = result[len(expected_prefix):]
    assert remaining.startswith("  File ")


# ---------------------------------------------------------------------------
# matches() method
# ---------------------------------------------------------------------------


# ESCAPE: test_matches_field_comparison
#   CLAIM: matches() returns True when all expected fields equal actual fields.
#   PATH:  matches() iterates expected keys and compares values.
#   CHECK: True for matching, False for non-matching.
#   MUTATION: Always returning True fails the False check.
#   ESCAPE: Nothing reasonable.
def test_matches_field_comparison() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="grpc:unary_unary:/pkg.Svc/Do",
        sequence=0,
        details={
            "method": "/pkg.Svc/Do",
            "call_type": "unary_unary",
            "request": b"data",
            "metadata": None,
        },
        plugin=p,
    )
    # Matching
    assert p.matches(
        interaction,
        {"method": "/pkg.Svc/Do", "call_type": "unary_unary", "request": b"data", "metadata": None},
    ) is True
    # Non-matching
    assert p.matches(
        interaction,
        {"method": "/pkg.Svc/WRONG", "call_type": "unary_unary", "request": b"data", "metadata": None},
    ) is False


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.grpc_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_grpc_mock_proxy_works
#   CLAIM: bigfoot.grpc_mock.mock_unary_unary() works when verifier is active.
#   PATH:  _GrpcProxy.__getattr__("mock_unary_unary") -> get verifier ->
#          find/create GrpcPlugin -> return plugin.mock_unary_unary.
#   CHECK: The proxy call does not raise and the mock is registered and consumed.
#   MUTATION: Returning None instead of the plugin fails with AttributeError.
#   ESCAPE: Nothing reasonable.
def test_grpc_mock_proxy_works(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Proxy", returns=b"proxy-ok")
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")
        stub = channel.unary_unary("/pkg.Svc/Proxy")
        result = stub(b"req")

    assert result == b"proxy-ok"
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Proxy",
        request=b"req",
        metadata=None,
    )


# ESCAPE: test_grpc_mock_proxy_raises_outside_context
#   CLAIM: Accessing grpc_mock outside a test context raises NoActiveVerifierError.
#   PATH:  _GrpcProxy.__getattr__() -> _get_test_verifier_or_raise() -> raises.
#   CHECK: The appropriate error is raised.
#   MUTATION: Not checking for active verifier allows access.
#   ESCAPE: Nothing reasonable.
def test_grpc_mock_proxy_raises_outside_context() -> None:
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    # Ensure no verifier is active
    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Do", returns=b"val")
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# GrpcPlugin in __all__
# ---------------------------------------------------------------------------


# ESCAPE: test_grpc_plugin_in_all
#   CLAIM: GrpcPlugin and grpc_mock are exported in bigfoot.__all__.
#   PATH:  __init__.py __all__ list.
#   CHECK: Both names are in __all__.
#   MUTATION: Removing from __all__ fails the membership check.
#   ESCAPE: Nothing reasonable.
def test_grpc_plugin_in_all() -> None:
    import bigfoot

    assert "GrpcPlugin" in bigfoot.__all__
    assert "grpc_mock" in bigfoot.__all__


# ---------------------------------------------------------------------------
# Separate queues per call_type
# ---------------------------------------------------------------------------


# ESCAPE: test_separate_queues_per_call_type
#   CLAIM: Mocks for different call types on the same method use separate queues.
#   PATH:  Queue key is f"{call_type}:{method}" so different call_types are separate.
#   CHECK: Each call consumes from its own queue.
#   MUTATION: Sharing queues means first call exhausts the wrong mock.
#   ESCAPE: Nothing reasonable.
def test_separate_queues_per_call_type(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.grpc_mock.mock_unary_unary("/pkg.Svc/Multi", returns=b"unary-resp")
    bigfoot.grpc_mock.mock_unary_stream("/pkg.Svc/Multi", returns=[b"stream-resp"])
    with bigfoot.sandbox():
        channel = grpc.insecure_channel("localhost:50051")

        unary_stub = channel.unary_unary("/pkg.Svc/Multi")
        r1 = unary_stub(b"req1")

        stream_stub = channel.unary_stream("/pkg.Svc/Multi")
        r2 = list(stream_stub(b"req2"))

    assert r1 == b"unary-resp"
    assert r2 == [b"stream-resp"]
    bigfoot.grpc_mock.assert_unary_unary(
        method="/pkg.Svc/Multi",
        request=b"req1",
        metadata=None,
    )
    bigfoot.grpc_mock.assert_unary_stream(
        method="/pkg.Svc/Multi",
        request=b"req2",
        metadata=None,
    )


# ---------------------------------------------------------------------------
# matches() exception-swallowing
# ---------------------------------------------------------------------------


# ESCAPE: test_matches_returns_false_on_eq_exception
#   CLAIM: matches() returns False (not propagates) when __eq__ raises an exception.
#   PATH:  matches() -> expected_val != actual_val -> __eq__ raises -> except -> return False.
#   CHECK: matches() returns False.
#   MUTATION: Removing the except block propagates the error instead of returning False.
#   ESCAPE: Nothing reasonable -- exact boolean check.
def test_matches_returns_false_on_eq_exception() -> None:
    from bigfoot._timeline import Interaction

    class BrokenEq:
        def __eq__(self, other: object) -> bool:
            raise TypeError("comparison exploded")

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="grpc:unary_unary:/pkg.Svc/Do",
        sequence=0,
        details={
            "method": "/pkg.Svc/Do",
            "call_type": "unary_unary",
            "request": b"data",
            "metadata": None,
        },
        plugin=p,
    )
    result = p.matches(
        interaction,
        {"method": BrokenEq(), "call_type": "unary_unary", "request": b"data", "metadata": None},
    )
    assert result is False


# ---------------------------------------------------------------------------
# _get_grpc_plugin() RuntimeError when no GrpcPlugin registered
# ---------------------------------------------------------------------------


# ESCAPE: test_get_grpc_plugin_raises_without_grpc_plugin
#   CLAIM: _get_grpc_plugin() raises RuntimeError when no GrpcPlugin is on the active verifier.
#   PATH:  _get_grpc_plugin() -> iterates verifier._plugins -> none is GrpcPlugin -> raise RuntimeError.
#   CHECK: RuntimeError raised with exact message.
#   MUTATION: Not raising means the function returns None or wrong plugin.
#   ESCAPE: Nothing reasonable -- exact message comparison.
def test_get_grpc_plugin_raises_without_grpc_plugin() -> None:
    from bigfoot._context import _active_verifier
    from bigfoot.plugins.grpc_plugin import _get_grpc_plugin

    v = StrictVerifier()
    # Remove all GrpcPlugin instances from the verifier's plugin list
    v._plugins = [p for p in v._plugins if not isinstance(p, GrpcPlugin)]
    token = _active_verifier.set(v)
    try:
        with pytest.raises(RuntimeError) as exc_info:
            _get_grpc_plugin()
        assert str(exc_info.value) == (
            "BUG: bigfoot GrpcPlugin interceptor is active but no "
            "GrpcPlugin is registered on the current verifier."
        )
    finally:
        _active_verifier.reset(token)
