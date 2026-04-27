# tests/unit/test_verifier.py
import sys
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup

from tripwire._context import _active_verifier, _any_order_depth
from tripwire._errors import (
    AssertionInsideSandboxError,
    InteractionMismatchError,
    UnassertedInteractionsError,
    UnusedMocksError,
    VerificationError,
)
from tripwire._timeline import Interaction
from tripwire._verifier import StrictVerifier

# --- Helpers ---


def _make_mock_plugin(verifier: StrictVerifier) -> MagicMock:
    """Create a mock plugin that is properly registered on the verifier."""
    plugin = MagicMock()
    plugin.get_unused_mocks.return_value = []
    plugin.matches.return_value = True
    plugin.format_interaction.return_value = "[Mock] test"
    plugin.format_assert_hint.return_value = "verifier.assert_interaction(...)"
    plugin.format_unused_mock_hint.return_value = "remove mock"
    # 'assertable_fields' starts with 'assert' so MagicMock's __getattr__ blocks
    # direct access. Use setattr() to bypass the protection.
    setattr(plugin, "assertable_fields", Mock(return_value=frozenset()))
    verifier._register_plugin(plugin)
    return plugin


def _inject_interaction(
    verifier: StrictVerifier, source_id: str, plugin: Any = None
) -> Interaction:
    if plugin is None:
        plugin = MagicMock()
        plugin.matches.return_value = True
        plugin.format_interaction.return_value = f"[Mock] {source_id}"
        plugin.format_assert_hint.return_value = f"assert {source_id}"
        setattr(plugin, "assertable_fields", Mock(return_value=frozenset()))
    interaction = Interaction(source_id=source_id, sequence=0, details={}, plugin=plugin)
    verifier._timeline.append(interaction)
    return interaction


# --- StrictVerifier basic tests ---


def test_verifier_init_auto_creates_plugins() -> None:
    """StrictVerifier() auto-instantiates all available plugins."""
    v = StrictVerifier()
    # Should have at least the always-available plugins
    plugin_types = {type(p).__name__ for p in v._plugins}
    assert "SubprocessPlugin" in plugin_types
    assert "PopenPlugin" in plugin_types
    assert "SmtpPlugin" in plugin_types
    assert "SocketPlugin" in plugin_types
    assert "DatabasePlugin" in plugin_types


def test_register_plugin_idempotent() -> None:
    """Registering the same plugin type twice silently skips the duplicate."""
    v = StrictVerifier()
    initial_count = len(v._plugins)
    # Create a plugin of a type that's already auto-registered
    from tripwire.plugins.subprocess import SubprocessPlugin

    SubprocessPlugin(v)  # Should NOT raise, should be silently skipped
    assert len(v._plugins) == initial_count  # count unchanged


def test_verifier_respects_disabled_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """disabled_plugins config excludes named plugins from auto-instantiation."""
    monkeypatch.setattr(
        "tripwire._verifier.load_tripwire_config",
        lambda: {"disabled_plugins": ["subprocess"]},
    )
    v = StrictVerifier()
    plugin_types = {type(p).__name__ for p in v._plugins}
    assert "SubprocessPlugin" not in plugin_types
    # Other plugins still present
    assert "PopenPlugin" in plugin_types


def test_verifier_respects_enabled_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """enabled_plugins config includes only named plugins."""
    monkeypatch.setattr(
        "tripwire._verifier.load_tripwire_config",
        lambda: {"enabled_plugins": ["subprocess"]},
    )
    v = StrictVerifier()
    plugin_types = {type(p).__name__ for p in v._plugins}
    assert plugin_types == {"SubprocessPlugin"}


def test_multi_sandbox_same_verifier() -> None:
    """Two sequential sandbox blocks on the same verifier succeed."""
    v = StrictVerifier()
    with v.sandbox():
        pass
    with v.sandbox():
        pass
    # Should not raise (no interactions to assert)


def test_verify_all_passes_when_empty() -> None:
    v = StrictVerifier()
    v.verify_all()  # No error


