"""Tests for all-wildcard assertion detection."""

import pytest

import bigfoot
from bigfoot._context import _current_test_verifier
from bigfoot._errors import AllWildcardAssertionError
from bigfoot._verifier import StrictVerifier

# Only run if dirty-equals is available
dirty_equals = pytest.importorskip("dirty_equals")
from dirty_equals import AnyThing  # noqa: E402


@pytest.fixture(autouse=True)
def _verifier_context():
    """Set up a test verifier context for each test."""
    StrictVerifier._suppress_direct_warning = True
    v = StrictVerifier()
    token = _current_test_verifier.set(v)
    try:
        yield v
    finally:
        _current_test_verifier.reset(token)
        StrictVerifier._suppress_direct_warning = False


# ---------------------------------------------------------------------------
# Import HttpPlugin conditionally
# ---------------------------------------------------------------------------

httpx = pytest.importorskip("httpx")
from bigfoot.plugins.http import HttpPlugin  # noqa: E402


def _reset_http_install():
    with HttpPlugin._install_lock:
        HttpPlugin._install_count = 0
        HttpPlugin.__new__(HttpPlugin).restore_patches()


@pytest.fixture(autouse=True)
def _clean_http():
    _reset_http_install()
    yield
    _reset_http_install()


def test_all_wildcard_assertion_raises():
    """All-wildcard assertion must raise AllWildcardAssertionError."""
    bigfoot.http.mock_response("GET", "http://test/api", json={"ok": True})
    with bigfoot:
        httpx.get("http://test/api")

    with pytest.raises(AllWildcardAssertionError, match="verifies nothing"):
        bigfoot.http.assert_request(
            method=AnyThing(),
            url=AnyThing(),
            headers=AnyThing(),
            body=AnyThing(),
            require_response=False,
        )


def test_partial_wildcard_is_allowed():
    """Partial wildcards (some real values, some AnyThing) must work normally."""
    bigfoot.http.mock_response("GET", "http://test/api", json={"ok": True})
    with bigfoot:
        httpx.get("http://test/api")

    # This should NOT raise AllWildcardAssertionError
    bigfoot.http.assert_request(
        method="GET",
        url=AnyThing(),
        headers=AnyThing(),
        body=AnyThing(),
        require_response=False,
    )


def test_all_wildcard_error_shows_real_values():
    """AllWildcardAssertionError should include copy-pasteable real values."""
    bigfoot.http.mock_response("GET", "http://test/api", json={"ok": True})
    with bigfoot:
        httpx.get("http://test/api")

    with pytest.raises(AllWildcardAssertionError) as exc_info:
        bigfoot.http.assert_request(
            method=AnyThing(),
            url=AnyThing(),
            headers=AnyThing(),
            body=AnyThing(),
            require_response=False,
        )

    # The error message should contain the real values from format_assert_hint
    msg = str(exc_info.value)
    assert "assert_request" in msg
    assert "GET" in msg


def test_all_wildcard_detection_in_any_order():
    """All-wildcard detection works inside in_any_order blocks too."""
    bigfoot.http.mock_response("GET", "http://test/api", json={"ok": True})
    with bigfoot:
        httpx.get("http://test/api")

    with pytest.raises(AllWildcardAssertionError, match="verifies nothing"):
        with bigfoot.in_any_order():
            bigfoot.http.assert_request(
                method=AnyThing(),
                url=AnyThing(),
                headers=AnyThing(),
                body=AnyThing(),
                require_response=False,
            )
