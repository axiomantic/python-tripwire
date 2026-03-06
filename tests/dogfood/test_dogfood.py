"""Dogfood tests: bigfoot uses itself to test bigfoot.

These tests exercise bigfoot's own internals via bigfoot's MockPlugin and
HttpPlugin, validating production-style usage rather than isolated unit behavior.
"""

import pytest
from dirty_equals import AnyThing

import bigfoot
from bigfoot import (
    MockPlugin,
    UnassertedInteractionsError,
    UnmockedInteractionError,
    UnusedMocksError,
)
from bigfoot._mock_plugin import MockProxy

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 1. MockPlugin records and asserts a collaborator interaction
# ---------------------------------------------------------------------------


def test_mock_plugin_records_and_asserts_collaborator_interaction() -> None:
    """Use bigfoot.mock() to mock a collaborator, verify interaction recorded and asserted."""
    service_proxy = bigfoot.mock("PaymentService")
    service_proxy.charge.returns({"status": "ok", "id": "ch_001"})

    with bigfoot.sandbox() as v:
        result = service_proxy.charge("order_99", amount=500)

    assert result == {"status": "ok", "id": "ch_001"}
    bigfoot.assert_interaction(
        service_proxy.charge,
        args=("order_99",),
        kwargs={"amount": 500},
    )
    # verify_all() is called automatically at teardown by _bigfoot_auto_verifier


# ---------------------------------------------------------------------------
# 2. Multiple mock calls asserted in FIFO order
# ---------------------------------------------------------------------------


def test_multiple_calls_asserted_in_fifo_order() -> None:
    """Multiple side effects consumed in FIFO order, each interaction asserted."""
    counter_proxy = bigfoot.mock("Counter")
    counter_proxy.tick.returns(1).returns(2).returns(3)

    with bigfoot.sandbox():
        first = counter_proxy.tick()
        second = counter_proxy.tick()
        third = counter_proxy.tick()

    assert first == 1
    assert second == 2
    assert third == 3
    bigfoot.assert_interaction(
        counter_proxy.tick,
        args=(),
        kwargs={},
    )
    bigfoot.assert_interaction(
        counter_proxy.tick,
        args=(),
        kwargs={},
    )
    bigfoot.assert_interaction(
        counter_proxy.tick,
        args=(),
        kwargs={},
    )


# ---------------------------------------------------------------------------
# 3. UnmockedInteractionError exact message content
# ---------------------------------------------------------------------------


def test_unmocked_interaction_error_exact_message() -> None:
    """UnmockedInteractionError.__str__() includes source_id, args, kwargs, hint."""
    proxy = bigfoot.mock("DataStore")

    with bigfoot.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            proxy.fetch("user_id_123")

    err = exc_info.value
    assert err.source_id == "mock:DataStore.fetch"
    assert err.args_tuple == ("user_id_123",)
    assert err.kwargs == {}

    expected_hint = (
        "Unexpected call to DataStore.fetch\n\n"
        "  Called with: args=('user_id_123',), kwargs={}\n\n"
        "  To mock this interaction, add before your sandbox:\n"
        '    verifier.mock("DataStore").fetch.returns(<value>)\n\n'
        "  Or to mark it optional:\n"
        '    verifier.mock("DataStore").fetch.required(False).returns(<value>)'
    )
    assert err.hint == expected_hint

    expected_str = (
        f"UnmockedInteractionError: source_id='mock:DataStore.fetch', "
        f"args=('user_id_123',), kwargs={{}}, "
        f"hint={expected_hint!r}"
    )
    assert str(err) == expected_str


# ---------------------------------------------------------------------------
# 4. UnassertedInteractionsError at teardown
# ---------------------------------------------------------------------------


def test_unasserted_interactions_error_at_teardown() -> None:
    """verify_all() raises UnassertedInteractionsError when interaction is not asserted."""
    proxy = bigfoot.mock("Logger")
    proxy.log.returns(None)

    with bigfoot.sandbox():
        proxy.log("event_happened")
        # Deliberately skip assert_interaction

    with pytest.raises(UnassertedInteractionsError) as exc_info:
        bigfoot.verify_all()

    err = exc_info.value
    assert len(err.interactions) == 1
    assert err.interactions[0].source_id == "mock:Logger.log"
    assert err.interactions[0].sequence == 0

    # Assert the interaction so the auto-verifier teardown does not raise again
    bigfoot.assert_interaction(proxy.log, args=("event_happened",), kwargs={})


