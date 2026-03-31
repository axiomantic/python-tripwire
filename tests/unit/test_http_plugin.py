"""Unit tests for bigfoot HttpPlugin.

Tests use unittest.mock.patch to avoid real network calls.
httpx and requests are optional extras -- skip all tests if not installed.
"""

from unittest.mock import MagicMock, patch

import pytest

import bigfoot

httpx = pytest.importorskip("httpx")
requests = pytest.importorskip("requests")
import requests.adapters  # noqa: E402 -- importorskip guarantees requests is available

from bigfoot._base_plugin import BasePlugin
from bigfoot._context import _active_verifier
from bigfoot._errors import ConflictError, SandboxNotActiveError, UnmockedInteractionError
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.http import (
    _HTTPX_ORIGINAL_ASYNC_HANDLE,
    _HTTPX_ORIGINAL_HANDLE,
    _REQUESTS_ORIGINAL_SEND,
    HttpAssertionBuilder,
    HttpPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, HttpPlugin]:
    """Return (verifier, plugin) with plugin registered but not activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    HttpPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, HttpPlugin):
            return v, p
    p = HttpPlugin(v)
    return v, p


def _reset_install_count() -> None:
    """Force-reset the class-level install count to 0 after a test leak."""
    with HttpPlugin._install_lock:
        HttpPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        HttpPlugin.__new__(HttpPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_install_count():
    """Ensure install count starts and ends at 0 for every test."""
    _reset_install_count()
    yield
    _reset_install_count()


# ---------------------------------------------------------------------------
# Test: HttpPlugin is a proper BasePlugin subclass
# ---------------------------------------------------------------------------


# ESCAPE: test_http_plugin_is_base_plugin_subclass
#   CLAIM: HttpPlugin is a subclass of BasePlugin.
#   PATH:  isinstance check against the class.
#   CHECK: isinstance(HttpPlugin(...), BasePlugin) is True.
#   MUTATION: Removing BasePlugin from HttpPlugin's MRO fails isinstance.
#   ESCAPE: Nothing reasonable -- isinstance on class hierarchy is definitive.
def test_http_plugin_is_base_plugin_subclass() -> None:
    v = StrictVerifier()
    p = HttpPlugin(v)
    assert isinstance(p, BasePlugin)


# ESCAPE: test_http_plugin_registers_on_verifier
#   CLAIM: HttpPlugin is auto-instantiated and registered on the verifier's plugin list.
#   PATH:  StrictVerifier.__init__ -> _auto_instantiate_plugins -> HttpPlugin(self) ->
#          BasePlugin.__init__ -> verifier._register_plugin(self).
#   CHECK: Exactly one HttpPlugin instance in v._plugins.
#   MUTATION: Removing auto-instantiation would mean no HttpPlugin registered.
#   ESCAPE: Nothing reasonable -- type check on plugin list.
def test_http_plugin_registers_on_verifier() -> None:
    v = StrictVerifier()
    http_plugins = [p for p in v._plugins if isinstance(p, HttpPlugin)]
    assert len(http_plugins) == 1


# ESCAPE: test_http_plugin_duplicate_is_idempotent
#   CLAIM: Registering a second HttpPlugin on the same verifier silently skips it.
#   PATH:  BasePlugin.__init__ -> StrictVerifier._register_plugin -> type match -> return.
#   CHECK: Plugin count unchanged after attempting duplicate registration.
#   MUTATION: Raising ValueError would break this test.
#   ESCAPE: Nothing reasonable -- exact count comparison.
def test_http_plugin_duplicate_is_idempotent() -> None:
    v = StrictVerifier()
    initial_count = len(v._plugins)
    HttpPlugin(v)  # Should silently skip (already auto-instantiated)
    assert len(v._plugins) == initial_count


# ---------------------------------------------------------------------------
# Test: activate() increments install count; installs patches on first call
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_increments_install_count
#   CLAIM: activate() increments _install_count from 0 to 1.
#   PATH:  activate() -> _install_lock -> _install_count += 1.
#   CHECK: _install_count == 1 after activate().
#   MUTATION: Removing the increment leaves count at 0.
#   ESCAPE: Nothing reasonable -- exact integer equality.
def test_activate_increments_install_count() -> None:
    v, p = _make_verifier_with_plugin()
    assert HttpPlugin._install_count == 0
    p.activate()
    assert HttpPlugin._install_count == 1


# ESCAPE: test_activate_installs_patches_on_first_call
#   CLAIM: First activate() replaces the httpx and requests transport methods.
#   PATH:  activate() -> _install_patches() patches class methods.
#   CHECK: httpx.HTTPTransport.handle_request is not _HTTPX_ORIGINAL_HANDLE.
#   MUTATION: Skipping _install_patches() leaves originals in place.
#   ESCAPE: Nothing reasonable -- identity comparison against import-time constant.
def test_activate_installs_patches_on_first_call() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert httpx.HTTPTransport.handle_request is not _HTTPX_ORIGINAL_HANDLE
    assert httpx.AsyncHTTPTransport.handle_async_request is not _HTTPX_ORIGINAL_ASYNC_HANDLE
    assert requests.adapters.HTTPAdapter.send is not _REQUESTS_ORIGINAL_SEND


# ---------------------------------------------------------------------------
# Test: deactivate() decrements; uninstalls on last call
# ---------------------------------------------------------------------------


# ESCAPE: test_deactivate_decrements_install_count
#   CLAIM: deactivate() after activate() brings count back to 0.
#   PATH:  deactivate() -> _install_lock -> _install_count -= 1.
#   CHECK: _install_count == 0 after activate()/deactivate().
#   MUTATION: Not decrementing leaves count at 1.
#   ESCAPE: Nothing reasonable -- exact integer equality.
def test_deactivate_decrements_install_count() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert HttpPlugin._install_count == 1
    p.deactivate()
    assert HttpPlugin._install_count == 0


# ESCAPE: test_deactivate_restores_patches_on_last_call
#   CLAIM: deactivate() on last reference restores original transport methods.
#   PATH:  deactivate() -> _install_count == 0 -> _restore_patches().
#   CHECK: handle_request is _HTTPX_ORIGINAL_HANDLE after full deactivate.
#   MUTATION: Skipping _restore_patches() leaves bigfoot patch in place.
#   ESCAPE: Nothing reasonable -- identity comparison against import-time constant.
def test_deactivate_restores_patches_on_last_call() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert httpx.HTTPTransport.handle_request is _HTTPX_ORIGINAL_HANDLE
    assert httpx.AsyncHTTPTransport.handle_async_request is _HTTPX_ORIGINAL_ASYNC_HANDLE
    assert requests.adapters.HTTPAdapter.send is _REQUESTS_ORIGINAL_SEND


# ---------------------------------------------------------------------------
# Test: activate()/deactivate() nesting -- second activate does not reinstall
# ---------------------------------------------------------------------------


# ESCAPE: test_second_activate_increments_but_does_not_reinstall
#   CLAIM: Second activate() increments count to 2 but does not call _install_patches again.
#   PATH:  activate() -> if _install_count == 0 -> skip _install_patches.
#   CHECK: _install_count == 2; call count for _install_patches == 1.
#   MUTATION: Removing the _install_count == 0 guard installs patches twice.
#   ESCAPE: A plugin that always calls _install_patches would still pass count check.
#           We verify patch identity is unchanged (same object, not re-set) by counting calls.
def test_second_activate_increments_but_does_not_reinstall() -> None:
    v, p = _make_verifier_with_plugin()
    with patch.object(p, "install_patches", wraps=p.install_patches) as mock_install:
        p.activate()
        assert HttpPlugin._install_count == 1
        assert mock_install.call_count == 1

        p.activate()
        assert HttpPlugin._install_count == 2
        assert mock_install.call_count == 1  # Not called again


# ESCAPE: test_nested_deactivate_only_uninstalls_on_last
#   CLAIM: deactivate() only calls _restore_patches when count reaches 0.
#   PATH:  deactivate() -> _install_count -= 1 -> if == 0 -> _restore_patches.
#   CHECK: After first deactivate _install_count==1; patches still active.
#          After second deactivate _install_count==0; originals restored.
#   MUTATION: Always calling _restore_patches breaks nested use.
#   ESCAPE: Nothing reasonable -- count check + identity comparison both required.
def test_nested_deactivate_only_uninstalls_on_last() -> None:
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert HttpPlugin._install_count == 2

    p.deactivate()
    assert HttpPlugin._install_count == 1
    # Patches should still be active -- not the originals
    assert httpx.HTTPTransport.handle_request is not _HTTPX_ORIGINAL_HANDLE

    p.deactivate()
    assert HttpPlugin._install_count == 0
    # Now originals should be restored
    assert httpx.HTTPTransport.handle_request is _HTTPX_ORIGINAL_HANDLE


# ---------------------------------------------------------------------------
# Test: _check_conflicts() raises ConflictError on foreign patch
# ---------------------------------------------------------------------------


# ESCAPE: test_check_conflicts_raises_when_httpx_sync_patched_by_foreign
#   CLAIM: _check_conflicts raises ConflictError if httpx.HTTPTransport.handle_request
#          is neither the import-time original nor our bigfoot patch.
#   PATH:  _check_conflicts() -> identity check -> ConflictError.
#   CHECK: ConflictError raised with target naming httpx sync handle.
#   MUTATION: Skipping the sync handle check lets the conflict through silently.
#   ESCAPE: Nothing reasonable -- exact exception type check.
def test_check_conflicts_raises_when_httpx_sync_patched_by_foreign() -> None:
    v, p = _make_verifier_with_plugin()

    foreign_patch = MagicMock()
    # Temporarily replace handle_request with a foreign function
    original = httpx.HTTPTransport.handle_request
    try:
        httpx.HTTPTransport.handle_request = foreign_patch
        with pytest.raises(ConflictError):
            p.check_conflicts()
    finally:
        httpx.HTTPTransport.handle_request = original


# ESCAPE: test_check_conflicts_raises_when_httpx_async_patched_by_foreign
#   CLAIM: _check_conflicts raises ConflictError if
#          httpx.AsyncHTTPTransport.handle_async_request is foreign-patched.
#   PATH:  _check_conflicts() -> async identity check -> ConflictError.
#   CHECK: ConflictError raised.
#   MUTATION: Only checking sync transport and skipping async lets async conflict through.
#   ESCAPE: Nothing reasonable -- exact exception type check on async transport.
def test_check_conflicts_raises_when_httpx_async_patched_by_foreign() -> None:
    v, p = _make_verifier_with_plugin()

    foreign_patch = MagicMock()
    original = httpx.AsyncHTTPTransport.handle_async_request
    try:
        httpx.AsyncHTTPTransport.handle_async_request = foreign_patch
        with pytest.raises(ConflictError):
            p.check_conflicts()
    finally:
        httpx.AsyncHTTPTransport.handle_async_request = original


# ESCAPE: test_check_conflicts_raises_when_requests_patched_by_foreign
#   CLAIM: _check_conflicts raises ConflictError if requests.adapters.HTTPAdapter.send
#          is foreign-patched.
#   PATH:  _check_conflicts() -> requests identity check -> ConflictError.
#   CHECK: ConflictError raised.
#   MUTATION: Not checking requests lets requests conflicts through silently.
#   ESCAPE: Nothing reasonable -- exact exception type check.
def test_check_conflicts_raises_when_requests_patched_by_foreign() -> None:
    v, p = _make_verifier_with_plugin()

    foreign_patch = MagicMock()
    original = requests.adapters.HTTPAdapter.send
    try:
        requests.adapters.HTTPAdapter.send = foreign_patch
        with pytest.raises(ConflictError):
            p.check_conflicts()
    finally:
        requests.adapters.HTTPAdapter.send = original


# ESCAPE: test_check_conflicts_does_not_raise_when_no_foreign_patch
#   CLAIM: _check_conflicts does not raise when methods are at their import-time originals.
#   PATH:  _check_conflicts() -> all checks pass -> no exception.
#   CHECK: No exception raised.
#   MUTATION: Raising unconditionally breaks activate() even when no conflict exists.
#   ESCAPE: If conflict detection has the condition inverted it raises when it shouldn't.
def test_check_conflicts_does_not_raise_when_no_foreign_patch() -> None:
    v, p = _make_verifier_with_plugin()
    # All methods are at import-time originals -- must not raise
    p.check_conflicts()  # No assertion needed; would raise if broken


# ---------------------------------------------------------------------------
# Test: interceptor raises SandboxNotActiveError when no sandbox active
# ---------------------------------------------------------------------------


# ESCAPE: test_httpx_interceptor_raises_sandbox_not_active_when_no_sandbox
#   CLAIM: When patches are installed but _active_verifier is None, httpx request
#          raises SandboxNotActiveError.
#   PATH:  interceptor -> get_verifier_or_raise -> raises SandboxNotActiveError.
#   CHECK: SandboxNotActiveError raised when making httpx.get call with patches active
#          but no sandbox ContextVar set.
#   MUTATION: Calling real network instead of raising lets calls through silently.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_httpx_interceptor_raises_sandbox_not_active_when_no_sandbox() -> None:
    from bigfoot._context import _guard_active, _guard_patches_installed

    v, p = _make_verifier_with_plugin()
    p.activate()
    # No sandbox active -- _active_verifier ContextVar is None
    # Disable guard mode to test the original SandboxNotActiveError behavior
    av_token = _active_verifier.set(None)
    guard_token = _guard_active.set(False)
    patches_token = _guard_patches_installed.set(False)
    try:
        with pytest.raises(SandboxNotActiveError):
            httpx.get("https://api.example.com/no-sandbox")
    finally:
        _guard_patches_installed.reset(patches_token)
        _guard_active.reset(guard_token)
        _active_verifier.reset(av_token)


# ESCAPE: test_requests_interceptor_raises_sandbox_not_active_when_no_sandbox
#   CLAIM: requests interceptor raises SandboxNotActiveError when no sandbox active.
#   PATH:  requests interceptor -> get_verifier_or_raise -> SandboxNotActiveError.
#   CHECK: SandboxNotActiveError raised on requests.get with no sandbox.
#   MUTATION: Letting request proceed to real network skips the error entirely.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_requests_interceptor_raises_sandbox_not_active_when_no_sandbox() -> None:
    from bigfoot._context import _guard_active, _guard_patches_installed

    v, p = _make_verifier_with_plugin()
    p.activate()
    av_token = _active_verifier.set(None)
    guard_token = _guard_active.set(False)
    patches_token = _guard_patches_installed.set(False)
    try:
        with pytest.raises(SandboxNotActiveError):
            requests.get("https://api.example.com/no-sandbox")
    finally:
        _guard_patches_installed.reset(patches_token)
        _guard_active.reset(guard_token)
        _active_verifier.reset(av_token)


# ---------------------------------------------------------------------------
# Test: interceptor raises UnmockedInteractionError when no response configured
# ---------------------------------------------------------------------------


# ESCAPE: test_httpx_interceptor_raises_unmocked_when_no_config
#   CLAIM: httpx request inside sandbox with no mock raises UnmockedInteractionError.
#   PATH:  interceptor -> _find_matching_config returns None -> UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised with correct source_id.
#   MUTATION: Returning empty response instead of raising hides unmocked calls.
#   ESCAPE: Test checks exception type AND source_id attribute.
def test_httpx_interceptor_raises_unmocked_when_no_config() -> None:
    v, p = _make_verifier_with_plugin()
    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            httpx.get("https://api.example.com/no-mock")
    assert exc_info.value.source_id == "http:request"


# ESCAPE: test_requests_interceptor_raises_unmocked_when_no_config
#   CLAIM: requests.get inside sandbox with no mock raises UnmockedInteractionError.
#   PATH:  requests interceptor -> _find_matching_config None -> UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised with source_id == "http:request".
#   MUTATION: Returning a default response instead of raising hides the bug.
#   ESCAPE: Nothing reasonable -- type check plus attribute check.
def test_requests_interceptor_raises_unmocked_when_no_config() -> None:
    v, p = _make_verifier_with_plugin()
    with bigfoot.allow("dns"), v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            requests.get("https://api.example.com/no-mock")
    assert exc_info.value.source_id == "http:request"


# ---------------------------------------------------------------------------
# Test: configured httpx response is returned
# ---------------------------------------------------------------------------


# ESCAPE: test_httpx_configured_response_returned
#   CLAIM: httpx.get returns the mock response with correct status and JSON body.
#   PATH:  interceptor -> _find_matching_config -> httpx.Response constructed from config.
#   CHECK: status_code == 200, response.json() == {"key": "value"}.
#   MUTATION: Wrong status or wrong body encoding fails the assertions.
#   ESCAPE: A response with status 200 but wrong JSON would pass status check but fail json.
def test_httpx_configured_response_returned() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/data", json={"key": "value"}, status=200)

    with v.sandbox():
        response = httpx.get("https://api.example.com/data")

    assert response.status_code == 200
    assert response.json() == {"key": "value"}


# ESCAPE: test_httpx_configured_response_custom_status
#   CLAIM: A mock with status=201 returns response.status_code == 201.
#   PATH:  config.response_status used directly in httpx.Response constructor.
#   CHECK: response.status_code == 201.
#   MUTATION: Hardcoding status=200 fails this test.
#   ESCAPE: Nothing reasonable -- exact integer equality.
def test_httpx_configured_response_custom_status() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("POST", "https://api.example.com/create", json={"id": 42}, status=201)

    with v.sandbox():
        response = httpx.post("https://api.example.com/create", json={"payload": "x"})

    assert response.status_code == 201
    assert response.json() == {"id": 42}


# ---------------------------------------------------------------------------
# Test: configured requests response is returned
# ---------------------------------------------------------------------------


# ESCAPE: test_requests_configured_response_returned
#   CLAIM: requests.get returns the mock response with status 200 and correct JSON.
#   PATH:  requests interceptor -> requests.Response constructed from config.
#   CHECK: status_code == 200, response.json() == {"items": [1, 2, 3]}.
#   MUTATION: Not setting _content leaves empty body; json() would fail.
#   ESCAPE: status check passes but json() check catches wrong body.
def test_requests_configured_response_returned() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/items", json={"items": [1, 2, 3]})

    with bigfoot.allow("dns"), v.sandbox():
        response = requests.get("https://api.example.com/items")

    assert response.status_code == 200
    assert response.json() == {"items": [1, 2, 3]}


# ESCAPE: test_requests_configured_response_custom_status
#   CLAIM: requests mock with status=404 returns response.status_code == 404.
#   PATH:  config.response_status assigned to requests.Response.status_code.
#   CHECK: status_code == 404.
#   MUTATION: Not assigning status_code leaves it at default (None or 200).
#   ESCAPE: Nothing reasonable -- exact integer equality.
def test_requests_configured_response_custom_status() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/missing", status=404)

    with bigfoot.allow("dns"), v.sandbox():
        response = requests.get("https://api.example.com/missing")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test: interaction is recorded in timeline
# ---------------------------------------------------------------------------


# ESCAPE: test_interaction_recorded_after_httpx_request
#   CLAIM: After a successful httpx request, one Interaction is appended to the timeline
#          with source_id=="http:request" and correct method/url in details.
#   PATH:  interceptor -> _record_http_interaction -> timeline.append.
#   CHECK: len(interactions)==1, source_id, details["method"], details["url"] exact match.
#   MUTATION: Skipping _record_http_interaction leaves timeline empty; len check fails.
#   ESCAPE: Recording with wrong method/url would pass len but fail detail assertions.
def test_interaction_recorded_after_httpx_request() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/data", json={"x": 1})

    with v.sandbox():
        httpx.get("https://api.example.com/data")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "http:request"
    assert interactions[0].details["method"] == "GET"
    assert interactions[0].details["url"] == "https://api.example.com/data"


# ESCAPE: test_interaction_recorded_after_requests_request
#   CLAIM: After a successful requests request, one Interaction is in the timeline.
#   PATH:  requests interceptor -> _record_http_interaction -> timeline.append.
#   CHECK: len==1, source_id, method, url in details.
#   MUTATION: Not recording for requests path leaves timeline empty.
#   ESCAPE: Recording only for httpx but not requests would fail this test.
def test_interaction_recorded_after_requests_request() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("POST", "https://api.example.com/submit", json={"ok": True})

    with bigfoot.allow("dns"), v.sandbox():
        requests.post("https://api.example.com/submit", json={"data": 1})

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "http:request"
    assert interactions[0].details["method"] == "POST"
    assert interactions[0].details["url"] == "https://api.example.com/submit"


# ---------------------------------------------------------------------------
# Test: FIFO queue -- same URL mock responses served in order
# ---------------------------------------------------------------------------


# ESCAPE: test_fifo_queue_serves_responses_in_order
#   CLAIM: Two mocks for the same URL are served in registration order (FIFO).
#   PATH:  _find_matching_config pops from front of list; first call gets first config.
#   CHECK: First response has json {"n": 1}, second has {"n": 2}.
#   MUTATION: LIFO instead of FIFO would swap the order.
#   ESCAPE: Nothing reasonable -- exact value assertions on both responses.
def test_fifo_queue_serves_responses_in_order() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/item", json={"n": 1})
    p.mock_response("GET", "https://api.example.com/item", json={"n": 2})

    with v.sandbox():
        r1 = httpx.get("https://api.example.com/item")
        r2 = httpx.get("https://api.example.com/item")

    assert r1.json() == {"n": 1}
    assert r2.json() == {"n": 2}


# ---------------------------------------------------------------------------
# Test: required=False unused mock does not raise at verify_all()
# ---------------------------------------------------------------------------


# ESCAPE: test_optional_mock_not_raised_by_verify_all
#   CLAIM: A mock with required=False that is never triggered does not cause
#          verify_all() to raise UnusedMocksError.
#   PATH:  get_unused_mocks filters by required; optional excluded from error.
#   CHECK: verify_all() completes without exception.
#   MUTATION: Including required=False in get_unused_mocks causes verify_all to raise.
#   ESCAPE: Nothing reasonable -- verify_all() raising is the only failure mode.
def test_optional_mock_not_raised_by_verify_all() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/optional", json={}, required=False)

    with v.sandbox():
        pass  # Never call the mocked URL

    v.verify_all()  # Must not raise


# ---------------------------------------------------------------------------
# Test: get_unused_mocks only returns required=True configs
# ---------------------------------------------------------------------------


# ESCAPE: test_get_unused_mocks_excludes_optional
#   CLAIM: get_unused_mocks() returns only required=True configs from the queue.
#   PATH:  get_unused_mocks() filters _mock_queue by .required.
#   CHECK: One required and one optional queued; get_unused_mocks returns exactly the required one.
#   MUTATION: Not filtering by required returns both.
#   ESCAPE: Filtering by wrong field would return wrong items.
def test_get_unused_mocks_excludes_optional() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/required", json={"a": 1}, required=True)
    p.mock_response("GET", "https://api.example.com/optional", json={"b": 2}, required=False)

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].url == "https://api.example.com/required"
    assert unused[0].method == "GET"


# ---------------------------------------------------------------------------
# Test: matches() checks details fields
# ---------------------------------------------------------------------------


# ESCAPE: test_matches_returns_true_when_details_match
#   CLAIM: matches() returns True when expected dict is a subset of interaction.details.
#   PATH:  matches() iterates expected items and compares to interaction.details values.
#   CHECK: True returned when method and url are correct.
#   MUTATION: Inverting the comparison returns False always.
#   ESCAPE: Returning True always passes this but fails test_matches_returns_false_when_mismatch.
def test_matches_returns_true_when_details_match() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={"method": "GET", "url": "https://example.com/", "status": 200},
        plugin=p,
    )
    assert p.matches(interaction, {"method": "GET", "url": "https://example.com/"}) is True


# ESCAPE: test_matches_returns_false_when_details_mismatch
#   CLAIM: matches() returns False when any expected field differs from details.
#   PATH:  matches() -> comparison fails -> returns False.
#   CHECK: False returned when url does not match.
#   MUTATION: Always returning True fails this test.
#   ESCAPE: Nothing reasonable -- exact boolean equality.
def test_matches_returns_false_when_details_mismatch() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={"method": "GET", "url": "https://example.com/other", "status": 200},
        plugin=p,
    )
    assert p.matches(interaction, {"method": "GET", "url": "https://example.com/"}) is False


# ---------------------------------------------------------------------------
# Test: format_interaction returns correct string
# ---------------------------------------------------------------------------


# ESCAPE: test_format_interaction_returns_correct_string
#   CLAIM: format_interaction() returns a string containing plugin name, method, url, status.
#   PATH:  format_interaction() reads details["method"], details["url"], details["status"].
#   CHECK: Exact string match.
#   MUTATION: Missing any field produces a different string.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_format_interaction_returns_correct_string() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={"method": "POST", "url": "https://api.example.com/v1", "status": 201},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[HttpPlugin] POST https://api.example.com/v1 (status=201)"


# ---------------------------------------------------------------------------
# Test: format_assert_hint returns correct snippet
# ---------------------------------------------------------------------------


# ESCAPE: test_format_assert_hint_returns_correct_snippet
#   CLAIM: format_assert_hint() returns a call snippet using method and url from details.
#   PATH:  format_assert_hint() reads details["method"] and details["url"].
#   CHECK: Snippet includes method, url, and status.
#   MUTATION: Using wrong key names produces a snippet with "?" placeholders.
#   ESCAPE: Snippet with wrong values would not be a valid copy-pasteable hint.
def test_format_assert_hint_returns_correct_snippet() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={
            "method": "GET",
            "url": "https://api.example.com/data",
            "status": 200,
            "request_headers": {},
            "request_body": "",
            "response_headers": {},
            "response_body": "",
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "http.assert_request(\n"
        '    "GET",\n'
        '    "https://api.example.com/data",\n'
        "    headers={},\n"
        "    body='',\n"
        "    require_response=True,\n"
        ").assert_response(\n"
        "    status=200,\n"
        "    headers={},\n"
        "    body='',\n"
        ")"
    )


def test_format_assert_hint_with_require_response() -> None:
    v = StrictVerifier()
    p = HttpPlugin(v, require_response=True)
    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={
            "method": "POST",
            "url": "https://api.example.com/create",
            "status": 201,
            "request_headers": {"content-type": "application/json"},
            "request_body": '{"name": "test"}',
            "response_headers": {"content-type": "application/json"},
            "response_body": '{"id": 1}',
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "http.assert_request(\n"
        '    "POST",\n'
        '    "https://api.example.com/create",\n'
        "    headers={'content-type': 'application/json'},\n"
        "    body='{\"name\": \"test\"}',\n"
        "    require_response=True,\n"
        ").assert_response(\n"
        "    status=201,\n"
        "    headers={'content-type': 'application/json'},\n"
        "    body='{\"id\": 1}',\n"
        ")"
    )


# ---------------------------------------------------------------------------
# Test: HttpRequestSentinel has correct source_id
# ---------------------------------------------------------------------------


# ESCAPE: test_request_sentinel_source_id
#   CLAIM: http.request sentinel has source_id == "http:request".
#   PATH:  HttpRequestSentinel.__init__ sets self.source_id.
#   CHECK: p.request.source_id == "http:request".
#   MUTATION: Using a different source_id string breaks assert_interaction routing.
#   ESCAPE: Nothing reasonable -- exact string equality.
def test_request_sentinel_source_id() -> None:
    v, p = _make_verifier_with_plugin()
    assert p.request.source_id == "http:request"


# ---------------------------------------------------------------------------
# Test: mock_response raises ValueError when json and body both provided
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_response_raises_when_json_and_body_both_provided
#   CLAIM: Calling mock_response with both json= and body= raises ValueError.
#   PATH:  mock_response() checks for mutual exclusion at top.
#   CHECK: ValueError raised.
#   MUTATION: Not checking allows both to be provided; body might silently win.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_mock_response_raises_when_json_and_body_both_provided() -> None:
    v, p = _make_verifier_with_plugin()
    with pytest.raises(ValueError):
        p.mock_response("GET", "https://api.example.com/x", json={"a": 1}, body="text")


# ---------------------------------------------------------------------------
# Test: format_unused_mock_hint includes registration_traceback
# ---------------------------------------------------------------------------


# ESCAPE: test_format_unused_mock_hint_includes_registration_traceback
#   CLAIM: format_unused_mock_hint() includes the registration_traceback in its output,
#          so callers can locate where the unused mock was registered.
#   PATH:  format_unused_mock_hint() reads mock_config.registration_traceback and embeds it.
#   CHECK: Exact full output equality, including traceback text, method/url header,
#          "Mock registered at:" label, and both options lines.
#   MUTATION: Omitting registration_traceback from the output produces a different string.
#   ESCAPE: Asserting only a substring would miss the case where traceback is present but
#           label text is wrong; exact equality catches both.
def test_format_unused_mock_hint_includes_registration_traceback() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("POST", "https://api.example.com/create", json={"id": 1})

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    config = unused[0]

    result = p.format_unused_mock_hint(config)

    expected = (
        f"http:POST https://api.example.com/create was registered but never called.\n"
        f"    Mock registered at:\n"
        f"{config.registration_traceback}\n"
        f"    Options:\n"
        f"      - Remove this mock if it's not needed\n"
        f'      - Mark it optional: http.mock_response("POST", '
        f'"https://api.example.com/create", ..., required=False)'
    )
    assert result == expected


# ---------------------------------------------------------------------------
# Coverage gap: _identify_patcher recognises known library names
# ---------------------------------------------------------------------------


def test_identify_patcher_recognises_respx() -> None:
    from bigfoot.plugins.http import _identify_patcher

    method = MagicMock()
    method.__module__ = "respx.mock"
    method.__qualname__ = "MockTransport.handle_request"
    assert _identify_patcher(method) == "respx"


def test_identify_patcher_recognises_responses() -> None:
    from bigfoot.plugins.http import _identify_patcher

    method = MagicMock()
    method.__module__ = "responses"
    method.__qualname__ = "RequestsMock.send"
    assert _identify_patcher(method) == "responses"


def test_identify_patcher_recognises_httpretty() -> None:
    from bigfoot.plugins.http import _identify_patcher

    method = MagicMock()
    method.__module__ = "httpretty.core"
    method.__qualname__ = "fakesocket"
    assert _identify_patcher(method) == "httpretty"


def test_identify_patcher_returns_unknown_for_unrecognised() -> None:
    from bigfoot.plugins.http import _identify_patcher

    method = MagicMock()
    method.__module__ = "some.other.lib"
    method.__qualname__ = "Interceptor.send"
    assert _identify_patcher(method) == "an unknown library"


# ---------------------------------------------------------------------------
# Coverage gap: _find_http_plugin raises RuntimeError when no HttpPlugin
# ---------------------------------------------------------------------------


def test_find_http_plugin_raises_when_no_http_plugin_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bigfoot.plugins.http import _find_http_plugin

    # Disable all plugins so HttpPlugin is not auto-instantiated
    monkeypatch.setattr(
        "bigfoot._verifier.load_bigfoot_config",
        lambda: {"enabled_plugins": ["subprocess"]},
    )
    v = StrictVerifier()
    # No HttpPlugin registered; _find_http_plugin must raise
    with pytest.raises(RuntimeError, match="BUG"):
        _find_http_plugin(v)


# ---------------------------------------------------------------------------
# Coverage gap: async httpx interceptor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_httpx_interceptor_returns_mock_response() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/async-data", json={"async": True})

    async with v.sandbox():
        async with httpx.AsyncClient() as client:
            response = await client.get("https://api.example.com/async-data")

    assert response.status_code == 200
    assert response.json() == {"async": True}


@pytest.mark.asyncio
async def test_async_httpx_interceptor_raises_unmocked_when_no_config() -> None:
    v, p = _make_verifier_with_plugin()

    async with v.sandbox():
        async with httpx.AsyncClient() as client:
            with pytest.raises(UnmockedInteractionError) as exc_info:
                await client.get("https://api.example.com/no-mock-async")
    assert exc_info.value.source_id == "http:request"


# ---------------------------------------------------------------------------
# Coverage gap: urllib interceptor
# ---------------------------------------------------------------------------


def test_urllib_interceptor_returns_mock_response() -> None:
    import urllib.request

    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "http://api.example.com/urllib-data", json={"urllib": True})

    with v.sandbox():
        response = urllib.request.urlopen("http://api.example.com/urllib-data")
        body = response.read()

    import json

    assert json.loads(body) == {"urllib": True}


def test_urllib_interceptor_raises_unmocked_when_no_config() -> None:
    import urllib.request

    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        with pytest.raises(UnmockedInteractionError) as exc_info:
            urllib.request.urlopen("http://api.example.com/no-mock-urllib")
    assert exc_info.value.source_id == "http:request"


def test_urllib_interceptor_records_interaction() -> None:
    import urllib.request

    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "http://api.example.com/record", json={"ok": 1})

    with v.sandbox():
        urllib.request.urlopen("http://api.example.com/record")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "http:request"
    assert interactions[0].details["method"] == "GET"
    assert interactions[0].details["url"] == "http://api.example.com/record"


def test_urllib_https_interceptor_returns_mock_response() -> None:
    """https_open handler is covered via the urllib opener for HTTPS URLs."""
    import urllib.request

    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/urllib-https", json={"tls": True})

    with v.sandbox():
        response = urllib.request.urlopen("https://api.example.com/urllib-https")
        body = response.read()

    import json

    assert json.loads(body) == {"tls": True}


# ---------------------------------------------------------------------------
# Coverage gap: mock_response with body as str (encode path)
# ---------------------------------------------------------------------------


def test_mock_response_with_str_body_encodes_to_bytes() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/text", body="hello world")

    with v.sandbox():
        response = httpx.get("https://api.example.com/text")

    assert response.content == b"hello world"


# ---------------------------------------------------------------------------
# Coverage gap: requests body as non-bytes str path
# ---------------------------------------------------------------------------


def test_requests_interceptor_records_str_body() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("POST", "https://api.example.com/str-body", json={"ok": True})

    with bigfoot.allow("dns"), v.sandbox():
        # Sending a string body directly via prepared request
        req = requests.Request("POST", "https://api.example.com/str-body", data="raw string")
        prepared = req.prepare()
        # prepared.body is a str when data= is a string
        session = requests.Session()
        response = session.send(prepared)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Coverage gap: HttpPlugin.matches() returns False on exception
# ---------------------------------------------------------------------------


def test_http_plugin_matches_returns_false_on_exception() -> None:
    v, p = _make_verifier_with_plugin()

    class _RaisesOnEq:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("comparison exploded")

    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={"method": "GET", "url": "https://example.com/"},
        plugin=p,
    )
    result = p.matches(interaction, {"method": _RaisesOnEq()})
    assert result is False


# ---------------------------------------------------------------------------
# Coverage gap: format_mock_hint
# ---------------------------------------------------------------------------


def test_format_mock_hint_returns_correct_snippet() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={"method": "DELETE", "url": "https://api.example.com/item/1"},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == 'http.mock_response("DELETE", "https://api.example.com/item/1", json={...})'


# ---------------------------------------------------------------------------
# Coverage gap: _url_matches with params that DON'T match (val not in actual)
# ---------------------------------------------------------------------------


def test_url_matches_returns_false_when_param_value_missing() -> None:
    """_url_matches returns False when a required param value is absent from the actual URL."""
    from bigfoot.plugins.http import HttpMockConfig

    v, p = _make_verifier_with_plugin()

    config = HttpMockConfig(
        method="GET",
        url="https://api.example.com/search",
        params={"q": "foo"},
        response_status=200,
        response_headers={},
        response_body=b"",
    )
    # Actual URL has the param key but with a different value
    assert p._url_matches(config, "https://api.example.com/search?q=bar") is False


def test_url_matches_returns_false_when_param_key_absent() -> None:
    """_url_matches returns False when a required param key is entirely absent."""
    from bigfoot.plugins.http import HttpMockConfig

    v, p = _make_verifier_with_plugin()

    config = HttpMockConfig(
        method="GET",
        url="https://api.example.com/search",
        params={"q": "foo"},
        response_status=200,
        response_headers={},
        response_body=b"",
    )
    # Actual URL has no query params
    assert p._url_matches(config, "https://api.example.com/search") is False


def test_url_matches_returns_true_with_empty_params_dict() -> None:
    """_url_matches returns True when params is an empty dict (no constraints)."""
    from bigfoot.plugins.http import HttpMockConfig

    v, p = _make_verifier_with_plugin()

    config = HttpMockConfig(
        method="GET",
        url="https://api.example.com/items",
        params={},  # empty dict: no param constraints
        response_status=200,
        response_headers={},
        response_body=b"",
    )
    assert p._url_matches(config, "https://api.example.com/items?page=2") is True


def test_url_matches_returns_false_when_val_not_in_actual_param_values() -> None:
    """_url_matches returns False when the param key is present but value doesn't match."""
    from bigfoot.plugins.http import HttpMockConfig

    v, p = _make_verifier_with_plugin()

    config = HttpMockConfig(
        method="GET",
        url="https://api.example.com/search",
        params={"q": "cats"},
        response_status=200,
        response_headers={},
        response_body=b"",
    )
    # The actual URL has the "q" key, but with a different value
    assert p._url_matches(config, "https://api.example.com/search?q=dogs") is False


