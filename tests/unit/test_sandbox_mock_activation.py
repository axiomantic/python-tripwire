"""Tests for SandboxContext mock activation and deactivation."""

import sys
import types

from tripwire._mock_plugin import ImportSiteMock, MockPlugin
from tripwire._verifier import SandboxContext, StrictVerifier


def _create_fake_module(name: str, **attrs: object) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _drain_unused_mocks(plugin: MockPlugin) -> None:
    """Mark all unused mock configs as not required to prevent teardown errors."""
    for mock in plugin._mocks:
        for method_proxy in mock._methods.values():
            for config in method_proxy._config_queue:
                config.required = False


def test_sandbox_activates_mocks_with_enforce_true(tripwire_verifier: StrictVerifier) -> None:
    """Sandbox entry activates all registered mocks with enforce=True."""
    mod = _create_fake_module("_test_sandbox_act", fn=lambda: "real")
    try:
        plugin = MockPlugin(tripwire_verifier)
        mock = ImportSiteMock(path="_test_sandbox_act:fn", plugin=plugin)
        mock.returns("mocked")

        ctx = SandboxContext(tripwire_verifier)
        ctx._enter()
        assert mock._active is True
        assert mock._enforce is True
        ctx._exit()
        _drain_unused_mocks(plugin)
    finally:
        del sys.modules["_test_sandbox_act"]


def test_sandbox_deactivates_mocks_on_exit(tripwire_verifier: StrictVerifier) -> None:
    """Sandbox exit deactivates all registered mocks."""
    mod = _create_fake_module("_test_sandbox_deact", fn=lambda: "real")
    try:
        plugin = MockPlugin(tripwire_verifier)
        mock = ImportSiteMock(path="_test_sandbox_deact:fn", plugin=plugin)
        mock.returns("mocked")

        ctx = SandboxContext(tripwire_verifier)
        ctx._enter()
        assert mock._active is True
        ctx._exit()
        assert mock._active is False
        assert mod.fn() == "real"  # original restored
        _drain_unused_mocks(plugin)
    finally:
        del sys.modules["_test_sandbox_deact"]


def test_sandbox_deactivates_mocks_in_reverse_order(tripwire_verifier: StrictVerifier) -> None:
    """Mocks are deactivated in reverse activation order."""
    deactivation_order: list[str] = []

    mod = _create_fake_module(
        "_test_sandbox_order", fn1=lambda: "r1", fn2=lambda: "r2"
    )
    try:
        plugin = MockPlugin(tripwire_verifier)
        m1 = ImportSiteMock(path="_test_sandbox_order:fn1", plugin=plugin)
        m1.returns("m1")
        m2 = ImportSiteMock(path="_test_sandbox_order:fn2", plugin=plugin)
        m2.returns("m2")

        # Patch _deactivate to track order
        orig_deact_1 = m1._deactivate
        orig_deact_2 = m2._deactivate

        def track1() -> None:
            deactivation_order.append("m1")
            orig_deact_1()

        def track2() -> None:
            deactivation_order.append("m2")
            orig_deact_2()

        ctx = SandboxContext(tripwire_verifier)
        ctx._enter()

        # Monkey-patch deactivate to track order
        m1._deactivate = track1  # type: ignore[assignment]
        m2._deactivate = track2  # type: ignore[assignment]

        ctx._exit()
        assert deactivation_order == ["m2", "m1"]  # reverse order
        _drain_unused_mocks(plugin)
    finally:
        del sys.modules["_test_sandbox_order"]


def test_sandbox_deactivates_mocks_before_plugins(tripwire_verifier: StrictVerifier) -> None:
    """Mocks deactivate before plugins during sandbox exit."""
    order: list[str] = []
    mod = _create_fake_module("_test_sandbox_order2", fn=lambda: "real")
    try:
        plugin = MockPlugin(tripwire_verifier)
        orig_deactivate = plugin.deactivate

        def track_plugin_deactivate() -> None:
            order.append("plugin")
            orig_deactivate()

        plugin.deactivate = track_plugin_deactivate  # type: ignore[assignment]

        mock = ImportSiteMock(path="_test_sandbox_order2:fn", plugin=plugin)
        mock.returns("mocked")

        orig_mock_deact = mock._deactivate

        def track_mock_deactivate() -> None:
            order.append("mock")
            orig_mock_deact()

        ctx = SandboxContext(tripwire_verifier)
        ctx._enter()
        mock._deactivate = track_mock_deactivate  # type: ignore[assignment]
        ctx._exit()

        # Mock should deactivate before plugin
        mock_idx = order.index("mock")
        plugin_idx = order.index("plugin")
        assert mock_idx < plugin_idx
        _drain_unused_mocks(plugin)
    finally:
        del sys.modules["_test_sandbox_order2"]