# ---------------------------------------------------------------------------
# 5. UnusedMocksError at teardown
# ---------------------------------------------------------------------------


def test_unused_mocks_error_at_teardown() -> None:
    """verify_all() raises UnusedMocksError when a required mock is never called."""
    proxy = bigfoot.mock("Emailer")
    proxy.send_email.returns(True)

    with bigfoot.sandbox():
        pass  # Never call send_email

    with pytest.raises(UnusedMocksError) as exc_info:
        bigfoot.verify_all()

    err = exc_info.value
    assert len(err.mocks) == 1
    assert err.mocks[0].mock_name == "Emailer"
    assert err.mocks[0].method_name == "send_email"

    # hint contains dynamic registration traceback; verify structure, not exact traceback text
    assert err.hint.startswith("1 mock(s) were registered but never triggered\n")
    assert "mock:Emailer.send_email" in err.hint
    assert "Mock registered at:" in err.hint
    assert "Remove this mock if it's not needed" in err.hint

    expected_str_prefix = "UnusedMocksError: 1 unused mock(s), hint="
    assert str(err).startswith(expected_str_prefix)
    assert str(err) == f"UnusedMocksError: 1 unused mock(s), hint={err.hint!r}"

    # Mark the mock as not required so the auto-verifier teardown does not raise
    # The mock_config is already in err.mocks; we need to drain the queue.
    # The simplest approach: call the method inside a sandbox to consume the config.
    # But the config was already popped from err via verify_all(). Since verify_all()
    # raises before modifying state, the config is still in the queue. We must mark
    # required=False on the MethodProxy to suppress the second verify_all() at teardown.
    # Access the method proxy directly via the verifier's plugin.
    verifier = bigfoot.current_verifier()
    for plugin in verifier._plugins:
        if isinstance(plugin, MockPlugin):
            for prx in plugin._proxies.values():
                methods = object.__getattribute__(prx, "_methods")
                for method_proxy in methods.values():
                    for config in method_proxy._config_queue:
                        config.required = False


# ---------------------------------------------------------------------------
# 6. required(False) suppresses UnusedMocksError
# ---------------------------------------------------------------------------


def test_required_false_suppresses_unused_mocks_error() -> None:
    """Mocks registered with required=False do not cause UnusedMocksError."""
    proxy = bigfoot.mock("Cache")
    proxy.get.required(False).returns(None)

    with bigfoot.sandbox():
        pass  # Never call get

    # verify_all() is called automatically at teardown by _bigfoot_auto_verifier -- must not raise


# ---------------------------------------------------------------------------
# 7. .raises() side effect is recorded and must be asserted
# ---------------------------------------------------------------------------


def test_raises_side_effect_is_recorded_and_assertable() -> None:
    """Interaction from .raises() is recorded in the timeline and must be asserted."""
    proxy = bigfoot.mock("Database")
    proxy.connect.raises(ConnectionError("db down"))

    with bigfoot.sandbox():
        with pytest.raises(ConnectionError, match="db down"):
            proxy.connect()

    bigfoot.assert_interaction(
        proxy.connect,
        args=(),
        kwargs={},
    )


# ---------------------------------------------------------------------------
# 8. .calls() side effect delegates to fn and is recorded
# ---------------------------------------------------------------------------


def test_calls_side_effect_delegates_to_fn() -> None:
    """Interaction from .calls() invokes the function with forwarded args."""
    proxy = bigfoot.mock("Calculator")
    proxy.add.calls(lambda x, y: x + y)

    with bigfoot.sandbox():
        result = proxy.add(3, 4)

    assert result == 7
    bigfoot.assert_interaction(
        proxy.add,
        args=(3, 4),
        kwargs={},
    )


# ---------------------------------------------------------------------------
# 9. in_any_order allows assertions regardless of call order
# ---------------------------------------------------------------------------