def test_url_matches_returns_false_when_scheme_differs() -> None:
    """_url_matches returns False immediately when schemes differ (short-circuit)."""
    from bigfoot.plugins.http import HttpMockConfig

    v, p = _make_verifier_with_plugin()

    config = HttpMockConfig(
        method="GET",
        url="https://api.example.com/data",
        params=None,
        response_status=200,
        response_headers={},
        response_body=b"",
    )
    # HTTP vs HTTPS
    assert p._url_matches(config, "http://api.example.com/data") is False


# ---------------------------------------------------------------------------
# Coverage gap: _restore_patches() no-op branches when originals already None
# ---------------------------------------------------------------------------


def test_restore_patches_is_idempotent_when_originals_are_none() -> None:
    """_restore_patches() must not raise when called with all original slots already None."""
    v, p = _make_verifier_with_plugin()
    # Ensure all originals are None (the default state before any activate)
    assert HttpPlugin._original_httpx_transport_handle is None
    assert HttpPlugin._original_httpx_async_transport_handle is None
    assert HttpPlugin._original_requests_adapter_send is None
    # Calling _restore_patches() with everything at None must not raise
    p.restore_patches()


# ---------------------------------------------------------------------------
# assertable_fields tests
# ---------------------------------------------------------------------------


def test_http_plugin_assertable_fields_returns_all_seven() -> None:
    """assertable_fields() returns frozenset of all seven HTTP details."""
    v, p = _make_verifier_with_plugin()

    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={
            "method": "GET",
            "url": "https://example.com",
            "request_headers": {},
            "request_body": "",
            "status": 200,
            "response_headers": {},
            "response_body": "",
        },
        plugin=p,
    )
    result = p.assertable_fields(interaction)
    assert result == frozenset(
        {"method", "url", "request_headers", "request_body", "status", "response_headers", "response_body"}
    )