def test_verify_all_raises_unasserted_when_timeline_not_empty() -> None:
    v = StrictVerifier()
    _inject_interaction(v, "mock:Svc.method")
    with pytest.raises(UnassertedInteractionsError) as exc_info:
        v.verify_all()
    assert exc_info.value.interactions[0].source_id == "mock:Svc.method"
    assert len(exc_info.value.interactions) == 1


def test_verify_all_raises_unused_mocks_when_plugin_has_unused() -> None:
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    unused_mock = MagicMock()
    plugin.get_unused_mocks.return_value = [unused_mock]
    plugin.format_unused_mock_hint.return_value = "remove mock or required=False"
    with pytest.raises(UnusedMocksError) as exc_info:
        v.verify_all()
    assert exc_info.value.mocks == [unused_mock]
    assert len(exc_info.value.mocks) == 1


def test_verify_all_raises_verification_error_when_both_fail() -> None:
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    _inject_interaction(v, "mock:Svc.method", plugin=plugin)
    unused_mock = MagicMock()
    plugin.get_unused_mocks.return_value = [unused_mock]
    plugin.format_unused_mock_hint.return_value = "remove mock"
    with pytest.raises(VerificationError) as exc_info:
        v.verify_all()
    assert exc_info.value.unasserted is not None
    assert exc_info.value.unused is not None
    assert isinstance(exc_info.value.unasserted, UnassertedInteractionsError)
    assert isinstance(exc_info.value.unused, UnusedMocksError)


# --- sandbox() tests ---


def test_sandbox_sets_active_verifier() -> None:
    v = StrictVerifier()
    assert _active_verifier.get() is None
    with v.sandbox():
        assert _active_verifier.get() is v
    assert _active_verifier.get() is None


def test_sandbox_resets_active_verifier_on_exception() -> None:
    v = StrictVerifier()
    try:
        with v.sandbox():
            raise RuntimeError("test error")
    except RuntimeError:
        pass
    assert _active_verifier.get() is None


def test_sandbox_activates_and_deactivates_plugins() -> None:
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    with v.sandbox():
        plugin.activate.assert_called_once()
    plugin.deactivate.assert_called_once()


def test_sandbox_deactivates_all_even_if_one_raises() -> None:
    v = StrictVerifier()
    p1 = _make_mock_plugin(v)
    p2 = _make_mock_plugin(v)
    p2.deactivate.side_effect = RuntimeError("deactivate failed")
    try:
        with v.sandbox():
            pass
    except BaseExceptionGroup:
        pass
    p1.deactivate.assert_called_once()
    p2.deactivate.assert_called_once()


@pytest.mark.asyncio
async def test_sandbox_async_protocol() -> None:
    v = StrictVerifier()
    assert _active_verifier.get() is None
    async with v.sandbox():
        assert _active_verifier.get() is v
    assert _active_verifier.get() is None


# --- assert_interaction() tests ---


def test_assert_interaction_fifo_matches_next() -> None:
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    i1 = _inject_interaction(v, "mock:Svc.a", plugin=plugin)
    i2 = _inject_interaction(v, "mock:Svc.b", plugin=plugin)

    source = MagicMock()
    source.source_id = "mock:Svc.a"

    v.assert_interaction(source)
    assert i1._asserted is True
    assert i2._asserted is False


def test_assert_interaction_fifo_raises_on_wrong_source() -> None:
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    _inject_interaction(v, "mock:Svc.a", plugin=plugin)

    source = MagicMock()
    source.source_id = "mock:Svc.b"

    with pytest.raises(InteractionMismatchError) as exc_info:
        v.assert_interaction(source)
    assert exc_info.value.expected == {"source_id": "mock:Svc.b"}
    assert exc_info.value.actual.source_id == "mock:Svc.a"


def test_assert_interaction_fifo_raises_when_fields_no_match() -> None:
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    plugin.matches.return_value = False
    _inject_interaction(v, "mock:Svc.a", plugin=plugin)

    source = MagicMock()
    source.source_id = "mock:Svc.a"

    with pytest.raises(InteractionMismatchError) as exc_info:
        v.assert_interaction(source, expected_field="x")
    assert exc_info.value.expected == {"source_id": "mock:Svc.a", "expected_field": "x"}
    assert exc_info.value.actual.source_id == "mock:Svc.a"


