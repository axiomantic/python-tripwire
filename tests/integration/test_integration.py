"""Integration tests: exercise the full bigfoot system end-to-end.

Each test is self-contained. No real network calls are made.
"""

import asyncio
import concurrent.futures

import pytest

from bigfoot import (
    MockPlugin,
    SandboxNotActiveError,
    StrictVerifier,
    UnassertedInteractionsError,
    UnmockedInteractionError,
    UnusedMocksError,
)
from bigfoot._context import _active_verifier, get_verifier_or_raise

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Scenario 1: Basic mock happy path
# ---------------------------------------------------------------------------


def test_mock_happy_path() -> None:
    """A fully correct mock round-trip: configure -> call -> assert -> verify_all passes."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)

    proxy = mock_plugin.get_or_create_proxy("SomeObject")
    proxy.some_method.returns("expected_result")

    with verifier.sandbox() as v:
        result = proxy.some_method("arg1", kwarg="val")

    assert result == "expected_result"
    v.assert_interaction(
        proxy.some_method,
        method_name="some_method",
        args=("arg1",),
        kwargs={"kwarg": "val"},
    )
    # verify_all must pass: no unasserted interactions, no unused mocks
    verifier.verify_all()


# ---------------------------------------------------------------------------
# Scenario 2: SandboxNotActiveError outside sandbox
# ---------------------------------------------------------------------------


def test_sandbox_not_active_error_raised_outside_sandbox() -> None:
    """Calling get_verifier_or_raise with no active verifier raises SandboxNotActiveError."""
    with pytest.raises(SandboxNotActiveError) as exc_info:
        get_verifier_or_raise("test:source")

    assert exc_info.value.source_id == "test:source"


# ---------------------------------------------------------------------------
# Scenario 3: UnmockedInteractionError
# ---------------------------------------------------------------------------


def test_unmocked_interaction_error_when_no_side_effect_configured() -> None:
    """Calling a mock with no configured side effect raises UnmockedInteractionError."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("MyService")
    # Deliberately do NOT configure any side effect for do_thing

    with verifier.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            proxy.do_thing("x")

    assert exc_info.value.source_id == "mock:MyService.do_thing"
    assert exc_info.value.args_tuple == ("x",)
    assert exc_info.value.kwargs == {}


# ---------------------------------------------------------------------------
# Scenario 4: UnassertedInteractionsError at teardown
# ---------------------------------------------------------------------------


def test_unasserted_interactions_error_at_teardown() -> None:
    """verify_all raises UnassertedInteractionsError when interactions were not asserted."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("Calculator")
    proxy.add.returns(5)

    with verifier.sandbox():
        _ = proxy.add(2, 3)
        # Deliberately do NOT call verifier.assert_interaction(...)

    with pytest.raises(UnassertedInteractionsError) as exc_info:
        verifier.verify_all()

    assert len(exc_info.value.interactions) == 1
    assert exc_info.value.interactions[0].source_id == "mock:Calculator.add"


# ---------------------------------------------------------------------------
# Scenario 5: UnusedMocksError at teardown
# ---------------------------------------------------------------------------


def test_unused_mocks_error_at_teardown() -> None:
    """verify_all raises UnusedMocksError when a required mock was never called."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("Emailer")
    proxy.send.returns(True)
    # Deliberately never call proxy.send inside the sandbox

    with verifier.sandbox():
        pass  # no calls

    with pytest.raises(UnusedMocksError) as exc_info:
        verifier.verify_all()

    assert len(exc_info.value.mocks) == 1
    assert exc_info.value.mocks[0].mock_name == "Emailer"
    assert exc_info.value.mocks[0].method_name == "send"


# ---------------------------------------------------------------------------
# Scenario 6: in_any_order context manager
# ---------------------------------------------------------------------------


def test_in_any_order_allows_out_of_order_assertions() -> None:
    """Assertions within in_any_order() match interactions regardless of call order."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("Cache")
    proxy.get.returns("cached_value")
    proxy.set.returns(None)

    with verifier.sandbox() as v:
        _ = proxy.get("key1")
        proxy.set("key2", "value2")

    # Assert set before get even though get was called first
    with v.in_any_order():
        v.assert_interaction(
            proxy.set,
            method_name="set",
            args=("key2", "value2"),
            kwargs={},
        )
        v.assert_interaction(
            proxy.get,
            method_name="get",
            args=("key1",),
            kwargs={},
        )
    verifier.verify_all()


# ---------------------------------------------------------------------------
# Scenario 7: .required(False) suppresses UnusedMocksError
# ---------------------------------------------------------------------------


def test_required_false_suppresses_unused_mocks_error() -> None:
    """A mock configured with .required(False) does not cause UnusedMocksError at teardown."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("Logger")
    proxy.debug.required(False).returns(None)
    # Never call proxy.debug inside sandbox

    with verifier.sandbox():
        pass

    # verify_all must NOT raise -- optional mock was not called
    verifier.verify_all()


# ---------------------------------------------------------------------------
# Scenario 8: Multiple side effects FIFO order
# ---------------------------------------------------------------------------