# ---------------------------------------------------------------------------
# pass_through tests
# ---------------------------------------------------------------------------


def test_pass_through_registers_rule() -> None:
    """pass_through() stores the rule as (METHOD.upper(), url)."""
    v, p = _make_verifier_with_plugin()
    p.pass_through("get", "https://example.com/api")
    assert p._pass_through_rules == [("GET", "https://example.com/api")]


def test_pass_through_multiple_rules() -> None:
    """pass_through() can register multiple rules."""
    v, p = _make_verifier_with_plugin()
    p.pass_through("GET", "https://example.com/a")
    p.pass_through("POST", "https://example.com/b")
    assert p._pass_through_rules == [
        ("GET", "https://example.com/a"),
        ("POST", "https://example.com/b"),
    ]


def test_matches_pass_through_rule_exact_match() -> None:
    """_matches_pass_through_rule returns True for exact scheme+host+path match."""
    v, p = _make_verifier_with_plugin()
    p.pass_through("GET", "https://example.com/api")
    assert p._matches_pass_through_rule("GET", "https://example.com/api") is True


def test_matches_pass_through_rule_ignores_query_params() -> None:
    """_matches_pass_through_rule matches even if actual URL has query params."""
    v, p = _make_verifier_with_plugin()
    p.pass_through("GET", "https://example.com/api")
    assert p._matches_pass_through_rule("GET", "https://example.com/api?key=val") is True


