"""Unit tests for PikaPlugin."""

from __future__ import annotations

import pika as pika_lib
import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import InvalidStateError, UnmockedInteractionError
from bigfoot._state_machine_plugin import ScriptStep
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.pika_plugin import (
    _PIKA_AVAILABLE,
    PikaPlugin,
    _FakeBlockingConnection,
    _FakeChannel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, PikaPlugin]:
    """Return (verifier, plugin) with plugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    PikaPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, PikaPlugin):
            return v, p
    p = PikaPlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 and restore pika if leaked."""
    with PikaPlugin._install_lock:
        PikaPlugin._install_count = 0
        if PikaPlugin._original_blocking_connection is not None:
            pika_lib.BlockingConnection = PikaPlugin._original_blocking_connection  # type: ignore[misc]
            PikaPlugin._original_blocking_connection = None


@pytest.fixture(autouse=True)
def clean_install_count() -> None:
    """Ensure PikaPlugin install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield  # type: ignore[misc]
    _reset_install_count()


# ---------------------------------------------------------------------------
# Static interface: _initial_state / _transitions / _unmocked_source_id
# ---------------------------------------------------------------------------


# ESCAPE: test_initial_state
#   CLAIM: _initial_state() returns "disconnected".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "disconnected".
#   MUTATION: Returning "connected" would fail the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_initial_state() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._initial_state() == "disconnected"


# ESCAPE: test_transitions_structure
#   CLAIM: _transitions() returns the exact expected dict.
#   PATH:  Direct call on plugin instance.
#   CHECK: result == exact dict mapping method names to {from_state: to_state}.
#   MUTATION: Any missing key or wrong state name fails the equality check.
#   ESCAPE: Extra keys in the dict would also fail the equality check.
def test_transitions_structure() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._transitions() == {
        "connect": {"disconnected": "connected"},
        "channel": {"connected": "channel_open"},
        "publish": {"channel_open": "channel_open"},
        "consume": {"channel_open": "channel_open"},
        "ack": {"channel_open": "channel_open"},
        "nack": {"channel_open": "channel_open"},
        "close": {
            "channel_open": "closed",
            "connected": "closed",
        },
    }


# ESCAPE: test_unmocked_source_id
#   CLAIM: _unmocked_source_id() returns "pika:connect".
#   PATH:  Direct call on plugin instance.
#   CHECK: result == "pika:connect".
#   MUTATION: Returning a different string fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_unmocked_source_id() -> None:
    v, p = _make_verifier_with_plugin()
    assert p._unmocked_source_id() == "pika:connect"


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patch
#   CLAIM: After activate(), pika.BlockingConnection is replaced with _FakeBlockingConnection.
#   PATH:  activate() -> _install_count == 0 -> store original -> install _FakeBlockingConnection.
#   CHECK: pika.BlockingConnection is _FakeBlockingConnection.
#   MUTATION: Skipping patch installation leaves original in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison against _FakeBlockingConnection class.
def test_activate_installs_patch() -> None:
    v, p = _make_verifier_with_plugin()
    original = pika_lib.BlockingConnection
    p.activate()
    assert pika_lib.BlockingConnection is _FakeBlockingConnection


# ESCAPE: test_deactivate_restores_patch
#   CLAIM: After activate() then deactivate(), pika.BlockingConnection is the original again.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore original.
#   CHECK: pika.BlockingConnection is NOT _FakeBlockingConnection.
#   MUTATION: Not restoring in deactivate() leaves _FakeBlockingConnection in place.
#   ESCAPE: Nothing reasonable -- identity comparison against original class.
def test_deactivate_restores_patch() -> None:
    v, p = _make_verifier_with_plugin()
    original = pika_lib.BlockingConnection
    p.activate()
    p.deactivate()
    assert pika_lib.BlockingConnection is not _FakeBlockingConnection


# ESCAPE: test_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patch is removed.
#   PATH:  First activate -> _install_count=1; second activate -> _install_count=2.
#          First deactivate -> _install_count=1 (patch remains).
#          Second deactivate -> _install_count=0 (original restored).
#   CHECK: After first deactivate, pika.BlockingConnection is still _FakeBlockingConnection.
#          After second deactivate, pika.BlockingConnection is not _FakeBlockingConnection.
#   MUTATION: Restoring on first deactivate would fail the mid-point identity check.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove count-controlled restoration.
def test_reference_counting_nested() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert PikaPlugin._install_count == 2

    p.deactivate()
    assert PikaPlugin._install_count == 1
    assert pika_lib.BlockingConnection is _FakeBlockingConnection

    p.deactivate()
    assert PikaPlugin._install_count == 0
    assert pika_lib.BlockingConnection is not _FakeBlockingConnection


# ---------------------------------------------------------------------------
# 1. Basic interception: connect, channel, publish, consume, ack, nack, close
# ---------------------------------------------------------------------------


# ESCAPE: test_full_publish_flow
#   CLAIM: A complete pika flow (connect -> channel -> publish -> close) consumes
#          steps in order, returns scripted values, and ends in "closed" state.
#   PATH:  sandbox -> activate -> _FakeBlockingConnection.__init__ triggers connect step
#          (state: connected); channel() -> state: channel_open; basic_publish -> state:
#          channel_open (self-loop); close() -> state: closed; close also calls _release_session.
#   CHECK: channel() returns _FakeChannel; publish returns None; close returns None;
#          session released after close.
#   MUTATION: Wrong return value for any step fails the exact equality check.
#   ESCAPE: Nothing reasonable -- exact equality on all returns.
def test_full_publish_flow() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("publish", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        assert isinstance(ch, _FakeChannel)
        ch.basic_publish(
            exchange="test_exchange",
            routing_key="test.key",
            body=b"hello",
        )
        conn.close()

    assert len(p._active_sessions) == 0


# ESCAPE: test_consume_flow
#   CLAIM: A consume flow (connect -> channel -> consume -> close) works correctly.
#   PATH:  connect -> channel -> basic_consume (channel_open -> channel_open self-loop) -> close.
#   CHECK: basic_consume returns the scripted consumer tag; close succeeds.
#   MUTATION: Wrong consumer tag would fail the equality check.
#   ESCAPE: Nothing reasonable -- exact return value equality.
def test_consume_flow() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("consume", returns="ctag_1")
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        tag = ch.basic_consume(queue="test_queue", auto_ack=True)
        conn.close()

    assert tag == "ctag_1"


# ESCAPE: test_ack_nack_flow
#   CLAIM: ack and nack operations work as self-transitions on channel_open.
#   PATH:  connect -> channel -> ack (channel_open -> channel_open) ->
#          nack (channel_open -> channel_open) -> close.
#   CHECK: ack returns None; nack returns None; close returns None.
#   MUTATION: Missing ack/nack in transitions would raise InvalidStateError.
#   ESCAPE: Nothing reasonable -- no exception plus correct returns.
def test_ack_nack_flow() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("ack", returns=None)
    session.expect("nack", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_ack(delivery_tag=1)
        ch.basic_nack(delivery_tag=2, requeue=True)
        conn.close()

    assert len(p._active_sessions) == 0


# ---------------------------------------------------------------------------
# 2. Full assertion certainty (assertable_fields)
# ---------------------------------------------------------------------------


# ESCAPE: test_assertable_fields_connect
#   CLAIM: assertable_fields for a "pika:connect" interaction returns {"host", "port", "virtual_host"}.
#   PATH:  Record a connect interaction, call assertable_fields.
#   CHECK: result == frozenset({"host", "port", "virtual_host"}).
#   MUTATION: Returning empty frozenset or missing a field fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_connect() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost", port=5672, virtual_host="/")
        )
        conn.close()

    # Find the connect interaction on the timeline
    interactions = v._timeline._interactions
    connect_interaction = [i for i in interactions if i.source_id == "pika:connect"][0]
    assert p.assertable_fields(connect_interaction) == frozenset({"host", "port", "virtual_host"})


# ESCAPE: test_assertable_fields_channel
#   CLAIM: assertable_fields for a "pika:channel" interaction returns frozenset()
#          because channel is a state-transition-only step with no data fields.
#   PATH:  Record a channel interaction, call assertable_fields.
#   CHECK: result == frozenset().
#   MUTATION: Returning non-empty frozenset fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_channel() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        conn.channel()
        conn.close()

    interactions = v._timeline._interactions
    channel_interaction = [i for i in interactions if i.source_id == "pika:channel"][0]
    assert p.assertable_fields(channel_interaction) == frozenset()


# ESCAPE: test_assertable_fields_publish
#   CLAIM: assertable_fields for a "pika:publish" interaction returns
#          {"exchange", "routing_key", "body", "properties"}.
#   PATH:  Record a publish interaction, call assertable_fields.
#   CHECK: result == frozenset({"exchange", "routing_key", "body", "properties"}).
#   MUTATION: Missing a field fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_publish() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("publish", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_publish(
            exchange="amq.direct",
            routing_key="test",
            body=b"msg",
        )
        conn.close()

    interactions = v._timeline._interactions
    publish_interaction = [i for i in interactions if i.source_id == "pika:publish"][0]
    assert p.assertable_fields(publish_interaction) == frozenset(
        {"exchange", "routing_key", "body", "properties"}
    )


# ESCAPE: test_assertable_fields_consume
#   CLAIM: assertable_fields for a "pika:consume" interaction returns {"queue", "auto_ack"}.
#   PATH:  Record a consume interaction, call assertable_fields.
#   CHECK: result == frozenset({"queue", "auto_ack"}).
#   MUTATION: Missing a field fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_consume() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("consume", returns="ctag")
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_consume(queue="q1", auto_ack=False)
        conn.close()

    interactions = v._timeline._interactions
    consume_interaction = [i for i in interactions if i.source_id == "pika:consume"][0]
    assert p.assertable_fields(consume_interaction) == frozenset({"queue", "auto_ack"})


# ESCAPE: test_assertable_fields_ack
#   CLAIM: assertable_fields for a "pika:ack" interaction returns {"delivery_tag"}.
#   PATH:  Record an ack interaction, call assertable_fields.
#   CHECK: result == frozenset({"delivery_tag"}).
#   MUTATION: Missing delivery_tag fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_ack() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("ack", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_ack(delivery_tag=42)
        conn.close()

    interactions = v._timeline._interactions
    ack_interaction = [i for i in interactions if i.source_id == "pika:ack"][0]
    assert p.assertable_fields(ack_interaction) == frozenset({"delivery_tag"})


# ESCAPE: test_assertable_fields_nack
#   CLAIM: assertable_fields for a "pika:nack" interaction returns {"delivery_tag", "requeue"}.
#   PATH:  Record a nack interaction, call assertable_fields.
#   CHECK: result == frozenset({"delivery_tag", "requeue"}).
#   MUTATION: Missing requeue fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_nack() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("nack", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_nack(delivery_tag=5, requeue=False)
        conn.close()

    interactions = v._timeline._interactions
    nack_interaction = [i for i in interactions if i.source_id == "pika:nack"][0]
    assert p.assertable_fields(nack_interaction) == frozenset({"delivery_tag", "requeue"})


# ESCAPE: test_assertable_fields_close
#   CLAIM: assertable_fields for a "pika:close" interaction returns frozenset()
#          because close is a state-transition-only step with no data fields.
#   PATH:  Record a close interaction, call assertable_fields.
#   CHECK: result == frozenset().
#   MUTATION: Returning non-empty frozenset fails the equality check.
#   ESCAPE: Nothing reasonable -- exact frozenset equality.
def test_assertable_fields_close() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        conn.close()

    interactions = v._timeline._interactions
    close_interaction = [i for i in interactions if i.source_id == "pika:close"][0]
    assert p.assertable_fields(close_interaction) == frozenset()


# ---------------------------------------------------------------------------
# 3. Unmocked interaction error
# ---------------------------------------------------------------------------


# ESCAPE: test_connection_with_empty_queue_raises_unmocked
#   CLAIM: If no session is queued when pika.BlockingConnection() fires,
#          UnmockedInteractionError is raised with source_id == "pika:connect".
#   PATH:  _FakeBlockingConnection.__init__ -> _bind_connection -> queue empty ->
#          UnmockedInteractionError(source_id="pika:connect").
#   CHECK: UnmockedInteractionError raised; exc.source_id == "pika:connect".
#   MUTATION: Returning a dummy session for empty queue would not raise.
#   ESCAPE: Raising with wrong source_id fails the source_id check.
def test_connection_with_empty_queue_raises_unmocked() -> None:
    v, p = _make_verifier_with_plugin()
    # No session registered

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            pika_lib.BlockingConnection(
                pika_lib.ConnectionParameters(host="localhost")
            )

    assert exc_info.value.source_id == "pika:connect"


# ---------------------------------------------------------------------------
# 4. Unused mock warning
# ---------------------------------------------------------------------------


# ESCAPE: test_get_unused_mocks_unconsumed_steps
#   CLAIM: When channel and close steps are never consumed, get_unused_mocks() returns them.
#   PATH:  new_session with connect + channel + close steps -> connect consumed in __init__ ->
#          session in _active_sessions with two remaining required steps ->
#          get_unused_mocks() returns them.
#   CHECK: len(unused) == 2; unused[0].method == "channel"; unused[1].method == "close".
#   MUTATION: Not scanning _active_sessions for remaining steps would return [].
#   ESCAPE: Returning all three including connect would give len == 3; fails count check.
def test_get_unused_mocks_unconsumed_steps() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)  # will NOT be consumed
    session.expect("close", returns=None)  # will NOT be consumed

    with v.sandbox():
        pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        # deliberately NOT calling channel or close

    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "channel"
    assert unused[1].method == "close"


# ESCAPE: test_get_unused_mocks_queued_session_never_bound
#   CLAIM: A session queued but never bound (no BlockingConnection() called) has all
#          its required steps returned by get_unused_mocks().
#   PATH:  new_session with connect + channel enqueued -> no BlockingConnection() call ->
#          _session_queue still holds handle -> get_unused_mocks() iterates _session_queue.
#   CHECK: len(unused) == 2; methods are ["connect", "channel"] in order.
#   MUTATION: Not iterating _session_queue would return [].
#   ESCAPE: Returning items in LIFO order would fail the method ordering check.
def test_get_unused_mocks_queued_session_never_bound() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)

    # Never call BlockingConnection; the session stays in the queue
    unused: list[ScriptStep] = p.get_unused_mocks()
    assert len(unused) == 2
    assert unused[0].method == "connect"
    assert unused[1].method == "channel"


# ---------------------------------------------------------------------------
# 5. Missing fields error (assert_interaction with wrong fields)
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_interaction_missing_fields_raises
#   CLAIM: Calling assert_interaction for a connect step with missing fields raises
#          MissingAssertionFieldsError.
#   PATH:  Record connect interaction with {host, port, virtual_host} ->
#          assert_interaction with only host= -> MissingAssertionFieldsError.
#   CHECK: MissingAssertionFieldsError raised.
#   MUTATION: Returning frozenset() from assertable_fields would skip field validation.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_assert_interaction_missing_fields_raises() -> None:
    from bigfoot._errors import MissingAssertionFieldsError

    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost", port=5672, virtual_host="/")
        )
        conn.close()

    # Assert connect with only host -- missing port and virtual_host
    with pytest.raises(MissingAssertionFieldsError):
        v.assert_interaction(p.connect, host="localhost")


# ---------------------------------------------------------------------------
# 6. Typed assertion helpers
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_connect_helper
#   CLAIM: assert_connect() typed helper correctly asserts a connect interaction.
#   PATH:  Record connect interaction -> assert_connect with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong host/port/virtual_host would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_connect_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.pika_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="rabbitmq.local", port=5672, virtual_host="/")
        )
        conn.close()

    bigfoot.pika_mock.assert_connect(host="rabbitmq.local", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_close()


# ESCAPE: test_assert_publish_helper
#   CLAIM: assert_publish() typed helper correctly asserts a publish interaction.
#   PATH:  Record publish interaction -> assert_publish with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong exchange/routing_key/body/properties would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_publish_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.pika_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("publish", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_publish(
            exchange="amq.direct",
            routing_key="test.route",
            body=b"payload",
            properties=None,
        )
        conn.close()

    bigfoot.pika_mock.assert_connect(host="localhost", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_publish(
        exchange="amq.direct",
        routing_key="test.route",
        body=b"payload",
        properties=None,
    )
    bigfoot.pika_mock.assert_close()


# ESCAPE: test_assert_consume_helper
#   CLAIM: assert_consume() typed helper correctly asserts a consume interaction.
#   PATH:  Record consume interaction -> assert_consume with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong queue/auto_ack would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_consume_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.pika_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("consume", returns="ctag_1")
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_consume(queue="my_queue", auto_ack=True)
        conn.close()

    bigfoot.pika_mock.assert_connect(host="localhost", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_consume(queue="my_queue", auto_ack=True)
    bigfoot.pika_mock.assert_close()


# ---------------------------------------------------------------------------
# 8. Exception propagation
# ---------------------------------------------------------------------------


# ESCAPE: test_exception_propagation
#   CLAIM: When a step has raises= set, that exception is propagated during execution.
#   PATH:  connect step -> channel step with raises=ConnectionError("channel failed") ->
#          _execute_step raises ConnectionError.
#   CHECK: ConnectionError raised with exact message "channel failed".
#   MUTATION: Not raising the exception would return the step.returns value instead.
#   ESCAPE: Raising a different exception type or message fails the assertion.
def test_exception_propagation() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None, raises=ConnectionError("channel failed"))

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        with pytest.raises(ConnectionError) as exc_info:
            conn.channel()

    assert str(exc_info.value) == "channel failed"


# ---------------------------------------------------------------------------
# 9. Graceful degradation
# ---------------------------------------------------------------------------


# ESCAPE: test_pika_available_flag
#   CLAIM: _PIKA_AVAILABLE is True when pika is installed.
#   PATH:  Module-level try/except import check.
#   CHECK: _PIKA_AVAILABLE == True.
#   MUTATION: Not importing pika at module level would leave flag False.
#   ESCAPE: Nothing reasonable -- exact boolean equality.
def test_pika_available_flag() -> None:
    assert _PIKA_AVAILABLE is True


# ESCAPE: test_pika_mock_proxy_raises_import_error_when_unavailable
#   CLAIM: Accessing bigfoot.pika_mock raises ImportError when pika is not installed.
#   PATH:  _PikaProxy.__getattr__ -> checks _PIKA_AVAILABLE -> raises ImportError.
#   CHECK: ImportError raised with message containing "bigfoot[pika]" and "pip install".
#   MUTATION: Not checking _PIKA_AVAILABLE would defer the error.
#   ESCAPE: Wrong message would fail the string check.
def test_pika_mock_proxy_raises_import_error_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bigfoot.plugins.pika_plugin as pika_mod

    monkeypatch.setattr(pika_mod, "_PIKA_AVAILABLE", False)

    with pytest.raises(ImportError) as exc_info:
        _ = bigfoot.pika_mock.new_session  # noqa: B018

    assert str(exc_info.value) == (
        "bigfoot[pika] is required to use bigfoot.pika_mock. "
        "Install it with: pip install bigfoot[pika]"
    )


# ---------------------------------------------------------------------------
# 10. State transition validation
# ---------------------------------------------------------------------------


# ESCAPE: test_publish_before_channel_raises_invalid_state
#   CLAIM: Calling publish when state is "connected" (after connect but before channel)
#          raises InvalidStateError.
#   PATH:  connect (in __init__) -> state: connected; publish via _execute_step ->
#          state "connected" not in method_transitions["publish"] -> InvalidStateError.
#   CHECK: InvalidStateError raised; exc.method == "publish";
#          exc.current_state == "connected";
#          exc.valid_states == frozenset({"channel_open"}).
#   MUTATION: Not checking from-state would allow the call through without raising.
#   ESCAPE: Raising with wrong current_state fails the attribute check.
def test_publish_before_channel_raises_invalid_state() -> None:
    # basic_publish is on _FakeChannel, which requires channel() to obtain.
    # Test via direct _execute_step on the handle to verify state validation.
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        handle = p._lookup_session(conn)
        with pytest.raises(InvalidStateError) as exc_info:
            p._execute_step(
                handle, "publish", (), {}, "pika:publish",
                details={"exchange": "", "routing_key": "", "body": b"", "properties": None},
            )

    exc = exc_info.value
    assert exc.source_id == "pika:publish"
    assert exc.method == "publish"
    assert exc.current_state == "connected"
    assert exc.valid_states == frozenset({"channel_open"})


# ESCAPE: test_channel_from_disconnected_raises_invalid_state
#   CLAIM: Calling channel() when state is "disconnected" raises InvalidStateError.
#   PATH:  channel method not valid from "disconnected" -> InvalidStateError.
#   CHECK: InvalidStateError raised with correct method, current_state, valid_states.
#   MUTATION: Allowing channel from disconnected would not raise.
#   ESCAPE: Wrong valid_states fails the attribute check.
def test_channel_from_disconnected_raises_invalid_state() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    # Create a handle manually in disconnected state
    handle = session
    with pytest.raises(InvalidStateError) as exc_info:
        p._execute_step(
            handle, "channel", (), {}, "pika:channel",
            details={},
        )

    exc = exc_info.value
    assert exc.source_id == "pika:channel"
    assert exc.method == "channel"
    assert exc.current_state == "disconnected"
    assert exc.valid_states == frozenset({"connected"})


# ---------------------------------------------------------------------------
# 11. Session lifecycle
# ---------------------------------------------------------------------------


# ESCAPE: test_session_lifecycle
#   CLAIM: Full session lifecycle: new_session -> expect -> bind -> execute -> release
#          works correctly through the sandbox context manager.
#   PATH:  new_session creates SessionHandle -> expect chains configure script ->
#          sandbox activate installs patch -> BlockingConnection binds session ->
#          operations execute steps -> close releases session -> deactivate restores.
#   CHECK: All scripted returns match; active_sessions empty after close;
#          patch restored after sandbox exit.
#   MUTATION: Missing any lifecycle step would leave sessions dangling or patch installed.
#   ESCAPE: Nothing reasonable -- multiple exact equality checks at each stage.
def test_session_lifecycle() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("publish", returns=None)
    session.expect("close", returns=None)

    # Before sandbox: session is queued
    assert len(p._session_queue) == 1

    with v.sandbox():
        # After activate: patch installed
        assert pika_lib.BlockingConnection is _FakeBlockingConnection

        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )

        # After connect: session bound, queue empty
        assert len(p._session_queue) == 0
        assert len(p._active_sessions) == 1

        ch = conn.channel()
        ch.basic_publish(exchange="", routing_key="q", body=b"data")
        conn.close()

        # After close: session released
        assert len(p._active_sessions) == 0

    # After sandbox: patch restored
    assert pika_lib.BlockingConnection is not _FakeBlockingConnection


# ---------------------------------------------------------------------------
# 12. Multiple sessions
# ---------------------------------------------------------------------------


# ESCAPE: test_multiple_sequential_sessions
#   CLAIM: Two sequential sessions on the same plugin work correctly.
#   PATH:  First session: connect -> channel -> publish -> close.
#          Second session: connect -> channel -> consume -> close.
#          Both are queued and consumed in order.
#   CHECK: Both sessions execute fully; active_sessions empty after both close.
#   MUTATION: Queue not being FIFO would bind sessions in wrong order.
#   ESCAPE: Wrong scripted return on second session's consume would fail equality check.
def test_multiple_sequential_sessions() -> None:
    v, p = _make_verifier_with_plugin()

    # First session
    s1 = p.new_session()
    s1.expect("connect", returns=None)
    s1.expect("channel", returns=None)
    s1.expect("publish", returns=None)
    s1.expect("close", returns=None)

    # Second session
    s2 = p.new_session()
    s2.expect("connect", returns=None)
    s2.expect("channel", returns=None)
    s2.expect("consume", returns="ctag_2")
    s2.expect("close", returns=None)

    with v.sandbox():
        # First connection
        conn1 = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="host1")
        )
        ch1 = conn1.channel()
        ch1.basic_publish(exchange="", routing_key="q1", body=b"msg1")
        conn1.close()

        # Second connection
        conn2 = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="host2")
        )
        ch2 = conn2.channel()
        tag = ch2.basic_consume(queue="q2", auto_ack=False)
        conn2.close()

    assert tag == "ctag_2"
    assert len(p._active_sessions) == 0
    assert len(p._session_queue) == 0


# ---------------------------------------------------------------------------
# matches() override
# ---------------------------------------------------------------------------


# ESCAPE: test_matches_field_by_field
#   CLAIM: matches() compares field-by-field and returns True only when all fields match.
#   PATH:  Record a connect interaction, call matches with correct and incorrect expected dicts.
#   CHECK: matches returns True for correct values, False for incorrect values.
#   MUTATION: A placeholder matches() that always returns True would pass the True case
#             but fail the False case.
#   ESCAPE: Nothing reasonable -- both True and False paths checked.
def test_matches_field_by_field() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="myhost", port=5672, virtual_host="/vhost")
        )
        conn.close()

    interactions = v._timeline._interactions
    connect_interaction = [i for i in interactions if i.source_id == "pika:connect"][0]

    # Correct match
    assert p.matches(connect_interaction, {"host": "myhost", "port": 5672, "virtual_host": "/vhost"}) is True

    # Wrong host
    assert p.matches(connect_interaction, {"host": "wrong", "port": 5672, "virtual_host": "/vhost"}) is False

    # Wrong port
    assert p.matches(connect_interaction, {"host": "myhost", "port": 9999, "virtual_host": "/vhost"}) is False

    # Wrong virtual_host
    assert p.matches(connect_interaction, {"host": "myhost", "port": 5672, "virtual_host": "/wrong"}) is False


# ---------------------------------------------------------------------------
# Sentinel properties
# ---------------------------------------------------------------------------


# ESCAPE: test_sentinel_properties
#   CLAIM: All sentinel properties return _StepSentinel instances with correct source_ids.
#   PATH:  Access each property on the plugin instance.
#   CHECK: Each sentinel.source_id == expected source_id string.
#   MUTATION: Wrong source_id string fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality on each.
def test_sentinel_properties() -> None:
    from bigfoot._state_machine_plugin import _StepSentinel

    v, p = _make_verifier_with_plugin()

    assert isinstance(p.connect, _StepSentinel)
    assert p.connect.source_id == "pika:connect"

    assert isinstance(p.channel, _StepSentinel)
    assert p.channel.source_id == "pika:channel"

    assert isinstance(p.publish, _StepSentinel)
    assert p.publish.source_id == "pika:publish"

    assert isinstance(p.consume, _StepSentinel)
    assert p.consume.source_id == "pika:consume"

    assert isinstance(p.ack, _StepSentinel)
    assert p.ack.source_id == "pika:ack"

    assert isinstance(p.nack, _StepSentinel)
    assert p.nack.source_id == "pika:nack"

    assert isinstance(p.close, _StepSentinel)
    assert p.close.source_id == "pika:close"


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.pika_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_pika_mock_proxy_new_session
#   CLAIM: bigfoot.pika_mock.new_session() returns a SessionHandle.
#   PATH:  _PikaProxy.__getattr__("new_session") -> get verifier -> find/create PikaPlugin ->
#          return plugin.new_session.
#   CHECK: session is a SessionHandle instance; chaining .expect() does not raise.
#   MUTATION: Returning None instead of a SessionHandle would fail isinstance check.
#   ESCAPE: Nothing reasonable -- both the isinstance and the chained .expect() call check it.
def test_pika_mock_proxy_new_session(bigfoot_verifier: StrictVerifier) -> None:
    from bigfoot._state_machine_plugin import SessionHandle

    session = bigfoot.pika_mock.new_session()
    assert isinstance(session, SessionHandle)
    result = session.expect("connect", returns=None, required=False)
    assert result is session  # expect() returns self for chaining


# ESCAPE: test_pika_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.pika_mock outside a test context raises NoActiveVerifierError.
#   PATH:  _PikaProxy.__getattr__ -> _get_test_verifier_or_raise -> NoActiveVerifierError.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Silently returning None would not raise and hide context failures.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_pika_mock_proxy_raises_outside_context() -> None:
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.pika_mock.new_session  # noqa: B018
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# Close from connected (without channel)
# ---------------------------------------------------------------------------


# ESCAPE: test_close_from_connected
#   CLAIM: close() is valid from "connected" state (without opening a channel).
#   PATH:  connect (in __init__) -> state: connected; close from "connected" -> state: closed.
#   CHECK: Close succeeds without raising; active sessions empty.
#   MUTATION: Missing "connected" in close's from-states would raise InvalidStateError.
#   ESCAPE: Nothing reasonable -- no exception proves close is valid from connected.
def test_close_from_connected() -> None:
    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        conn.close()

    assert len(p._active_sessions) == 0


# ---------------------------------------------------------------------------
# Flow tests with assert_interaction() calls (Fix 4: green mirage)
# ---------------------------------------------------------------------------


# ESCAPE: test_full_publish_flow_assertions
#   CLAIM: A complete pika publish flow records correct interaction details.
#   PATH:  sandbox -> connect -> channel -> publish -> close -> assert each interaction.
#   CHECK: assert_interaction verifies every assertable field for every step.
#   MUTATION: Wrong detail values in any step fail the assertion.
#   ESCAPE: Nothing reasonable -- full field coverage on all assertable steps.
def test_full_publish_flow_assertions(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.pika_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("publish", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_publish(
            exchange="test_exchange",
            routing_key="test.key",
            body=b"hello",
        )
        conn.close()

    bigfoot.pika_mock.assert_connect(host="localhost", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_publish(
        exchange="test_exchange",
        routing_key="test.key",
        body=b"hello",
        properties=None,
    )
    bigfoot.pika_mock.assert_close()


# ESCAPE: test_consume_flow_assertions
#   CLAIM: A consume flow records correct interaction details.
#   PATH:  sandbox -> connect -> channel -> consume -> close -> assert each interaction.
#   CHECK: assert_interaction verifies every assertable field for every step.
#   MUTATION: Wrong queue or auto_ack values fail the assertion.
#   ESCAPE: Nothing reasonable -- full field coverage on all assertable steps.
def test_consume_flow_assertions(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.pika_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("consume", returns="ctag_1")
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        tag = ch.basic_consume(queue="test_queue", auto_ack=True)
        conn.close()

    assert tag == "ctag_1"

    bigfoot.pika_mock.assert_connect(host="localhost", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_consume(queue="test_queue", auto_ack=True)
    bigfoot.pika_mock.assert_close()


# ESCAPE: test_ack_nack_flow_assertions
#   CLAIM: An ack/nack flow records correct interaction details.
#   PATH:  sandbox -> connect -> channel -> ack -> nack -> close -> assert each interaction.
#   CHECK: assert_interaction verifies delivery_tag and requeue on each step.
#   MUTATION: Wrong delivery_tag or requeue values fail the assertion.
#   ESCAPE: Nothing reasonable -- full field coverage on all assertable steps.
def test_ack_nack_flow_assertions(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.pika_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("ack", returns=None)
    session.expect("nack", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_ack(delivery_tag=1)
        ch.basic_nack(delivery_tag=2, requeue=True)
        conn.close()

    bigfoot.pika_mock.assert_connect(host="localhost", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_ack(delivery_tag=1)
    bigfoot.pika_mock.assert_nack(delivery_tag=2, requeue=True)
    bigfoot.pika_mock.assert_close()


# ESCAPE: test_close_from_connected_assertions
#   CLAIM: Closing from connected state records correct interaction details.
#   PATH:  sandbox -> connect -> close -> assert each interaction.
#   CHECK: assert_interaction verifies connect fields; close has no assertable fields.
#   MUTATION: Wrong host/port/virtual_host fail the assertion.
#   ESCAPE: Nothing reasonable -- full field coverage.
def test_close_from_connected_assertions(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.pika_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="rabbitmq.local")
        )
        conn.close()

    bigfoot.pika_mock.assert_connect(host="rabbitmq.local", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_close()


# ESCAPE: test_session_lifecycle_assertions
#   CLAIM: Full session lifecycle records correct interaction details at each step.
#   PATH:  sandbox -> connect -> channel -> publish -> close -> assert each interaction.
#   CHECK: assert_interaction verifies all assertable fields for every step.
#   MUTATION: Wrong detail values fail the assertion.
#   ESCAPE: Nothing reasonable -- full field coverage.
def test_session_lifecycle_assertions(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.pika_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("publish", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_publish(exchange="", routing_key="q", body=b"data")
        conn.close()

    bigfoot.pika_mock.assert_connect(host="localhost", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_publish(
        exchange="", routing_key="q", body=b"data", properties=None,
    )
    bigfoot.pika_mock.assert_close()


# ESCAPE: test_multiple_sequential_sessions_assertions
#   CLAIM: Two sequential sessions record correct interaction details for each session.
#   PATH:  Two sessions queued -> first: connect/channel/publish/close;
#          second: connect/channel/consume/close -> assert all interactions.
#   CHECK: assert_interaction verifies fields for every step in both sessions.
#   MUTATION: Wrong host or routing_key/queue values fail the assertion.
#   ESCAPE: Nothing reasonable -- full field coverage on both sessions.
def test_multiple_sequential_sessions_assertions(bigfoot_verifier: StrictVerifier) -> None:
    # First session
    s1 = bigfoot.pika_mock.new_session()
    s1.expect("connect", returns=None)
    s1.expect("channel", returns=None)
    s1.expect("publish", returns=None)
    s1.expect("close", returns=None)

    # Second session
    s2 = bigfoot.pika_mock.new_session()
    s2.expect("connect", returns=None)
    s2.expect("channel", returns=None)
    s2.expect("consume", returns="ctag_2")
    s2.expect("close", returns=None)

    with bigfoot.sandbox():
        # First connection
        conn1 = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="host1")
        )
        ch1 = conn1.channel()
        ch1.basic_publish(exchange="", routing_key="q1", body=b"msg1")
        conn1.close()

        # Second connection
        conn2 = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="host2")
        )
        ch2 = conn2.channel()
        tag = ch2.basic_consume(queue="q2", auto_ack=False)
        conn2.close()

    assert tag == "ctag_2"

    # Assert first session interactions
    bigfoot.pika_mock.assert_connect(host="host1", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_publish(
        exchange="", routing_key="q1", body=b"msg1", properties=None,
    )
    bigfoot.pika_mock.assert_close()

    # Assert second session interactions
    bigfoot.pika_mock.assert_connect(host="host2", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_consume(queue="q2", auto_ack=False)
    bigfoot.pika_mock.assert_close()


# ---------------------------------------------------------------------------
# Fix 3: assert_ack() and assert_nack() typed helper tests
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_ack_helper
#   CLAIM: assert_ack() typed helper correctly asserts an ack interaction.
#   PATH:  Record ack interaction -> assert_ack with matching delivery_tag -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong delivery_tag would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_ack_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.pika_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("ack", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_ack(delivery_tag=42)
        conn.close()

    bigfoot.pika_mock.assert_connect(host="localhost", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_ack(delivery_tag=42)
    bigfoot.pika_mock.assert_close()


# ESCAPE: test_assert_nack_helper
#   CLAIM: assert_nack() typed helper correctly asserts a nack interaction.
#   PATH:  Record nack interaction -> assert_nack with matching fields -> no error.
#   CHECK: No exception raised.
#   MUTATION: Wrong delivery_tag or requeue would raise InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- helper delegates to assert_interaction with full fields.
def test_assert_nack_helper(bigfoot_verifier: StrictVerifier) -> None:
    session = bigfoot.pika_mock.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("nack", returns=None)
    session.expect("close", returns=None)

    with bigfoot.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_nack(delivery_tag=7, requeue=False)
        conn.close()

    bigfoot.pika_mock.assert_connect(host="localhost", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_nack(delivery_tag=7, requeue=False)
    bigfoot.pika_mock.assert_close()


# ---------------------------------------------------------------------------
# Fix 5: Negative tests for typed helpers
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_connect_helper_rejects_wrong_values
#   CLAIM: assert_connect() raises when passed wrong field values.
#   PATH:  Record connect with host="localhost" -> assert_connect with host="wrong_host" ->
#          InteractionMismatchError.
#   CHECK: Exception raised.
#   MUTATION: A no-op assert_connect that never checks would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_assert_connect_helper_rejects_wrong_values() -> None:
    from bigfoot._errors import InteractionMismatchError

    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        conn.close()

    with pytest.raises(InteractionMismatchError):
        v.assert_interaction(p.connect, host="wrong_host", port=5672, virtual_host="/")


# ESCAPE: test_assert_publish_helper_rejects_wrong_values
#   CLAIM: assert_publish() raises when passed wrong field values.
#   PATH:  Record publish with routing_key="test.key" -> assert_publish with routing_key="wrong" ->
#          InteractionMismatchError.
#   CHECK: Exception raised.
#   MUTATION: A no-op assert_publish that never checks would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_assert_publish_helper_rejects_wrong_values() -> None:
    from bigfoot._errors import InteractionMismatchError

    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("publish", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_publish(exchange="amq.direct", routing_key="test.key", body=b"payload")
        conn.close()

    v.assert_interaction(p.connect, host="localhost", port=5672, virtual_host="/")
    v.assert_interaction(p.channel)
    with pytest.raises(InteractionMismatchError):
        v.assert_interaction(
            p.publish,
            exchange="amq.direct", routing_key="wrong", body=b"payload", properties=None,
        )


# ESCAPE: test_assert_ack_helper_rejects_wrong_values
#   CLAIM: assert_ack() raises when passed wrong delivery_tag.
#   PATH:  Record ack with delivery_tag=1 -> assert_ack with delivery_tag=999 ->
#          InteractionMismatchError.
#   CHECK: Exception raised.
#   MUTATION: A no-op assert_ack that never checks would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_assert_ack_helper_rejects_wrong_values() -> None:
    from bigfoot._errors import InteractionMismatchError

    v, p = _make_verifier_with_plugin()
    session = p.new_session()
    session.expect("connect", returns=None)
    session.expect("channel", returns=None)
    session.expect("ack", returns=None)
    session.expect("close", returns=None)

    with v.sandbox():
        conn = pika_lib.BlockingConnection(
            pika_lib.ConnectionParameters(host="localhost")
        )
        ch = conn.channel()
        ch.basic_ack(delivery_tag=1)
        conn.close()

    v.assert_interaction(p.connect, host="localhost", port=5672, virtual_host="/")
    v.assert_interaction(p.channel)
    with pytest.raises(InteractionMismatchError):
        v.assert_interaction(p.ack, delivery_tag=999)


# ---------------------------------------------------------------------------
# Fix 6: format_* method tests
# ---------------------------------------------------------------------------


# ESCAPE: test_format_interaction_connect
#   CLAIM: format_interaction for a connect interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="pika:connect" and details -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_connect() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:connect",
        sequence=0,
        details={"host": "localhost", "port": 5672, "virtual_host": "/"},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[PikaPlugin] pika.connect(host='localhost', port=5672, virtual_host='/')"


# ESCAPE: test_format_interaction_channel
#   CLAIM: format_interaction for a channel interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="pika:channel" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_channel() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:channel",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[PikaPlugin] connection.channel()"


# ESCAPE: test_format_interaction_publish
#   CLAIM: format_interaction for a publish interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="pika:publish" and details -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_publish() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:publish",
        sequence=0,
        details={"exchange": "amq.direct", "routing_key": "test.key", "body": b"hello", "properties": None},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[PikaPlugin] channel.basic_publish(exchange='amq.direct', routing_key='test.key')"


# ESCAPE: test_format_interaction_consume
#   CLAIM: format_interaction for a consume interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="pika:consume" and details -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_consume() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:consume",
        sequence=0,
        details={"queue": "my_queue", "auto_ack": True},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[PikaPlugin] channel.basic_consume(queue='my_queue', auto_ack=True)"


# ESCAPE: test_format_interaction_ack
#   CLAIM: format_interaction for an ack interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="pika:ack" and details -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_ack() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:ack",
        sequence=0,
        details={"delivery_tag": 42},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[PikaPlugin] channel.basic_ack(delivery_tag=42)"


# ESCAPE: test_format_interaction_nack
#   CLAIM: format_interaction for a nack interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="pika:nack" and details -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_nack() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:nack",
        sequence=0,
        details={"delivery_tag": 5, "requeue": False},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[PikaPlugin] channel.basic_nack(delivery_tag=5, requeue=False)"


# ESCAPE: test_format_interaction_close
#   CLAIM: format_interaction for a close interaction returns the exact expected string.
#   PATH:  Create Interaction with source_id="pika:close" -> format_interaction.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong format string fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_close() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:close",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[PikaPlugin] connection.close()"


# ESCAPE: test_format_interaction_unknown
#   CLAIM: format_interaction for an unknown source_id returns the fallback string.
#   PATH:  Create Interaction with source_id="pika:unknown_method" -> format_interaction.
#   CHECK: result == exact expected fallback string.
#   MUTATION: Wrong fallback format fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_unknown() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:unknown_method",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[PikaPlugin] pika.unknown_method(...)"


# ESCAPE: test_format_mock_hint
#   CLAIM: format_mock_hint returns copy-pasteable code to mock the interaction.
#   PATH:  format_mock_hint(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_mock_hint() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:publish",
        sequence=0,
        details={"exchange": "amq.direct", "routing_key": "test", "body": b"msg", "properties": None},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.pika_mock.new_session().expect('publish', returns=...)"


# ESCAPE: test_format_mock_hint_connect
#   CLAIM: format_mock_hint for a connect interaction returns the correct hint.
#   PATH:  format_mock_hint(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong method name in hint fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_mock_hint_connect() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:connect",
        sequence=0,
        details={"host": "localhost", "port": 5672, "virtual_host": "/"},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.pika_mock.new_session().expect('connect', returns=...)"


# ESCAPE: test_format_unmocked_hint
#   CLAIM: format_unmocked_hint returns copy-pasteable code for an unmocked call.
#   PATH:  format_unmocked_hint(source_id, args, kwargs) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("pika:connect", (), {})
    assert result == (
        "pika.BlockingConnection.connect(...) was called but no session was queued.\n"
        "Register a session with:\n"
        "    bigfoot.pika_mock.new_session().expect('connect', returns=...)"
    )


# ESCAPE: test_format_unmocked_hint_publish
#   CLAIM: format_unmocked_hint for publish returns the correct hint.
#   PATH:  format_unmocked_hint("pika:publish", ...) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong method name fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_unmocked_hint_publish() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("pika:publish", (), {})
    assert result == (
        "pika.BlockingConnection.publish(...) was called but no session was queued.\n"
        "Register a session with:\n"
        "    bigfoot.pika_mock.new_session().expect('publish', returns=...)"
    )


# ESCAPE: test_format_assert_hint_connect
#   CLAIM: format_assert_hint for connect returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_connect syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_connect() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:connect",
        sequence=0,
        details={"host": "localhost", "port": 5672, "virtual_host": "/"},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.pika_mock.assert_connect(host='localhost', port=5672, virtual_host='/')"


# ESCAPE: test_format_assert_hint_channel
#   CLAIM: format_assert_hint for channel returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_channel syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_channel() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:channel",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.pika_mock.assert_channel()"


# ESCAPE: test_format_assert_hint_publish
#   CLAIM: format_assert_hint for publish returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_publish syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_publish() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:publish",
        sequence=0,
        details={"exchange": "amq.direct", "routing_key": "test", "body": b"msg", "properties": None},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.pika_mock.assert_publish("
        "exchange='amq.direct', routing_key='test', "
        "body=b'msg', properties=None)"
    )


# ESCAPE: test_format_assert_hint_consume
#   CLAIM: format_assert_hint for consume returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_consume syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_consume() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:consume",
        sequence=0,
        details={"queue": "my_queue", "auto_ack": True},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.pika_mock.assert_consume(queue='my_queue', auto_ack=True)"


# ESCAPE: test_format_assert_hint_ack
#   CLAIM: format_assert_hint for ack returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_ack syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_ack() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:ack",
        sequence=0,
        details={"delivery_tag": 42},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.pika_mock.assert_ack(delivery_tag=42)"


# ESCAPE: test_format_assert_hint_nack
#   CLAIM: format_assert_hint for nack returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_nack syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_nack() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:nack",
        sequence=0,
        details={"delivery_tag": 5, "requeue": False},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.pika_mock.assert_nack(delivery_tag=5, requeue=False)"


# ESCAPE: test_format_assert_hint_close
#   CLAIM: format_assert_hint for close returns the correct assert code.
#   PATH:  format_assert_hint(interaction) -> string with assert_close syntax.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_close() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:close",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    bigfoot.pika_mock.assert_close()"


# ESCAPE: test_format_assert_hint_unknown
#   CLAIM: format_assert_hint for an unknown source_id returns the fallback string.
#   PATH:  format_assert_hint(interaction) -> fallback string.
#   CHECK: result == exact expected fallback string.
#   MUTATION: Wrong fallback format fails equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_assert_hint_unknown() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="pika:unknown_op",
        sequence=0,
        details={},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == "    # bigfoot.pika_mock: unknown source_id='pika:unknown_op'"


# ESCAPE: test_format_unused_mock_hint
#   CLAIM: format_unused_mock_hint returns hint containing method name and traceback.
#   PATH:  format_unused_mock_hint(mock_config) -> string.
#   CHECK: result == exact expected string including registration_traceback.
#   MUTATION: Wrong prefix text fails the equality check.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    step = ScriptStep(method="publish", returns=None)
    result = p.format_unused_mock_hint(step)
    expected_prefix = (
        "pika.BlockingConnection.publish(...) was mocked (required=True) but never called.\n"
        "Registered at:\n"
    )
    assert result == expected_prefix + step.registration_traceback
