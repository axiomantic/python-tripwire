"""End-to-end integration tests for the new mock/spy API."""

import sys
import types

import pytest

import bigfoot
from bigfoot._errors import UnassertedInteractionsError
from bigfoot._verifier import StrictVerifier


pytestmark = pytest.mark.integration


def _create_fake_module(name: str, **attrs: object) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def test_mock_register_sandbox_call_assert(bigfoot_verifier: StrictVerifier) -> None:
    """Full flow: register mock, sandbox, call, assert."""
    mod = _create_fake_module("_e2e_mock1", fn=lambda: "real")
    try:
        mock = bigfoot.mock("_e2e_mock1:fn")
        mock.returns("mocked")

        with bigfoot:
            result = mod.fn()

        assert result == "mocked"
        mock.assert_call(args=(), kwargs={})
        bigfoot.verify_all()
    finally:
        del sys.modules["_e2e_mock1"]


def test_spy_register_sandbox_call_assert(bigfoot_verifier: StrictVerifier) -> None:
    """Full flow: register spy, sandbox, call real, assert with returned."""
    mod = _create_fake_module("_e2e_spy1", fn=lambda x: x * 3)
    try:
        spy = bigfoot.spy("_e2e_spy1:fn")

        with bigfoot:
            result = mod.fn(7)

        assert result == 21
        spy.assert_call(args=(7,), kwargs={}, returned=21)
        bigfoot.verify_all()
    finally:
        del sys.modules["_e2e_spy1"]


def test_mock_raises_records_raised(bigfoot_verifier: StrictVerifier) -> None:
    """Mock with .raises() records raised in details."""
    mod = _create_fake_module("_e2e_raises", fn=lambda: "real")
    try:
        mock = bigfoot.mock("_e2e_raises:fn")
        exc = ValueError("test error")
        mock.raises(exc)

        with bigfoot:
            with pytest.raises(ValueError, match="test error"):
                mod.fn()

        mock.assert_call(args=(), kwargs={}, raised=exc)
        bigfoot.verify_all()
    finally:
        del sys.modules["_e2e_raises"]


def test_individual_mock_enforce_false(bigfoot_verifier: StrictVerifier) -> None:
    """Individual mock activation (with mock:) uses enforce=False."""
    mod = _create_fake_module("_e2e_individual", fn=lambda: "real")
    try:
        mock = bigfoot.mock("_e2e_individual:fn")
        mock.returns("mocked")

        with mock:
            result = mod.fn()

        assert result == "mocked"
        # Do NOT assert -- enforce=False means verify_all() should not complain
        bigfoot.verify_all()  # should not raise
    finally:
        del sys.modules["_e2e_individual"]


def test_mock_object_api(bigfoot_verifier: StrictVerifier) -> None:
    """bigfoot.mock.object() patches a specific object's attribute."""

    class Service:
        def compute(self, x: int) -> int:
            return x + 1

    svc = Service()
    mock = bigfoot.mock.object(svc, "compute")
    mock.returns(42)

    with bigfoot:
        result = svc.compute(10)

    assert result == 42
    mock.assert_call(args=(10,), kwargs={})


def test_sandbox_plus_individual_mock(bigfoot_verifier: StrictVerifier) -> None:
    """Individual activation then sandbox activation works correctly."""
    mod = _create_fake_module("_e2e_combo", fn=lambda: "real")
    try:
        mock = bigfoot.mock("_e2e_combo:fn")
        mock.returns("setup_val")

        # Individual activation for setup (enforce=False)
        with mock:
            setup_result = mod.fn()
        assert setup_result == "setup_val"

        # Now register another return for sandbox use
        mock.returns("sandbox_val")

        with bigfoot:
            sandbox_result = mod.fn()
        assert sandbox_result == "sandbox_val"

        # Both interactions are on the timeline. Assert both in FIFO order.
        # The first is enforce=False (individual), the second is enforce=True (sandbox).
        mock.assert_call(args=(), kwargs={})  # individual (enforce=False)
        mock.assert_call(args=(), kwargs={})  # sandbox (enforce=True)
        bigfoot.verify_all()
    finally:
        del sys.modules["_e2e_combo"]


async def test_async_context_manager(bigfoot_verifier: StrictVerifier) -> None:
    """async with mock: works for individual activation."""
    mod = _create_fake_module("_e2e_async_cm", fn=lambda: "real")
    try:
        mock = bigfoot.mock("_e2e_async_cm:fn")
        mock.returns("async_mocked")

        async with mock:
            result = mod.fn()

        assert result == "async_mocked"
        bigfoot.verify_all()  # enforce=False, should not raise
    finally:
        del sys.modules["_e2e_async_cm"]