def test_matches_pass_through_rule_method_mismatch() -> None:
    """_matches_pass_through_rule returns False when method doesn't match."""
    v, p = _make_verifier_with_plugin()
    p.pass_through("GET", "https://example.com/api")
    assert p._matches_pass_through_rule("POST", "https://example.com/api") is False


def test_matches_pass_through_rule_path_mismatch() -> None:
    """_matches_pass_through_rule returns False when path doesn't match."""
    v, p = _make_verifier_with_plugin()
    p.pass_through("GET", "https://example.com/api")
    assert p._matches_pass_through_rule("GET", "https://example.com/other") is False


def test_matches_pass_through_rule_no_rules_registered() -> None:
    """_matches_pass_through_rule returns False when no rules are registered."""
    v, p = _make_verifier_with_plugin()
    assert p._matches_pass_through_rule("GET", "https://example.com/api") is False


def test_httpx_pass_through_calls_original_transport() -> None:
    """httpx sync pass-through calls _execute_httpx_pass_through and records interaction."""
    v, p = _make_verifier_with_plugin()
    p.pass_through("GET", "https://example.com/api")

    fake_transport = MagicMock(spec=httpx.HTTPTransport)
    fake_response = httpx.Response(200, json={"ok": True})

    # activate() sets _original_httpx_transport_handle to the real transport method.
    # We set it to a fake inside the sandbox so pass_through can call it.
    # We must restore the real original after the sandbox exits to avoid corrupting
    # the global class state for subsequent tests.
    with v.sandbox():
        real_original = HttpPlugin._original_httpx_transport_handle
        HttpPlugin._original_httpx_transport_handle = lambda ts, req: fake_response  # type: ignore[assignment]
        try:
            request = httpx.Request("GET", "https://example.com/api")
            result = p._handle_httpx_request(fake_transport, request)
        finally:
            HttpPlugin._original_httpx_transport_handle = real_original  # type: ignore[assignment]

    assert result.status_code == 200
    unasserted = v._timeline.all_unasserted()
    assert len(unasserted) == 1
    assert unasserted[0].details["method"] == "GET"
    assert unasserted[0].details["url"] == "https://example.com/api"
    assert unasserted[0].details["status"] == 200


