# src/bigfoot/pytest_plugin.py
"""pytest fixture registration for bigfoot."""
import pytest

from bigfoot._verifier import StrictVerifier


@pytest.fixture
def bigfoot_verifier(request: pytest.FixtureRequest) -> StrictVerifier:
    """Provides a StrictVerifier with automatic teardown via addfinalizer.

    verify_all() is called at teardown. The sandbox is NOT automatically
    activated -- the test controls sandbox lifetime.

    Usage:
        def test_something(bigfoot_verifier):
            http = HttpPlugin(bigfoot_verifier)
            http.mock_response("GET", "https://api.example.com/data", json={})
            with bigfoot_verifier.sandbox():
                response = httpx.get("https://api.example.com/data")
                bigfoot_verifier.assert_interaction(http.request, method="GET")
    """
    verifier = StrictVerifier()

    def _run_verification() -> None:
        verifier.verify_all()

    request.addfinalizer(_run_verification)
    return verifier