def test_in_any_order_allows_out_of_order_assertions() -> None:
    """in_any_order() allows assertions in any sequence across two mock proxies."""
    email_proxy = bigfoot.mock("Email")
    sms_proxy = bigfoot.mock("SMS")
    email_proxy.send.returns(True)
    sms_proxy.send.returns(True)

    with bigfoot.sandbox():
        sms_proxy.send("hello")
        email_proxy.send("world")

    # Assert out-of-order: email first, then sms -- both occurred in reverse order
    with bigfoot.in_any_order():
        bigfoot.assert_interaction(
            email_proxy.send,
            args=("world",),
            kwargs={},
        )
        bigfoot.assert_interaction(
            sms_proxy.send,
            args=("hello",),
            kwargs={},
        )


# ---------------------------------------------------------------------------
# 10. bigfoot.mock() lazily creates MockPlugin
# ---------------------------------------------------------------------------


def test_verifier_mock_lazily_creates_mock_plugin() -> None:
    """bigfoot.mock() creates MockPlugin on first call without explicit instantiation."""
    verifier = bigfoot.current_verifier()

    # Before any mock() call, no MockPlugin registered
    assert len(verifier._plugins) == 0

    proxy = bigfoot.mock("Service")

    # After mock(), MockPlugin is registered
    assert len(verifier._plugins) == 1
    assert isinstance(verifier._plugins[0], MockPlugin)
    assert isinstance(proxy, MockProxy)


# ---------------------------------------------------------------------------
# 11. mock() returns same proxy on repeated calls
# ---------------------------------------------------------------------------


def test_get_or_create_proxy_returns_same_instance() -> None:
    """bigfoot.mock() with the same name always returns the identical object."""
    proxy_a = bigfoot.mock("Widget")
    proxy_b = bigfoot.mock("Widget")

    assert proxy_a is proxy_b


# ---------------------------------------------------------------------------
# 12. Async context: MockPlugin works inside an async test
# ---------------------------------------------------------------------------


async def test_mock_plugin_works_in_async_context() -> None:
    """bigfoot MockPlugin correctly records interactions inside an async test."""
    proxy = bigfoot.mock("AsyncService")
    proxy.fetch_data.returns({"value": 42})

    async with bigfoot.sandbox():
        result = proxy.fetch_data("key")

    assert result == {"value": 42}
    bigfoot.assert_interaction(
        proxy.fetch_data,
        args=("key",),
        kwargs={},
    )


# ---------------------------------------------------------------------------
# 13. HttpPlugin: full cycle with httpx (skip if not installed)
# ---------------------------------------------------------------------------


def test_http_plugin_full_cycle_httpx() -> None:
    """Full mock + assert + verify cycle using httpx GET."""
    httpx = pytest.importorskip("httpx")

    bigfoot.http.mock_response(
        "GET",
        "https://api.stripe.com/v1/charges",
        json={"id": "ch_123", "amount": 5000},
        status=200,
    )

    with bigfoot.sandbox():
        response = httpx.get("https://api.stripe.com/v1/charges")

    assert response.status_code == 200
    assert response.json() == {"id": "ch_123", "amount": 5000}
    bigfoot.assert_interaction(
        bigfoot.http.request,
        method="GET",
        url="https://api.stripe.com/v1/charges",
        request_headers=AnyThing(),
        request_body="",
        status=200,
        response_headers=AnyThing(),
        response_body=AnyThing(),
    )
    # _bigfoot_auto_verifier fixture calls verify_all() at teardown


# ---------------------------------------------------------------------------
# 14. MockPlugin and HttpPlugin together: global FIFO order
# ---------------------------------------------------------------------------


def test_mock_and_http_plugins_tracked_in_global_fifo_order() -> None:
    """MockPlugin and HttpPlugin interactions share a global FIFO timeline."""
    httpx = pytest.importorskip("httpx")

    service_proxy = bigfoot.mock("AuthService")
    service_proxy.authenticate.returns({"token": "tok_abc"})

    bigfoot.http.mock_response(
        "POST",
        "https://api.example.com/data",
        json={"created": True},
        status=201,
    )

    with bigfoot.sandbox():
        # Call mock first, then HTTP
        auth_result = service_proxy.authenticate("user_x")
        http_response = httpx.post("https://api.example.com/data", json={})

    assert auth_result == {"token": "tok_abc"}
    assert http_response.status_code == 201
    # Assert in the same FIFO order they were called
    bigfoot.assert_interaction(
        service_proxy.authenticate,
        args=("user_x",),
        kwargs={},
    )
    bigfoot.assert_interaction(
        bigfoot.http.request,
        method="POST",
        url="https://api.example.com/data",
        request_headers=AnyThing(),
        request_body=AnyThing(),
        status=201,
        response_headers=AnyThing(),
        response_body=AnyThing(),
    )