def test_requests_pass_through_calls_original_adapter() -> None:
    """requests pass-through calls _execute_requests_pass_through and records interaction."""
    v, p = _make_verifier_with_plugin()
    p.pass_through("GET", "https://example.com/api")

    fake_adapter = MagicMock(spec=requests.adapters.HTTPAdapter)
    fake_response = requests.Response()
    fake_response.status_code = 200

    prepared = requests.Request("GET", "https://example.com/api").prepare()

    # activate() sets _original_requests_adapter_send to the real adapter send method.
    # We set it to a fake inside the sandbox so pass_through can call it.
    # We must restore the real original after the sandbox exits to avoid corrupting
    # the global class state for subsequent tests.
    with v.sandbox():
        real_original = HttpPlugin._original_requests_adapter_send
        HttpPlugin._original_requests_adapter_send = lambda adapter, req, **kw: fake_response  # type: ignore[assignment]
        try:
            result = p._handle_requests_request(fake_adapter, prepared)
        finally:
            HttpPlugin._original_requests_adapter_send = real_original  # type: ignore[assignment]

    assert result.status_code == 200
    unasserted = v._timeline.all_unasserted()
    assert len(unasserted) == 1
    assert unasserted[0].details["method"] == "GET"
    assert unasserted[0].details["status"] == 200


def test_unused_pass_through_rule_does_not_raise_at_verify_all() -> None:
    """A pass_through rule that is never triggered raises no error at verify_all()."""
    v, p = _make_verifier_with_plugin()
    p.pass_through("GET", "https://example.com/api")

    with v.sandbox():
        pass  # No requests made

    v.verify_all()  # Must not raise


# ---------------------------------------------------------------------------
# HttpAssertionBuilder tests
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_request_returns_http_assertion_builder
#   CLAIM: p.assert_request(require_response=True) returns an HttpAssertionBuilder instance.
#   PATH:  HttpPlugin.assert_request(require_response=True) -> HttpAssertionBuilder(...).
#   CHECK: isinstance check.
#   MUTATION: Returning None or a different type fails isinstance.
#   ESCAPE: Nothing reasonable -- isinstance check on the exact class.
def test_assert_request_returns_http_assertion_builder() -> None:
    v, p = _make_verifier_with_plugin()
    builder = p.assert_request("GET", "https://example.com/api", require_response=True)
    assert isinstance(builder, HttpAssertionBuilder)


# ESCAPE: test_assert_request_stores_method_and_url
#   CLAIM: HttpAssertionBuilder stores method and url from assert_request().
#   PATH:  assert_request(require_response=True) passes method/url to HttpAssertionBuilder.__init__.
#   CHECK: builder._method == "GET", builder._url == "https://example.com/api".
#   MUTATION: Swapping method and url would fail both checks.
#   ESCAPE: Nothing reasonable -- exact attribute equality.
def test_assert_request_stores_method_and_url() -> None:
    v, p = _make_verifier_with_plugin()
    builder = p.assert_request("GET", "https://example.com/api", require_response=True)
    assert builder._method == "GET"
    assert builder._url == "https://example.com/api"


# ESCAPE: test_assert_request_default_headers_and_body
#   CLAIM: assert_request(require_response=True) defaults headers to {} and body to "".
#   PATH:  assert_request() uses `headers if headers is not None else {}` and body="".
#   CHECK: builder._headers == {}, builder._body == "".
#   MUTATION: Defaulting headers to None would leave None stored.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_assert_request_default_headers_and_body() -> None:
    v, p = _make_verifier_with_plugin()
    builder = p.assert_request("POST", "https://example.com/submit", require_response=True)
    assert builder._headers == {}
    assert builder._body == ""


# ESCAPE: test_assert_request_with_explicit_headers_and_body
#   CLAIM: assert_request(require_response=True) passes through explicit headers and body.
#   PATH:  assert_request(headers=..., body=..., require_response=True) -> builder stores them.
#   CHECK: builder._headers and builder._body match what was passed.
#   MUTATION: Ignoring the kwargs and using defaults would fail.
#   ESCAPE: Nothing reasonable -- exact dict/str equality.
def test_assert_request_with_explicit_headers_and_body() -> None:
    v, p = _make_verifier_with_plugin()
    builder = p.assert_request(
        "POST",
        "https://example.com/submit",
        headers={"Authorization": "Bearer tok"},
        body='{"key": "val"}',
        require_response=True,
    )
    assert builder._headers == {"Authorization": "Bearer tok"}
    assert builder._body == '{"key": "val"}'


# ESCAPE: test_assert_response_calls_assert_interaction_with_all_seven_fields
#   CLAIM: HttpAssertionBuilder.assert_response() calls verifier.assert_interaction()
#          with all seven fields (method, url, request_headers, request_body, status,
#          response_headers, response_body).
#   PATH:  assert_response() -> verifier.assert_interaction(sentinel, **all_seven).
#   CHECK: Full interaction is found in timeline after a real mock request.
#   MUTATION: Omitting any field from assert_interaction call leaves it unasserted.
#   ESCAPE: Verifier raises if any required field is missing from the expected dict.
def test_assert_response_calls_assert_interaction_with_all_seven_fields() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response(
        "GET",
        "https://api.example.com/data",
        json={"key": "value"},
        status=200,
        headers={"content-type": "application/json"},
    )

    with v.sandbox():
        httpx.get("https://api.example.com/data")

    # Capture actual recorded headers so we can assert them exactly
    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    recorded_request_headers = interactions[0].details["request_headers"]

    # Use the builder to assert all seven fields
    p.assert_request(
        "GET",
        "https://api.example.com/data",
        headers=recorded_request_headers,
        require_response=True,
    ).assert_response(
        status=200,
        headers={"content-type": "application/json"},
        body='{"key": "value"}',
    )

    # All interactions asserted -- verify_all must not raise
    v.verify_all()