# --- in_any_order() tests ---


def test_in_any_order_allows_unordered_assertion() -> None:
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    i1 = _inject_interaction(v, "mock:Svc.a", plugin=plugin)
    i2 = _inject_interaction(v, "mock:Svc.b", plugin=plugin)

    source_b = MagicMock()
    source_b.source_id = "mock:Svc.b"
    source_a = MagicMock()
    source_a.source_id = "mock:Svc.a"

    with v.in_any_order():
        v.assert_interaction(source_b)
        v.assert_interaction(source_a)

    assert i1._asserted is True
    assert i2._asserted is True


def test_in_any_order_raises_if_no_match() -> None:
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    _inject_interaction(v, "mock:Svc.a", plugin=plugin)

    source = MagicMock()
    source.source_id = "mock:Svc.nonexistent"

    with pytest.raises(InteractionMismatchError) as exc_info:
        with v.in_any_order():
            v.assert_interaction(source)
    assert exc_info.value.expected == {"source_id": "mock:Svc.nonexistent"}
    assert exc_info.value.actual is None


def test_in_any_order_depth_resets_after_exit() -> None:
    v = StrictVerifier()
    assert _any_order_depth.get() == 0
    with v.in_any_order():
        assert _any_order_depth.get() == 1
    assert _any_order_depth.get() == 0


# --- Coverage gap: verifier.mock() reuses existing MockPlugin ---


def test_mock_reuses_existing_mock_plugin() -> None:
    """verifier.mock() called twice finds the existing MockPlugin and doesn't create a second."""
    from tripwire._mock_plugin import MockPlugin

    v = StrictVerifier()
    # First call creates MockPlugin and mock
    mock_a = v.mock("os.path:sep")
    # Exactly one MockPlugin should be registered
    plugins_after_first = [p for p in v._plugins if isinstance(p, MockPlugin)]
    assert len(plugins_after_first) == 1

    # Second call must reuse the same MockPlugin (not register another)
    mock_b = v.mock("os.path:join")
    plugins_after_second = [p for p in v._plugins if isinstance(p, MockPlugin)]
    assert len(plugins_after_second) == 1
    assert plugins_after_first[0] is plugins_after_second[0]

    # Both mocks should be distinct
    assert mock_a is not mock_b


def test_mock_skips_non_mock_plugins_when_searching() -> None:
    """verifier.mock() iterates past non-MockPlugin entries to find an existing MockPlugin."""
    from tripwire._mock_plugin import MockPlugin

    v = StrictVerifier()
    # Register a non-MockPlugin first (a raw MagicMock that passes type() check)
    non_mock = _make_mock_plugin(v)

    # Calling mock() with a non-MockPlugin already registered must create exactly one
    # MockPlugin (the search will iterate past non_mock, find none, and create one).
    mock_obj = v.mock("os.path:sep")
    mock_plugins = [p for p in v._plugins if isinstance(p, MockPlugin)]
    assert len(mock_plugins) == 1
    assert mock_obj is not None


# --- Coverage gap: SandboxContext._enter() activation failure recovery ---


def test_sandbox_activation_failure_deactivates_already_activated_plugins() -> None:
    """When plugin.activate() raises, previously-activated plugins are deactivated."""
    v = StrictVerifier()
    p1 = _make_mock_plugin(v)
    p2 = _make_mock_plugin(v)
    p2.activate.side_effect = RuntimeError("activation failed")

    with pytest.raises(BaseExceptionGroup, match="tripwire sandbox activation failed"):
        with v.sandbox():
            pass  # pragma: no cover - never reached

    p1.activate.assert_called_once()
    p1.deactivate.assert_called_once()
    p2.activate.assert_called_once()
    p2.deactivate.assert_not_called()