def test_multiple_returns_consumed_in_fifo_order() -> None:
    """Multiple .returns() calls are consumed one per call in FIFO order."""
    verifier = StrictVerifier()
    mock_plugin = MockPlugin(verifier)
    proxy = mock_plugin.get_or_create_proxy("Counter")
    proxy.next_value.returns(1).returns(2).returns(3)

    with verifier.sandbox() as v:
        first = proxy.next_value()
        second = proxy.next_value()
        third = proxy.next_value()

    assert first == 1
    assert second == 2
    assert third == 3
    with v.in_any_order():
        v.assert_interaction(proxy.next_value, method_name="next_value", args=(), kwargs={})
        v.assert_interaction(proxy.next_value, method_name="next_value", args=(), kwargs={})
        v.assert_interaction(proxy.next_value, method_name="next_value", args=(), kwargs={})
    verifier.verify_all()


# ---------------------------------------------------------------------------
# Scenario 9: SandboxNotActiveError from direct get_verifier_or_raise call
# ---------------------------------------------------------------------------


def testget_verifier_or_raise_raises_sandbox_not_active_error() -> None:
    """get_verifier_or_raise raises SandboxNotActiveError when no active verifier."""
    with pytest.raises(SandboxNotActiveError) as exc_info:
        get_verifier_or_raise("test:source")

    # Verify exact source_id is preserved
    assert exc_info.value.source_id == "test:source"


# ---------------------------------------------------------------------------
# Scenario 10: HttpPlugin mock response (httpx required)
# ---------------------------------------------------------------------------


def test_http_plugin_mock_response_full_round_trip() -> None:
    """Full httpx round-trip: register mock, call inside sandbox, assert, verify_all passes."""
    httpx = pytest.importorskip("httpx")
    from bigfoot.plugins.http import HttpPlugin

    verifier = StrictVerifier()
    # Retrieve the auto-created HttpPlugin instead of creating a duplicate
    http = next(p for p in verifier._plugins if isinstance(p, HttpPlugin))
    http.mock_response(
        "GET",
        "https://api.example.com/items",
        json={"items": [1, 2, 3]},
        status=200,
    )

    with verifier.sandbox() as v:
        response = httpx.get("https://api.example.com/items")

    assert response.status_code == 200
    assert response.json() == {"items": [1, 2, 3]}
    from dirty_equals import AnyThing

    v.assert_interaction(
        http.request,
        method="GET",
        url="https://api.example.com/items",
        request_headers=AnyThing(),
        request_body=AnyThing(),
        status=200,
        response_headers=AnyThing(),
        response_body=AnyThing(),
    )
    verifier.verify_all()


# ---------------------------------------------------------------------------
# Scenario 11: ConflictError when httpx is already patched by a third party
# ---------------------------------------------------------------------------


def test_conflict_error_raised_when_httpx_already_patched() -> None:
    """HttpPlugin.activate() raises ConflictError if httpx.HTTPTransport.handle_request
    is already patched by a third-party library before HttpPlugin activates.
    """
    httpx = pytest.importorskip("httpx")
    from bigfoot._errors import ConflictError
    from bigfoot.plugins.http import HttpPlugin

    # Save guard fixture state (session fixture may have installed patches)
    saved_count = HttpPlugin._install_count
    saved_handle = httpx.HTTPTransport.handle_request

    # Reset install count so _check_conflicts() runs on next activate()
    with HttpPlugin._install_lock:
        HttpPlugin._install_count = 0

    original = httpx.HTTPTransport.handle_request

    def fake_patch(self: object, request: object) -> None: ...

    httpx.HTTPTransport.handle_request = fake_patch  # type: ignore[method-assign]

    try:
        verifier = StrictVerifier()
        plugin = HttpPlugin(verifier)

        with pytest.raises(ConflictError) as exc_info:
            plugin.activate()

        assert exc_info.value.target == "httpx.HTTPTransport.handle_request"
        assert exc_info.value.patcher == "an unknown library"
    finally:
        # Restore guard fixture state
        httpx.HTTPTransport.handle_request = saved_handle  # type: ignore[method-assign]
        with HttpPlugin._install_lock:
            HttpPlugin._install_count = saved_count


# ---------------------------------------------------------------------------
# Scenario 12: run_in_executor propagates ContextVars via HttpPlugin
# ---------------------------------------------------------------------------


async def test_run_in_executor_propagates_context_var_via_http_plugin() -> None:
    """When HttpPlugin is active, run_in_executor propagates ContextVars to thread pool workers."""
    pytest.importorskip("httpx")
    from bigfoot.plugins.http import HttpPlugin

    sentinel = object()
    token = _active_verifier.set(sentinel)  # type: ignore[arg-type]

    verifier = StrictVerifier()
    plugin = HttpPlugin(verifier)
    plugin.activate()
    try:
        captured: list[object] = []

        def worker() -> None:
            captured.append(_active_verifier.get())

        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            await loop.run_in_executor(pool, worker)

        assert captured == [sentinel]
    finally:
        plugin.deactivate()
        _active_verifier.reset(token)