# ESCAPE: test_assert_response_is_terminal_marks_interaction_asserted
#   CLAIM: After assert_response(), the interaction is marked asserted on the timeline.
#   PATH:  assert_response() -> assert_interaction() -> timeline.mark_asserted().
#   CHECK: v._timeline.all_unasserted() is empty after assert_response().
#   MUTATION: Not calling assert_interaction() leaves interaction unasserted.
#   ESCAPE: Nothing reasonable -- empty list check is definitive.
def test_assert_response_is_terminal_marks_interaction_asserted() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("POST", "https://api.example.com/create", json={"id": 1}, status=201)

    with v.sandbox():
        httpx.post("https://api.example.com/create", json={"payload": "x"})

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1

    # Capture the actual recorded fields to use in assertion
    recorded = interactions[0].details

    p.assert_request(
        "POST",
        "https://api.example.com/create",
        headers=recorded["request_headers"],
        body=recorded["request_body"],
        require_response=True,
    ).assert_response(
        status=201,
        headers={"content-type": "application/json"},
        body='{"id": 1}',
    )

    assert len(v._timeline.all_unasserted()) == 0


# ESCAPE: test_assert_request_lazy_does_not_touch_timeline
#   CLAIM: Calling assert_request(require_response=True) alone (without assert_response())
#          does not modify the timeline.
#   PATH:  assert_request(require_response=True) only stores fields in the builder;
#          timeline is untouched until assert_response() is called.
#   CHECK: all_unasserted() still contains the interaction after assert_request().
#   MUTATION: If assert_request() touches the timeline the interaction would disappear.
#   ESCAPE: Nothing reasonable -- count check is definitive.
def test_assert_request_lazy_does_not_touch_timeline() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/lazy", json={"lazy": True})

    with v.sandbox():
        httpx.get("https://api.example.com/lazy")

    # Call assert_request with require_response=True but NOT assert_response
    p.assert_request("GET", "https://api.example.com/lazy", require_response=True)

    # Timeline interaction should still be unasserted (builder path is lazy)
    assert len(v._timeline.all_unasserted()) == 1


# ---------------------------------------------------------------------------
# require_response config tests
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_request_terminal_when_require_response_false
#   CLAIM: assert_request() with explicit require_response=False is terminal: returns
#          None and fully asserts the interaction (verify_all passes).
#   PATH:  assert_request() -> effective=False -> assert_interaction(4 request fields)
#          -> returns None.
#   CHECK: return value is None; verify_all() does not raise.
#   MUTATION: Returning a builder or not asserting would fail one of the two checks.
#   ESCAPE: Nothing reasonable -- None check + verify_all() are both required.
def test_assert_request_terminal_when_require_response_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/terminal", json={"ok": True})

    with v.sandbox():
        httpx.get("https://api.example.com/terminal")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    recorded_headers = interactions[0].details["request_headers"]

    result = p.assert_request(
        "GET",
        "https://api.example.com/terminal",
        headers=recorded_headers,
        require_response=False,
    )

    assert result is None
    v.verify_all()


# ESCAPE: test_assert_request_per_call_require_response_true
#   CLAIM: When plugin default is False but per-call require_response=True is passed,
#          assert_request() returns a builder instead of being terminal.
#   PATH:  assert_request(require_response=True) -> effective=True -> returns builder.
#   CHECK: isinstance(result, HttpAssertionBuilder) is True.
#   MUTATION: Ignoring the per-call override would return None instead of builder.
#   ESCAPE: Nothing reasonable -- isinstance check on the exact class.
def test_assert_request_per_call_require_response_true() -> None:
    v, p = _make_verifier_with_plugin()  # default require_response=False
    p.mock_response("POST", "https://api.example.com/per-call", json={"id": 42}, status=201)

    with v.sandbox():
        httpx.post("https://api.example.com/per-call")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    recorded = interactions[0].details

    result = p.assert_request(
        "POST",
        "https://api.example.com/per-call",
        headers=recorded["request_headers"],
        body=recorded["request_body"],
        require_response=True,
    )

    assert isinstance(result, HttpAssertionBuilder)
    # Complete the assertion so timeline is clean
    result.assert_response(
        status=201,
        headers={"content-type": "application/json"},
        body='{"id": 42}',
    )
    v.verify_all()


# ESCAPE: test_assert_request_global_require_response_true
#   CLAIM: When plugin is constructed with require_response=True, assert_request()
#          returns a builder that must be chained with assert_response().
#   PATH:  HttpPlugin(v, require_response=True) -> assert_request() -> effective=True
#          -> returns HttpAssertionBuilder.
#   CHECK: isinstance(result, HttpAssertionBuilder); verify_all() passes after chaining.
#   MUTATION: Not using instance default would ignore the constructor flag.
#   ESCAPE: Nothing reasonable -- isinstance + verify_all() together are definitive.
def test_assert_request_global_require_response_true() -> None:
    v, p = _make_verifier_with_plugin()
    p._require_response = True
    p.mock_response("GET", "https://api.example.com/global", json={"global": True})

    with v.sandbox():
        httpx.get("https://api.example.com/global")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    recorded = interactions[0].details

    builder = p.assert_request(
        "GET",
        "https://api.example.com/global",
        headers=recorded["request_headers"],
    )

    assert isinstance(builder, HttpAssertionBuilder)
    builder.assert_response(
        status=200,
        headers={"content-type": "application/json"},
        body='{"global": true}',
    )
    v.verify_all()


# ESCAPE: test_assert_request_terminal_missing_field_raises
#   CLAIM: When terminal (require_response=False), passing wrong method raises
#          InteractionMismatchError.
#   PATH:  assert_request() -> assert_interaction() -> plugin.matches() fails
#          -> InteractionMismatchError raised.
#   CHECK: InteractionMismatchError is raised when method does not match.
#   MUTATION: Not calling assert_interaction() terminally would let wrong calls pass.
#   ESCAPE: Nothing reasonable -- exact exception type check.
def test_assert_request_terminal_missing_field_raises() -> None:
    from bigfoot._errors import InteractionMismatchError

    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/mismatch", json={"x": 1})

    with v.sandbox():
        httpx.get("https://api.example.com/mismatch")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    recorded_headers = interactions[0].details["request_headers"]

    with pytest.raises(InteractionMismatchError):
        p.assert_request(
            "POST",  # Wrong method -- recorded interaction is GET
            "https://api.example.com/mismatch",
            headers=recorded_headers,
            require_response=False,
        )


# ---------------------------------------------------------------------------
# aiohttp tests
# ---------------------------------------------------------------------------

aiohttp_mod = pytest.importorskip("aiohttp")


async def _aiohttp_get(url: str) -> "aiohttp_mod.ClientResponse":
    """Helper: perform a GET request via aiohttp.ClientSession."""
    async with aiohttp_mod.ClientSession() as session:
        return await session.get(url)


async def _aiohttp_post(
    url: str, json: object = None, data: object = None
) -> "aiohttp_mod.ClientResponse":
    """Helper: perform a POST request via aiohttp.ClientSession."""
    async with aiohttp_mod.ClientSession() as session:
        return await session.post(url, json=json, data=data)


# ESCAPE: None -- aiohttp GET with mocked response exercises the full intercept path.
# CHECK: Intercepted aiohttp GET returns correct status, body, and records interaction.
# MUTATION: Removing the aiohttp interceptor would cause a real network call or error.
@pytest.mark.asyncio
async def test_aiohttp_get_basic() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/data", json={"value": 42})

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            response = await _aiohttp_get("https://api.example.com/data")
            assert response.status == 200
            body = await response.json()
            assert body == {"value": 42}
    finally:
        _active_verifier.reset(token)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["method"] == "GET"
    assert interactions[0].details["url"] == "https://api.example.com/data"
    assert interactions[0].details["status"] == 200
    assert interactions[0].details["response_body"] == '{"value": 42}'

    v.assert_interaction(
        p.request,
        method="GET",
        url="https://api.example.com/data",
        request_headers={},
        request_body="",
        status=200,
        response_headers={"content-type": "application/json"},
        response_body='{"value": 42}',
    )


# ESCAPE: None -- exercises POST with JSON body through aiohttp.
# CHECK: Request body is captured from json kwarg and recorded correctly.
# MUTATION: Not extracting json kwarg would record empty body.
@pytest.mark.asyncio
async def test_aiohttp_post_json() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response(
        "POST", "https://api.example.com/items", json={"id": 1}, status=201
    )

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            response = await _aiohttp_post(
                "https://api.example.com/items", json={"name": "widget"}
            )
            assert response.status == 201
    finally:
        _active_verifier.reset(token)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["method"] == "POST"
    assert interactions[0].details["request_body"] == '{"name": "widget"}'

    v.assert_interaction(
        p.request,
        method="POST",
        url="https://api.example.com/items",
        request_headers={},
        request_body='{"name": "widget"}',
        status=201,
        response_headers={"content-type": "application/json"},
        response_body='{"id": 1}',
    )


# ESCAPE: None -- exercises POST with raw data body through aiohttp.
# CHECK: Request body is captured from data kwarg (bytes) and recorded correctly.
# MUTATION: Not extracting data kwarg would record empty body.
@pytest.mark.asyncio
async def test_aiohttp_post_data() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("POST", "https://api.example.com/upload", body="ok", status=200)

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            async with aiohttp_mod.ClientSession() as session:
                response = await session.post(
                    "https://api.example.com/upload", data=b"raw bytes"
                )
                assert response.status == 200
    finally:
        _active_verifier.reset(token)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["request_body"] == "raw bytes"

    v.assert_interaction(
        p.request,
        method="POST",
        url="https://api.example.com/upload",
        request_headers={},
        request_body="raw bytes",
        status=200,
        response_headers={},
        response_body="ok",
    )