def test_sandbox_activation_failure_with_deactivation_also_failing() -> None:
    """When activate raises AND cleanup deactivate also raises, both errors are collected."""
    v = StrictVerifier()
    p1 = _make_mock_plugin(v)
    p2 = _make_mock_plugin(v)
    p1.deactivate.side_effect = RuntimeError("cleanup failed too")
    p2.activate.side_effect = RuntimeError("activation failed")

    with pytest.raises(BaseExceptionGroup) as exc_info:
        with v.sandbox():
            pass  # pragma: no cover

    errors = exc_info.value.exceptions
    assert len(errors) == 2
    assert any("activation failed" in str(e) for e in errors)
    assert any("cleanup failed too" in str(e) for e in errors)


def test_sandbox_activation_failure_resets_context_var() -> None:
    """When plugin.activate() raises, the _active_verifier ContextVar is reset."""
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    plugin.activate.side_effect = RuntimeError("activation failed")

    assert _active_verifier.get() is None
    with pytest.raises(BaseExceptionGroup):
        with v.sandbox():
            pass  # pragma: no cover

    assert _active_verifier.get() is None


def test_sandbox_deactivation_failure_still_resets_context_var() -> None:
    """When plugin.deactivate() raises, the _active_verifier ContextVar is still reset."""
    v = StrictVerifier()
    p = _make_mock_plugin(v)
    p.deactivate.side_effect = RuntimeError("deactivate failed")

    with pytest.raises(BaseExceptionGroup, match="tripwire sandbox deactivation failed"):
        with v.sandbox():
            pass

    assert _active_verifier.get() is None


# --- Coverage gap: async InAnyOrderContext ---


@pytest.mark.asyncio
async def test_in_any_order_async_protocol() -> None:
    """InAnyOrderContext supports async with syntax."""
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    i1 = _inject_interaction(v, "mock:Svc.a", plugin=plugin)
    i2 = _inject_interaction(v, "mock:Svc.b", plugin=plugin)

    source_b = MagicMock()
    source_b.source_id = "mock:Svc.b"
    source_a = MagicMock()
    source_a.source_id = "mock:Svc.a"

    async with v.in_any_order():
        v.assert_interaction(source_b)
        v.assert_interaction(source_a)

    assert i1._asserted is True
    assert i2._asserted is True


# --- Coverage gap: _format_mismatch_error with non-empty remaining list ---


def test_format_mismatch_error_with_empty_timeline() -> None:
    """_format_mismatch_error with actual=None and empty remaining produces clean hint."""
    v = StrictVerifier()
    # Timeline is empty -- no interactions at all
    source = MagicMock()
    source.source_id = "mock:Svc.method"

    with pytest.raises(InteractionMismatchError) as exc_info:
        v.assert_interaction(source)

    # Hint should mention expected source but NOT show remaining timeline section
    assert "mock:Svc.method" in exc_info.value.hint
    assert "Remaining timeline" not in exc_info.value.hint
    assert "timeline is empty" in exc_info.value.hint


def test_format_mismatch_error_includes_remaining_when_present() -> None:
    """_format_mismatch_error includes remaining interactions when the list is non-empty."""
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    _inject_interaction(v, "mock:Svc.a", plugin=plugin)
    _inject_interaction(v, "mock:Svc.b", plugin=plugin)

    # i1 is the actual next; i2 is remaining; we assert the wrong source
    source = MagicMock()
    source.source_id = "mock:Svc.wrong"

    with pytest.raises(InteractionMismatchError) as exc_info:
        v.assert_interaction(source)

    # The hint should mention the expected source and show remaining interaction count
    assert "mock:Svc.wrong" in exc_info.value.hint
    assert "Remaining timeline" in exc_info.value.hint
    # Both interactions appear in the remaining section (formatted via format_interaction)
    assert exc_info.value.hint.count("[Mock] test") >= 2


# --- Sandbox-active guard tests ---


def test_assert_interaction_raises_if_sandbox_active() -> None:
    """assert_interaction() raises AssertionInsideSandboxError when the verifier's sandbox is active."""
    v = StrictVerifier()
    source = MagicMock()
    source.source_id = "mock:Svc.method"
    with v.sandbox():
        with pytest.raises(AssertionInsideSandboxError):
            v.assert_interaction(source)