# ---------------------------------------------------------------------------
# 15. spy() records interaction and delegates to real object
# ---------------------------------------------------------------------------


def test_spy_records_and_delegates() -> None:
    """bigfoot.spy() creates a spy that delegates to real implementation and records interaction."""

    class _Calculator:
        def add(self, x: int, y: int) -> int:
            return x + y

    real = _Calculator()
    calc_spy = bigfoot.spy("Calculator", real)

    with bigfoot.sandbox():
        result = calc_spy.add(10, 20)

    assert result == 30
    bigfoot.assert_interaction(calc_spy.add, args=(10, 20), kwargs={})


# ---------------------------------------------------------------------------
# 16. spy() with real method that raises: interaction recorded, exception propagated
# ---------------------------------------------------------------------------


def test_spy_records_when_real_raises() -> None:
    """spy(): if real method raises, interaction is still recorded and exception re-raised."""

    class _Flaky:
        def fetch(self) -> None:
            raise ConnectionError("unreachable")

    real = _Flaky()
    flaky_spy = bigfoot.spy("Flaky", real)

    with bigfoot.sandbox():
        with pytest.raises(ConnectionError, match="unreachable"):
            flaky_spy.fetch()

    bigfoot.assert_interaction(flaky_spy.fetch, args=(), kwargs={})


# ---------------------------------------------------------------------------
# 17. MissingAssertionFieldsError raised when assertable field omitted
# ---------------------------------------------------------------------------


def test_missing_assertion_fields_error_raised_for_mock() -> None:
    """assert_interaction() raises MissingAssertionFieldsError when args/kwargs omitted."""
    from bigfoot import MissingAssertionFieldsError

    proxy = bigfoot.mock("Service")
    proxy.process.returns("done")

    with bigfoot.sandbox():
        proxy.process("input")

    with pytest.raises(MissingAssertionFieldsError) as exc_info:
        bigfoot.assert_interaction(proxy.process)  # missing args and kwargs

    assert "args" in exc_info.value.missing_fields
    assert "kwargs" in exc_info.value.missing_fields

    # Now assert correctly so teardown doesn't raise
    bigfoot.assert_interaction(proxy.process, args=("input",), kwargs={})


# ---------------------------------------------------------------------------
# 18. HTTP pass_through routes request to real backend (mocked at transport level)
# ---------------------------------------------------------------------------


def test_http_pass_through_routes_to_real_backend() -> None:
    """pass_through() routes the matched request to the real transport and records interaction."""
    httpx = pytest.importorskip("httpx")
    from bigfoot.plugins.http import HttpPlugin

    bigfoot.http.pass_through("GET", "https://real-api.example.com/data")

    fake_response = httpx.Response(200, json={"real": True})

    # The patch must be applied INSIDE the sandbox because activate() sets
    # _original_httpx_transport_handle to the real transport. After activate(), we
    # override that saved reference so pass-through uses our fake response.
    # We restore the real original with try/finally to avoid corrupting global state.
    with bigfoot.sandbox():
        real_original = HttpPlugin._original_httpx_transport_handle
        HttpPlugin._original_httpx_transport_handle = lambda transport_self, request: fake_response  # type: ignore[assignment]
        try:
            response = httpx.get("https://real-api.example.com/data")
        finally:
            HttpPlugin._original_httpx_transport_handle = real_original  # type: ignore[assignment]

    assert response.status_code == 200
    bigfoot.assert_interaction(
        bigfoot.http.request,
        method="GET",
        url="https://real-api.example.com/data",
        request_headers=AnyThing(),
        request_body="",
        status=200,
        response_headers=AnyThing(),
        response_body=AnyThing(),
    )