# ESCAPE: None -- tests custom response headers are returned correctly.
# CHECK: Mock response headers are recorded and available on the fake response.
# MUTATION: Not passing headers through to _FakeAiohttpResponse would lose them.
@pytest.mark.asyncio
async def test_aiohttp_mock_response_with_headers() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response(
        "GET",
        "https://api.example.com/data",
        json={"ok": True},
        headers={"x-custom": "value", "content-type": "application/json"},
    )

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            response = await _aiohttp_get("https://api.example.com/data")
            assert response.headers["x-custom"] == "value"
    finally:
        _active_verifier.reset(token)

    v.assert_interaction(
        p.request,
        method="GET",
        url="https://api.example.com/data",
        request_headers={},
        request_body="",
        status=200,
        response_headers={"x-custom": "value", "content-type": "application/json"},
        response_body='{"ok": true}',
    )


# ESCAPE: None -- assert_request/assert_response chaining works with aiohttp.
# CHECK: The chained builder pattern works identically for aiohttp interactions.
# MUTATION: If aiohttp interactions were recorded differently, chaining would fail.
@pytest.mark.asyncio
async def test_aiohttp_assert_request_assert_response_chaining() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/chain", json={"chained": True})

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            await _aiohttp_get("https://api.example.com/chain")
    finally:
        _active_verifier.reset(token)

    builder = p.assert_request(
        "GET",
        "https://api.example.com/chain",
        headers={},
        body="",
        require_response=True,
    )
    assert builder is not None
    builder.assert_response(
        200,
        {"content-type": "application/json"},
        '{"chained": true}',
    )


# ESCAPE: None -- unmocked aiohttp raises UnmockedInteractionError.
# CHECK: An aiohttp request with no matching mock raises immediately.
# MUTATION: Without the unmocked check, the request would hit the real network.
@pytest.mark.asyncio
async def test_aiohttp_unmocked_raises() -> None:
    v, p = _make_verifier_with_plugin()

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            with pytest.raises(UnmockedInteractionError):
                await _aiohttp_get("https://api.example.com/unmocked")
    finally:
        _active_verifier.reset(token)


# ESCAPE: None -- request headers passed via aiohttp kwargs are captured.
# CHECK: Headers dict from the request is recorded in interaction details.
# MUTATION: Not reading kwargs["headers"] would record empty headers.
@pytest.mark.asyncio
async def test_aiohttp_request_headers_captured() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/auth", json={"ok": True})

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            async with aiohttp_mod.ClientSession() as session:
                await session.get(
                    "https://api.example.com/auth",
                    headers={"Authorization": "Bearer tok123"},
                )
    finally:
        _active_verifier.reset(token)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["request_headers"]["Authorization"] == "Bearer tok123"

    v.assert_interaction(
        p.request,
        method="GET",
        url="https://api.example.com/auth",
        request_headers={"Authorization": "Bearer tok123"},
        request_body="",
        status=200,
        response_headers={"content-type": "application/json"},
        response_body='{"ok": true}',
    )


# ESCAPE: None -- fake aiohttp response text() method works correctly.
# CHECK: The _FakeAiohttpResponse.text() returns decoded body.
# MUTATION: Not implementing text() would break callers using await response.text().
@pytest.mark.asyncio
async def test_aiohttp_response_text() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/text", body="hello world")

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            response = await _aiohttp_get("https://api.example.com/text")
            text = await response.text()
            assert text == "hello world"
    finally:
        _active_verifier.reset(token)

    v.assert_interaction(
        p.request,
        method="GET",
        url="https://api.example.com/text",
        request_headers={},
        request_body="",
        status=200,
        response_headers={},
        response_body="hello world",
    )


# ESCAPE: None -- fake aiohttp response read() method returns raw bytes.
# CHECK: The _FakeAiohttpResponse.read() returns the body as bytes.
# MUTATION: Not implementing read() would break callers using await response.read().
@pytest.mark.asyncio
async def test_aiohttp_response_read() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/binary", body=b"\x00\x01\x02")

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            response = await _aiohttp_get("https://api.example.com/binary")
            raw = await response.read()
            assert raw == b"\x00\x01\x02"
    finally:
        _active_verifier.reset(token)

    v.assert_interaction(
        p.request,
        method="GET",
        url="https://api.example.com/binary",
        request_headers={},
        request_body="",
        status=200,
        response_headers={},
        response_body="\x00\x01\x02",
    )


# ESCAPE: None -- fake aiohttp response works as async context manager.
# CHECK: The _FakeAiohttpResponse supports async with syntax.
# MUTATION: Not implementing __aenter__/__aexit__ would break callers using async with.
@pytest.mark.asyncio
async def test_aiohttp_response_as_context_manager() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/ctx", json={"ctx": True})

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            async with aiohttp_mod.ClientSession() as session:
                async with session.get("https://api.example.com/ctx") as response:
                    assert response.status == 200
                    body = await response.json()
                    assert body == {"ctx": True}
    finally:
        _active_verifier.reset(token)

    v.assert_interaction(
        p.request,
        method="GET",
        url="https://api.example.com/ctx",
        request_headers={},
        request_body="",
        status=200,
        response_headers={"content-type": "application/json"},
        response_body='{"ctx": true}',
    )


# ---------------------------------------------------------------------------
# HttpErrorConfig tests
# ---------------------------------------------------------------------------


def test_http_error_config_exists_and_has_expected_fields() -> None:
    """HttpErrorConfig dataclass has method, url, params, raises, required fields."""
    from bigfoot.plugins.http import HttpErrorConfig

    exc = ConnectionError("refused")
    config = HttpErrorConfig(
        method="GET",
        url="https://api.example.com/data",
        params=None,
        raises=exc,
        required=True,
    )
    assert config.method == "GET"
    assert config.url == "https://api.example.com/data"
    assert config.params is None
    assert config.raises is exc
    assert config.required is True
    assert isinstance(config.registration_traceback, str)
    assert len(config.registration_traceback) > 0


def test_find_matching_config_returns_http_error_config() -> None:
    """_find_matching_config returns HttpErrorConfig when an error mock matches."""
    from bigfoot.plugins.http import HttpErrorConfig

    v, p = _make_verifier_with_plugin()
    exc = ConnectionError("refused")
    error_config = HttpErrorConfig(
        method="GET",
        url="https://api.example.com/data",
        params=None,
        raises=exc,
    )
    p._mock_queue.append(error_config)

    result = p._find_matching_config("GET", "https://api.example.com/data")
    assert isinstance(result, HttpErrorConfig)
    assert result.raises is exc


# ---------------------------------------------------------------------------
# mock_error tests
# ---------------------------------------------------------------------------


def test_mock_error_appends_to_unified_queue() -> None:
    """mock_error() appends an HttpErrorConfig to the unified _mock_queue."""
    from bigfoot.plugins.http import HttpErrorConfig

    v, p = _make_verifier_with_plugin()
    exc = ConnectionError("refused")
    p.mock_error("GET", "https://api.example.com/data", raises=exc)

    assert len(p._mock_queue) == 1
    assert isinstance(p._mock_queue[0], HttpErrorConfig)
    assert p._mock_queue[0].raises is exc
    assert p._mock_queue[0].method == "GET"
    assert p._mock_queue[0].url == "https://api.example.com/data"


def test_mock_error_uppercases_method() -> None:
    """mock_error() uppercases the HTTP method."""

    v, p = _make_verifier_with_plugin()
    p.mock_error("get", "https://api.example.com/data", raises=ConnectionError("x"))

    assert p._mock_queue[0].method == "GET"


def test_mock_error_default_required_true() -> None:
    """mock_error() defaults required to True."""
    v, p = _make_verifier_with_plugin()
    p.mock_error("GET", "https://api.example.com/data", raises=ConnectionError("x"))

    assert p._mock_queue[0].required is True


def test_mock_error_required_false() -> None:
    """mock_error() accepts required=False."""
    v, p = _make_verifier_with_plugin()
    p.mock_error(
        "GET", "https://api.example.com/data",
        raises=ConnectionError("x"),
        required=False,
    )

    assert p._mock_queue[0].required is False


def test_mock_error_with_params() -> None:
    """mock_error() accepts params for URL matching."""
    v, p = _make_verifier_with_plugin()
    p.mock_error(
        "GET", "https://api.example.com/search",
        raises=ConnectionError("x"),
        params={"q": "test"},
    )

    assert p._mock_queue[0].params == {"q": "test"}


# ---------------------------------------------------------------------------
# Handler error dispatch tests
# ---------------------------------------------------------------------------


def test_httpx_sync_handler_raises_error_config() -> None:
    """httpx sync handler raises the configured exception for HttpErrorConfig."""
    v, p = _make_verifier_with_plugin()
    exc = httpx.ConnectError("Connection refused")
    p.mock_error("GET", "https://api.example.com/data", raises=exc)

    with v.sandbox():
        with pytest.raises(httpx.ConnectError, match="Connection refused"):
            httpx.get("https://api.example.com/data")

    # Interaction should be recorded with request fields + raised
    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["method"] == "GET"
    assert interactions[0].details["url"] == "https://api.example.com/data"
    assert interactions[0].details["raised"] is exc
    assert "status" not in interactions[0].details
    assert "response_headers" not in interactions[0].details
    assert "response_body" not in interactions[0].details


def test_requests_handler_raises_error_config() -> None:
    """requests handler raises the configured exception for HttpErrorConfig."""
    v, p = _make_verifier_with_plugin()
    exc = requests.ConnectionError("DNS resolution failed")
    p.mock_error("GET", "https://api.example.com/data", raises=exc)

    with bigfoot.allow("dns"), v.sandbox():
        with pytest.raises(requests.ConnectionError, match="DNS resolution failed"):
            requests.get("https://api.example.com/data")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["raised"] is exc


def test_urllib_handler_raises_error_config() -> None:
    """urllib handler raises the configured exception for HttpErrorConfig."""
    import urllib.request

    v, p = _make_verifier_with_plugin()
    exc = ConnectionError("refused")
    p.mock_error("GET", "http://api.example.com/data", raises=exc)

    with v.sandbox():
        with pytest.raises(ConnectionError, match="refused"):
            urllib.request.urlopen("http://api.example.com/data")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["raised"] is exc