def test_in_any_order_raises_if_sandbox_active() -> None:
    """in_any_order() raises AssertionInsideSandboxError when the verifier's sandbox is active."""
    v = StrictVerifier()
    with v.sandbox():
        with pytest.raises(AssertionInsideSandboxError):
            v.in_any_order()


def test_verify_all_raises_if_sandbox_active() -> None:
    """verify_all() raises AssertionInsideSandboxError when the verifier's sandbox is active."""
    v = StrictVerifier()
    with v.sandbox():
        with pytest.raises(AssertionInsideSandboxError):
            v.verify_all()


# ---------------------------------------------------------------------------
# MissingAssertionFieldsError enforcement tests
# ---------------------------------------------------------------------------


def test_assert_interaction_raises_missing_fields_when_assertable_field_omitted() -> None:
    """assert_interaction() raises MissingAssertionFieldsError when a required field is absent."""
    from tripwire._errors import MissingAssertionFieldsError

    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    setattr(plugin, "assertable_fields", Mock(return_value=frozenset({"args", "kwargs"})))
    _inject_interaction(v, "mock:Svc.method", plugin=plugin)

    source = MagicMock()
    source.source_id = "mock:Svc.method"

    with pytest.raises(MissingAssertionFieldsError) as exc_info:
        v.assert_interaction(source)  # missing args and kwargs

    assert exc_info.value.missing_fields == frozenset({"args", "kwargs"})


def test_assert_interaction_passes_when_all_assertable_fields_present() -> None:
    """assert_interaction() succeeds when all required fields are present."""
    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    setattr(plugin, "assertable_fields", Mock(return_value=frozenset({"args"})))
    i = _inject_interaction(v, "mock:Svc.method", plugin=plugin)
    i.details["args"] = ()

    source = MagicMock()
    source.source_id = "mock:Svc.method"

    v.assert_interaction(source, args=())  # all required fields present — no exception


def test_assert_interaction_missing_fields_fires_after_source_id_match() -> None:
    """MissingAssertionFieldsError is raised only after source_id is matched, not before."""

    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    setattr(plugin, "assertable_fields", Mock(return_value=frozenset({"args"})))
    _inject_interaction(v, "mock:Svc.method", plugin=plugin)

    # Wrong source_id: should raise InteractionMismatchError, NOT MissingAssertionFieldsError
    source = MagicMock()
    source.source_id = "mock:Svc.OTHER"

    with pytest.raises(InteractionMismatchError):
        v.assert_interaction(source)  # source_id wrong, completeness check never fires


def test_assert_interaction_in_any_order_raises_missing_fields() -> None:
    """in_any_order path also enforces completeness before field-value matching."""
    from tripwire._errors import MissingAssertionFieldsError

    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    setattr(plugin, "assertable_fields", Mock(return_value=frozenset({"args", "kwargs"})))
    _inject_interaction(v, "mock:Svc.method", plugin=plugin)

    source = MagicMock()
    source.source_id = "mock:Svc.method"

    with pytest.raises(MissingAssertionFieldsError):
        with v.in_any_order():
            v.assert_interaction(source)  # missing args and kwargs


def test_assert_interaction_in_any_order_no_source_raises_mismatch_not_missing() -> None:
    """In in_any_order, if source_id not found, InteractionMismatchError fires before completeness."""
    from tripwire._errors import MissingAssertionFieldsError  # noqa: F401 — imported for clarity

    v = StrictVerifier()
    plugin = _make_mock_plugin(v)
    setattr(plugin, "assertable_fields", Mock(return_value=frozenset({"args"})))
    _inject_interaction(v, "mock:Svc.method", plugin=plugin)

    source = MagicMock()
    source.source_id = "mock:Svc.OTHER"  # no interaction with this source_id

    with pytest.raises(InteractionMismatchError):
        with v.in_any_order():
            v.assert_interaction(source)


# ---------------------------------------------------------------------------
# spy() tests
# ---------------------------------------------------------------------------


