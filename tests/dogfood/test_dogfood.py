"""Dogfood tests: bigfoot uses itself to test bigfoot.

These tests exercise bigfoot's own internals via bigfoot's MockPlugin and
HttpPlugin, validating production-style usage rather than isolated unit behavior.
"""

import pytest

from bigfoot import (
    MockPlugin,
    StrictVerifier,
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
    """Use MockPlugin to mock a collaborator, verify interaction recorded and asserted."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)

    service_proxy = mock_plugin.get_or_create_proxy("PaymentService")
    service_proxy.charge.returns({"status": "ok", "id": "ch_001"})

    with verifier.sandbox() as v:
        result = service_proxy.charge("order_99", amount=500)
        v.assert_interaction(
            service_proxy.charge,
            method_name="charge",
            args="('order_99',)",
            kwargs="{'amount': 500}",
        )

    assert result == {"status": "ok", "id": "ch_001"}
    verifier.verify_all()


# ---------------------------------------------------------------------------
# 2. Multiple mock calls asserted in FIFO order
# ---------------------------------------------------------------------------


def test_multiple_calls_asserted_in_fifo_order() -> None:
    """Multiple side effects consumed in FIFO order, each interaction asserted."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    counter_proxy = mock_plugin.get_or_create_proxy("Counter")
    counter_proxy.tick.returns(1).returns(2).returns(3)

    with verifier.sandbox() as v:
        first = counter_proxy.tick()
        second = counter_proxy.tick()
        third = counter_proxy.tick()

        v.assert_interaction(
            counter_proxy.tick,
            method_name="tick",
            args="()",
            kwargs="{}",
        )
        v.assert_interaction(
            counter_proxy.tick,
            method_name="tick",
            args="()",
            kwargs="{}",
        )
        v.assert_interaction(
            counter_proxy.tick,
            method_name="tick",
            args="()",
            kwargs="{}",
        )

    assert first == 1
    assert second == 2
    assert third == 3
    verifier.verify_all()


# ---------------------------------------------------------------------------
# 3. UnmockedInteractionError exact message content
# ---------------------------------------------------------------------------


def test_unmocked_interaction_error_exact_message() -> None:
    """UnmockedInteractionError.__str__() includes source_id, args, kwargs, hint."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("DataStore")

    with verifier.sandbox():
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
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("Logger")
    proxy.log.returns(None)

    with verifier.sandbox():
        proxy.log("event_happened")
        # Deliberately skip assert_interaction

    with pytest.raises(UnassertedInteractionsError) as exc_info:
        verifier.verify_all()

    err = exc_info.value
    assert len(err.interactions) == 1
    assert err.interactions[0].source_id == "mock:Logger.log"
    assert err.interactions[0].sequence == 0

    expected_hint = (
        "1 interaction(s) were not asserted\n"
        "\n"
        "  [sequence=0] [MockPlugin] Logger.log\n"
        "    To assert this interaction:\n"
        '      verifier.assert_interaction(verifier.mock("Logger").log)\n'
    )
    assert err.hint == expected_hint

    expected_str = (
        "UnassertedInteractionsError: 1 unasserted interaction(s), "
        f"hint={expected_hint!r}"
    )
    assert str(err) == expected_str


# ---------------------------------------------------------------------------
# 5. UnusedMocksError at teardown
# ---------------------------------------------------------------------------


def test_unused_mocks_error_at_teardown() -> None:
    """verify_all() raises UnusedMocksError when a required mock is never called."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("Emailer")
    proxy.send_email.returns(True)

    with verifier.sandbox():
        pass  # Never call send_email

    with pytest.raises(UnusedMocksError) as exc_info:
        verifier.verify_all()

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


# ---------------------------------------------------------------------------
# 6. required(False) suppresses UnusedMocksError
# ---------------------------------------------------------------------------


def test_required_false_suppresses_unused_mocks_error() -> None:
    """Mocks registered with required=False do not cause UnusedMocksError."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("Cache")
    proxy.get.required(False).returns(None)

    with verifier.sandbox():
        pass  # Never call get

    verifier.verify_all()  # Must not raise


# ---------------------------------------------------------------------------
# 7. .raises() side effect is recorded and must be asserted
# ---------------------------------------------------------------------------


def test_raises_side_effect_is_recorded_and_assertable() -> None:
    """Interaction from .raises() is recorded in the timeline and must be asserted."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("Database")
    proxy.connect.raises(ConnectionError("db down"))

    with verifier.sandbox() as v:
        with pytest.raises(ConnectionError, match="db down"):
            proxy.connect()
        v.assert_interaction(
            proxy.connect,
            method_name="connect",
            args="()",
            kwargs="{}",
        )

    verifier.verify_all()


# ---------------------------------------------------------------------------
# 8. .calls() side effect delegates to fn and is recorded
# ---------------------------------------------------------------------------


def test_calls_side_effect_delegates_to_fn() -> None:
    """Interaction from .calls() invokes the function with forwarded args."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("Calculator")
    proxy.add.calls(lambda x, y: x + y)

    with verifier.sandbox() as v:
        result = proxy.add(3, 4)
        v.assert_interaction(
            proxy.add,
            method_name="add",
            args="(3, 4)",
            kwargs="{}",
        )

    assert result == 7
    verifier.verify_all()


# ---------------------------------------------------------------------------
# 9. in_any_order allows assertions regardless of call order
# ---------------------------------------------------------------------------


def test_in_any_order_allows_out_of_order_assertions() -> None:
    """in_any_order() allows assertions in any sequence across two mock proxies."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    email_proxy = mock_plugin.get_or_create_proxy("Email")
    sms_proxy = mock_plugin.get_or_create_proxy("SMS")
    email_proxy.send.returns(True)
    sms_proxy.send.returns(True)

    with verifier.sandbox():
        sms_proxy.send("hello")
        email_proxy.send("world")

    # Assert out-of-order: email first, then sms -- both occurred in reverse order
    with verifier.in_any_order():
        verifier.assert_interaction(
            email_proxy.send,
            method_name="send",
            args="('world',)",
            kwargs="{}",
        )
        verifier.assert_interaction(
            sms_proxy.send,
            method_name="send",
            args="('hello',)",
            kwargs="{}",
        )

    verifier.verify_all()


# ---------------------------------------------------------------------------
# 10. verifier.mock() lazily creates MockPlugin
# ---------------------------------------------------------------------------


def test_verifier_mock_lazily_creates_mock_plugin() -> None:
    """verifier.mock() creates MockPlugin on first call without explicit instantiation."""
    verifier = StrictVerifier()

    # Before any mock() call, no MockPlugin registered
    assert len(verifier._plugins) == 0

    proxy = verifier.mock("Service")

    # After mock(), MockPlugin is registered
    assert len(verifier._plugins) == 1
    assert isinstance(verifier._plugins[0], MockPlugin)
    assert isinstance(proxy, MockProxy)


# ---------------------------------------------------------------------------
# 11. get_or_create_proxy returns same proxy on repeated calls
# ---------------------------------------------------------------------------


def test_get_or_create_proxy_returns_same_instance() -> None:
    """get_or_create_proxy with the same name always returns the identical object."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)

    proxy_a = mock_plugin.get_or_create_proxy("Widget")
    proxy_b = mock_plugin.get_or_create_proxy("Widget")

    assert proxy_a is proxy_b


# ---------------------------------------------------------------------------
# 12. Async context: MockPlugin works inside an async test
# ---------------------------------------------------------------------------


async def test_mock_plugin_works_in_async_context() -> None:
    """bigfoot MockPlugin correctly records interactions inside an async test."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("AsyncService")
    proxy.fetch_data.returns({"value": 42})

    async with verifier.sandbox() as v:
        result = proxy.fetch_data("key")
        v.assert_interaction(
            proxy.fetch_data,
            method_name="fetch_data",
            args="('key',)",
            kwargs="{}",
        )

    assert result == {"value": 42}
    verifier.verify_all()


# ---------------------------------------------------------------------------
# 13. HttpPlugin: full cycle with httpx (skip if not installed)
# ---------------------------------------------------------------------------


def test_http_plugin_full_cycle_httpx(bigfoot_verifier: StrictVerifier) -> None:
    """Full mock + assert + verify cycle using httpx GET."""
    httpx = pytest.importorskip("httpx")
    from bigfoot.plugins.http import HttpPlugin

    http = HttpPlugin(bigfoot_verifier)
    http.mock_response(
        "GET",
        "https://api.stripe.com/v1/charges",
        json={"id": "ch_123", "amount": 5000},
        status=200,
    )

    with bigfoot_verifier.sandbox():
        response = httpx.get("https://api.stripe.com/v1/charges")
        bigfoot_verifier.assert_interaction(
            http.request,
            method="GET",
            url="https://api.stripe.com/v1/charges",
            status=200,
        )

    assert response.status_code == 200
    assert response.json() == {"id": "ch_123", "amount": 5000}
    # bigfoot_verifier fixture calls verify_all() at teardown


# ---------------------------------------------------------------------------
# 14. MockPlugin and HttpPlugin together: global FIFO order
# ---------------------------------------------------------------------------


def test_mock_and_http_plugins_tracked_in_global_fifo_order(
    bigfoot_verifier: StrictVerifier,
) -> None:
    """MockPlugin and HttpPlugin interactions share a global FIFO timeline."""
    httpx = pytest.importorskip("httpx")
    from bigfoot.plugins.http import HttpPlugin

    mock_plugin = MockPlugin(bigfoot_verifier)
    http = HttpPlugin(bigfoot_verifier)

    service_proxy = mock_plugin.get_or_create_proxy("AuthService")
    service_proxy.authenticate.returns({"token": "tok_abc"})

    http.mock_response(
        "POST",
        "https://api.example.com/data",
        json={"created": True},
        status=201,
    )

    with bigfoot_verifier.sandbox():
        # Call mock first, then HTTP
        auth_result = service_proxy.authenticate("user_x")
        http_response = httpx.post("https://api.example.com/data", json={})

        # Assert in the same FIFO order they were called
        bigfoot_verifier.assert_interaction(
            service_proxy.authenticate,
            method_name="authenticate",
            args="('user_x',)",
            kwargs="{}",
        )
        bigfoot_verifier.assert_interaction(
            http.request,
            method="POST",
            url="https://api.example.com/data",
            status=201,
        )

    assert auth_result == {"token": "tok_abc"}
    assert http_response.status_code == 201