@pytest.mark.asyncio
async def test_httpx_async_handler_raises_error_config() -> None:
    """httpx async handler raises the configured exception for HttpErrorConfig."""
    v, p = _make_verifier_with_plugin()
    exc = httpx.ConnectError("Connection refused")
    p.mock_error("GET", "https://api.example.com/data", raises=exc)

    async with v.sandbox():
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.ConnectError, match="Connection refused"):
                await client.get("https://api.example.com/data")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["raised"] is exc


@pytest.mark.asyncio
async def test_aiohttp_handler_raises_error_config() -> None:
    """aiohttp handler raises the configured exception for HttpErrorConfig."""
    v, p = _make_verifier_with_plugin()
    exc = ConnectionError("aiohttp refused")
    p.mock_error("GET", "https://api.example.com/data", raises=exc)

    token = _active_verifier.set(v)
    try:
        async with v.sandbox():
            with pytest.raises(ConnectionError, match="aiohttp refused"):
                await _aiohttp_get("https://api.example.com/data")
    finally:
        _active_verifier.reset(token)

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].details["raised"] is exc


def test_mixed_mock_response_and_mock_error_fifo() -> None:
    """mock_response and mock_error are served in FIFO order from unified queue."""
    v, p = _make_verifier_with_plugin()
    p.mock_response("GET", "https://api.example.com/data", json={"ok": True})
    exc = httpx.ConnectError("Connection refused")
    p.mock_error("GET", "https://api.example.com/data", raises=exc)
    p.mock_response("GET", "https://api.example.com/data", json={"retry": True})

    with v.sandbox():
        r1 = httpx.get("https://api.example.com/data")
        with pytest.raises(httpx.ConnectError):
            httpx.get("https://api.example.com/data")
        r3 = httpx.get("https://api.example.com/data")

    assert r1.json() == {"ok": True}
    assert r3.json() == {"retry": True}

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 3
    assert "status" in interactions[0].details  # success
    assert "raised" in interactions[1].details  # error
    assert "status" in interactions[2].details  # success


# ---------------------------------------------------------------------------
# assertable_fields for error interactions
# ---------------------------------------------------------------------------


def test_http_plugin_assertable_fields_error_interaction() -> None:
    """assertable_fields returns request fields + raised for error interactions."""
    v, p = _make_verifier_with_plugin()

    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={
            "method": "GET",
            "url": "https://example.com",
            "request_headers": {},
            "request_body": "",
            "raised": ConnectionError("refused"),
        },
        plugin=p,
    )
    result = p.assertable_fields(interaction)
    assert result == frozenset({"method", "url", "request_headers", "request_body", "raised"})


def test_http_plugin_assertable_fields_success_interaction_unchanged() -> None:
    """assertable_fields for success interaction (request-only mode) is unchanged."""
    v, p = _make_verifier_with_plugin()
    p._asserting_request_only = True

    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={
            "method": "GET",
            "url": "https://example.com",
            "request_headers": {},
            "request_body": "",
            "status": 200,
            "response_headers": {},
            "response_body": "",
        },
        plugin=p,
    )
    result = p.assertable_fields(interaction)
    assert result == frozenset({"method", "url", "request_headers", "request_body"})
    p._asserting_request_only = False


# ---------------------------------------------------------------------------
# assert_request with raised
# ---------------------------------------------------------------------------


def test_assert_request_with_raised_full_roundtrip() -> None:
    """assert_request(raised=...) asserts an error interaction end-to-end."""
    v, p = _make_verifier_with_plugin()
    exc = httpx.ConnectError("Connection refused")
    p.mock_error("GET", "https://api.example.com/data", raises=exc)

    with v.sandbox():
        with pytest.raises(httpx.ConnectError):
            httpx.get("https://api.example.com/data")

    interactions = v._timeline.all_unasserted()
    assert len(interactions) == 1
    recorded_headers = interactions[0].details["request_headers"]

    p.assert_request(
        "GET",
        "https://api.example.com/data",
        headers=recorded_headers,
        body="",
        raised=exc,
    )

    assert len(v._timeline.all_unasserted()) == 0
    v.verify_all()


def test_assert_request_with_raised_returns_none() -> None:
    """assert_request(raised=...) always returns None (never HttpAssertionBuilder)."""
    v, p = _make_verifier_with_plugin()
    exc = httpx.ConnectError("Connection refused")
    p.mock_error("GET", "https://api.example.com/data", raises=exc)

    with v.sandbox():
        with pytest.raises(httpx.ConnectError):
            httpx.get("https://api.example.com/data")

    interactions = v._timeline.all_unasserted()
    recorded_headers = interactions[0].details["request_headers"]

    result = p.assert_request(
        "GET",
        "https://api.example.com/data",
        headers=recorded_headers,
        body="",
        raised=exc,
    )
    assert result is None


def test_assert_request_missing_raised_triggers_missing_fields_error() -> None:
    """Omitting raised= on an error interaction triggers MissingAssertionFieldsError."""
    from bigfoot._errors import MissingAssertionFieldsError

    v, p = _make_verifier_with_plugin()
    exc = httpx.ConnectError("Connection refused")
    p.mock_error("GET", "https://api.example.com/data", raises=exc)

    with v.sandbox():
        with pytest.raises(httpx.ConnectError):
            httpx.get("https://api.example.com/data")

    interactions = v._timeline.all_unasserted()
    recorded_headers = interactions[0].details["request_headers"]

    with pytest.raises(MissingAssertionFieldsError) as exc_info:
        p.assert_request(
            "GET",
            "https://api.example.com/data",
            headers=recorded_headers,
            body="",
            require_response=False,
            # raised= intentionally omitted
        )
    assert "raised" in exc_info.value.missing_fields


# ---------------------------------------------------------------------------
# Format helpers for error interactions
# ---------------------------------------------------------------------------


def test_format_interaction_error_shows_raised() -> None:
    """format_interaction for error interaction shows raised exception type."""
    v, p = _make_verifier_with_plugin()

    exc = httpx.ConnectError("Connection refused")
    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={
            "method": "GET",
            "url": "https://api.example.com/data",
            "request_headers": {},
            "request_body": "",
            "raised": exc,
        },
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == (
        f"[HttpPlugin] GET https://api.example.com/data"
        f" -> raised {type(exc).__module__}.{type(exc).__qualname__}({exc!s})"
    )


def test_format_assert_hint_error_includes_raised() -> None:
    """format_assert_hint for error interaction includes raised= parameter."""
    v, p = _make_verifier_with_plugin()

    exc = httpx.ConnectError("Connection refused")
    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={
            "method": "GET",
            "url": "https://api.example.com/data",
            "request_headers": {},
            "request_body": "",
            "raised": exc,
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "http.assert_request(\n"
        '    "GET",\n'
        '    "https://api.example.com/data",\n'
        "    headers={},\n"
        "    body='',\n"
        f"    raised={exc!r},\n"
        ")"
    )


def test_format_mock_hint_error_shows_mock_error() -> None:
    """format_mock_hint for error interaction shows mock_error() call."""
    v, p = _make_verifier_with_plugin()

    exc = httpx.ConnectError("Connection refused")
    interaction = Interaction(
        source_id="http:request",
        sequence=0,
        details={
            "method": "GET",
            "url": "https://api.example.com/data",
            "request_headers": {},
            "request_body": "",
            "raised": exc,
        },
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == f'http.mock_error("GET", "https://api.example.com/data", raises={exc!r})'


def test_format_unmocked_hint_suggests_both_options() -> None:
    """format_unmocked_hint suggests both mock_response() and mock_error()."""
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("http:request", ("GET", "https://api.example.com/data"), {})
    assert "mock_response" in result
    assert "mock_error" in result


# ---------------------------------------------------------------------------
# get_unused_mocks and format_unused_mock_hint for error mocks
# ---------------------------------------------------------------------------


def test_get_unused_mocks_returns_error_configs() -> None:
    """get_unused_mocks returns HttpErrorConfig entries from the unified queue."""
    from bigfoot.plugins.http import HttpErrorConfig

    v, p = _make_verifier_with_plugin()
    p.mock_error("GET", "https://api.example.com/data", raises=ConnectionError("x"))

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert isinstance(unused[0], HttpErrorConfig)


def test_get_unused_mocks_excludes_optional_error_configs() -> None:
    """get_unused_mocks excludes HttpErrorConfig entries with required=False."""
    v, p = _make_verifier_with_plugin()
    p.mock_error(
        "GET", "https://api.example.com/data",
        raises=ConnectionError("x"),
        required=False,
    )

    unused = p.get_unused_mocks()
    assert len(unused) == 0


def test_format_unused_mock_hint_for_error_config() -> None:
    """format_unused_mock_hint handles HttpErrorConfig entries."""

    v, p = _make_verifier_with_plugin()
    exc = ConnectionError("refused")
    p.mock_error("GET", "https://api.example.com/data", raises=exc)

    unused = p.get_unused_mocks()
    assert len(unused) == 1

    result = p.format_unused_mock_hint(unused[0])
    assert "error mock" in result
    assert "GET" in result
    assert "https://api.example.com/data" in result
    assert isinstance(result, str)


def test_unused_error_mock_raises_at_verify_all() -> None:
    """An unused required error mock causes verify_all() to raise."""
    from bigfoot._errors import UnusedMocksError, VerificationError

    v, p = _make_verifier_with_plugin()
    p.mock_error("GET", "https://api.example.com/data", raises=ConnectionError("x"))

    with v.sandbox():
        pass  # Never trigger the error mock

    with pytest.raises((UnusedMocksError, VerificationError)):
        v.verify_all()