def test_verifier_spy_returns_import_site_mock_with_spy_flag() -> None:
    """verifier.spy() creates an ImportSiteMock with spy=True."""
    from tripwire._mock_plugin import ImportSiteMock

    v = StrictVerifier()
    spy = v.spy("os.path:sep")
    assert isinstance(spy, ImportSiteMock)
    assert spy._spy is True


def test_verifier_spy_delegates_to_real_function() -> None:
    """verifier.spy() delegates calls to the real function and records on timeline."""
    import sys
    import types

    v = StrictVerifier()
    mod = types.ModuleType("_test_verifier_spy")
    mod.add = lambda x, y: x + y  # type: ignore[attr-defined]
    sys.modules["_test_verifier_spy"] = mod
    try:
        spy = v.spy("_test_verifier_spy:add")

        with v.sandbox():
            result = mod.add(2, 3)

        assert result == 5
        unasserted = v._timeline.all_unasserted()
        assert len(unasserted) == 1
        assert "_test_verifier_spy:add" in unasserted[0].source_id

        # Assert the interaction so verify_all doesn't fail
        v.assert_interaction(
            spy.__getattr__("__call__"),
            args=(2, 3),
            kwargs={},
            returned=5,
        )
    finally:
        del sys.modules["_test_verifier_spy"]


# ---------------------------------------------------------------------------
# Entry point plugin discovery
# ---------------------------------------------------------------------------


class TestEntryPointPluginDiscovery:
    """_load_entrypoint_plugins discovers 3rd-party plugins via entry points."""

    def test_entrypoint_plugin_is_instantiated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A plugin registered via tripwire.plugins entry point is auto-instantiated."""
        from unittest.mock import MagicMock

        from tripwire._base_plugin import BasePlugin

        class FakeEntryPointPlugin(BasePlugin):
            activated = False

            def activate(self) -> None:
                FakeEntryPointPlugin.activated = True

            def deactivate(self) -> None:
                pass

            def matches(self, interaction, expected):
                return True

            def format_interaction(self, interaction):
                return ""

            def format_mock_hint(self, interaction):
                return ""

            def format_unmocked_hint(self, source_id, args, kwargs):
                return ""

            def format_assert_hint(self, interaction):
                return ""

            def assertable_fields(self, interaction):
                return frozenset()

            def get_unused_mocks(self):
                return []

            def format_unused_mock_hint(self, mock_config):
                return ""

        fake_ep = MagicMock()
        fake_ep.name = "fake_plugin"
        fake_ep.load.return_value = FakeEntryPointPlugin

        monkeypatch.setattr(
            "tripwire._verifier.entry_points",
            lambda group: [fake_ep] if group == "tripwire.plugins" else [],
        )

        v = StrictVerifier()
        plugin_types = [type(p).__name__ for p in v._plugins]
        assert "FakeEntryPointPlugin" in plugin_types

    def test_entrypoint_plugin_failure_is_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If a 3rd-party entry point plugin fails to load, it is silently skipped."""
        from unittest.mock import MagicMock

        fake_ep = MagicMock()
        fake_ep.name = "broken_plugin"
        fake_ep.load.side_effect = ImportError("broken")

        monkeypatch.setattr(
            "tripwire._verifier.entry_points",
            lambda group: [fake_ep] if group == "tripwire.plugins" else [],
        )

        # Should not raise
        v = StrictVerifier()
        assert v is not None

    def test_duplicate_entrypoint_plugin_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An entry point plugin of a type already registered is silently skipped."""
        from unittest.mock import MagicMock

        from tripwire.plugins.subprocess import SubprocessPlugin

        fake_ep = MagicMock()
        fake_ep.name = "subprocess_again"
        fake_ep.load.return_value = SubprocessPlugin

        monkeypatch.setattr(
            "tripwire._verifier.entry_points",
            lambda group: [fake_ep] if group == "tripwire.plugins" else [],
        )

        v = StrictVerifier()
        subprocess_count = sum(
            1 for p in v._plugins if type(p).__name__ == "SubprocessPlugin"
        )
        assert subprocess_count == 1
