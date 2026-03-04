# tests/unit/test_init.py
"""Unit tests for bigfoot.__init__ public API.

Verifies that all public names are importable directly from the top-level
package and that __all__ contains exactly the expected names.
"""

import pytest


def test_all_contains_expected_names() -> None:
    """__all__ must be exactly the declared public API set."""
    # ESCAPE: if __all__ contained extras or was missing entries callers get
    # wrong autocomplete / wildcard-import results
    import bigfoot

    expected_all = {
        "StrictVerifier",
        "SandboxContext",
        "InAnyOrderContext",
        "MockPlugin",
        "BigfootError",
        "UnmockedInteractionError",
        "UnassertedInteractionsError",
        "UnusedMocksError",
        "VerificationError",
        "InteractionMismatchError",
        "SandboxNotActiveError",
        "ConflictError",
    }
    assert set(bigfoot.__all__) == expected_all


def test_strict_verifier_importable() -> None:
    """StrictVerifier must be importable from the top-level package."""
    # ESCAPE: if the import was missing or aliased wrongly instantiation would fail
    from bigfoot import StrictVerifier
    from bigfoot._verifier import StrictVerifier as _StrictVerifier

    assert StrictVerifier is _StrictVerifier


def test_sandbox_context_importable() -> None:
    """SandboxContext must be importable from the top-level package."""
    from bigfoot import SandboxContext
    from bigfoot._verifier import SandboxContext as _SandboxContext

    assert SandboxContext is _SandboxContext


def test_in_any_order_context_importable() -> None:
    """InAnyOrderContext must be importable from the top-level package."""
    from bigfoot import InAnyOrderContext
    from bigfoot._verifier import InAnyOrderContext as _InAnyOrderContext

    assert InAnyOrderContext is _InAnyOrderContext


def test_mock_plugin_importable() -> None:
    """MockPlugin must be importable from the top-level package."""
    from bigfoot import MockPlugin
    from bigfoot._mock_plugin import MockPlugin as _MockPlugin

    assert MockPlugin is _MockPlugin


def test_bigfoot_error_importable() -> None:
    """BigfootError must be importable from the top-level package."""
    from bigfoot import BigfootError
    from bigfoot._errors import BigfootError as _BigfootError

    assert BigfootError is _BigfootError


def test_unmocked_interaction_error_importable() -> None:
    """UnmockedInteractionError must be importable from the top-level package."""
    from bigfoot import UnmockedInteractionError
    from bigfoot._errors import UnmockedInteractionError as _UnmockedInteractionError

    assert UnmockedInteractionError is _UnmockedInteractionError


def test_unasserted_interactions_error_importable() -> None:
    """UnassertedInteractionsError must be importable from the top-level package."""
    from bigfoot import UnassertedInteractionsError
    from bigfoot._errors import UnassertedInteractionsError as _UnassertedInteractionsError

    assert UnassertedInteractionsError is _UnassertedInteractionsError


def test_unused_mocks_error_importable() -> None:
    """UnusedMocksError must be importable from the top-level package."""
    from bigfoot import UnusedMocksError
    from bigfoot._errors import UnusedMocksError as _UnusedMocksError

    assert UnusedMocksError is _UnusedMocksError


def test_verification_error_importable() -> None:
    """VerificationError must be importable from the top-level package."""
    from bigfoot import VerificationError
    from bigfoot._errors import VerificationError as _VerificationError

    assert VerificationError is _VerificationError


def test_interaction_mismatch_error_importable() -> None:
    """InteractionMismatchError must be importable from the top-level package."""
    from bigfoot import InteractionMismatchError
    from bigfoot._errors import InteractionMismatchError as _InteractionMismatchError

    assert InteractionMismatchError is _InteractionMismatchError


def test_sandbox_not_active_error_importable() -> None:
    """SandboxNotActiveError must be importable from the top-level package."""
    from bigfoot import SandboxNotActiveError
    from bigfoot._errors import SandboxNotActiveError as _SandboxNotActiveError

    assert SandboxNotActiveError is _SandboxNotActiveError


def test_conflict_error_importable() -> None:
    """ConflictError must be importable from the top-level package."""
    from bigfoot import ConflictError
    from bigfoot._errors import ConflictError as _ConflictError

    assert ConflictError is _ConflictError


def test_http_plugin_importable_if_http_extra_installed() -> None:
    """HttpPlugin must be importable from bigfoot if [http] extra is installed."""
    # ESCAPE: if HttpPlugin import was missing from __init__ when http extra is
    # installed, users would have to import from bigfoot.plugins.http directly
    try:
        import httpx  # noqa: F401
        import requests  # noqa: F401
        http_available = True
    except ImportError:
        http_available = False

    if not http_available:
        pytest.skip("http extra not installed")

    import bigfoot

    assert hasattr(bigfoot, "HttpPlugin")

    from bigfoot import HttpPlugin
    from bigfoot.plugins.http import HttpPlugin as _HttpPlugin

    assert HttpPlugin is _HttpPlugin
