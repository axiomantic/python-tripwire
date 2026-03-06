"""Tests for Task 5: _base_plugin.py — BasePlugin ABC."""

from abc import ABC
from typing import Any
from unittest.mock import MagicMock

import pytest

from bigfoot._timeline import Interaction

# ---------------------------------------------------------------------------
# Stubs for StrictVerifier (not yet implemented; we only need duck-typed interface)
# ---------------------------------------------------------------------------


class _StubTimeline:
    """Minimal stub that records calls to append()."""

    def __init__(self) -> None:
        self.appended: list[Interaction] = []

    def append(self, interaction: Interaction) -> None:
        self.appended.append(interaction)


class _StubVerifier:
    """Minimal stub for StrictVerifier — only attributes that BasePlugin touches."""

    def __init__(self) -> None:
        self._timeline = _StubTimeline()
        self.registered_plugins: list[Any] = []

    def _register_plugin(self, plugin: Any) -> None:
        self.registered_plugins.append(plugin)


# ---------------------------------------------------------------------------
# Minimal concrete subclass implementing all 9 abstract methods
# ---------------------------------------------------------------------------


def _make_plugin(verifier: Any = None) -> "ConcretePlugin":
    if verifier is None:
        verifier = _StubVerifier()
    return ConcretePlugin(verifier)


def _make_interaction() -> Interaction:
    plugin = MagicMock()
    return Interaction(source_id="stub:Source.method", sequence=0, details={}, plugin=plugin)


class ConcretePlugin:
    """A concrete subclass of BasePlugin implementing all 9 abstract methods.

    Imported after BasePlugin is defined so tests exercise the real class.
    """

    # We delay import of BasePlugin inside the test class body to ensure the
    # import is exercised only after it exists.
    pass


# ---------------------------------------------------------------------------
# Import BasePlugin — must exist for tests to run
# ---------------------------------------------------------------------------


from bigfoot._base_plugin import BasePlugin  # noqa: E402


class ConcretePlugin(BasePlugin):  # type: ignore[no-redef]
    """Concrete implementation of all 10 abstract methods for testing."""

    def activate(self) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
        return True

    def format_interaction(self, interaction: Interaction) -> str:
        return "stub interaction"

    def format_mock_hint(self, interaction: Interaction) -> str:
        return "stub mock hint"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        return "stub unmocked hint"

    def format_assert_hint(self, interaction: Interaction) -> str:
        return "stub assert hint"

    def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
        return frozenset()

    def get_unused_mocks(self) -> list[Any]:
        return []

    def format_unused_mock_hint(self, mock_config: Any) -> str:
        return "stub unused mock hint"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_base_plugin_is_abstract() -> None:
    """BasePlugin cannot be instantiated directly — TypeError because of abstract methods."""
    # ESCAPE analysis:
    # CLAIM: BasePlugin is an ABC and cannot be directly instantiated.
    # PATH: ABC.__new__ checks for unimplemented abstract methods.
    # CHECK: TypeError is raised when attempting to instantiate BasePlugin.
    # MUTATION: Removing @abstractmethod from any method, or removing ABC base, would prevent this error.
    # ESCAPE: A broken implementation that is not truly abstract (inherits object, not ABC) would pass construction.
    # IMPACT: Plugin authors could accidentally instantiate BasePlugin and get attribute errors at runtime instead of clear TypeError.
    with pytest.raises(TypeError):
        BasePlugin(_StubVerifier())  # type: ignore[abstract]


def test_base_plugin_is_abc_subclass() -> None:
    """BasePlugin is a subclass of ABC."""
    # ESCAPE:
    # CLAIM: BasePlugin inherits from ABC.
    # PATH: issubclass() check against ABC.
    # CHECK: issubclass returns True.
    # MUTATION: Changing `class BasePlugin(ABC)` to `class BasePlugin` would fail this.
    # ESCAPE: Nothing reasonable passes issubclass(BasePlugin, ABC) without inheriting from ABC.
    # IMPACT: Abstract enforcement would be silently lost.
    assert issubclass(BasePlugin, ABC)


def test_concrete_subclass_is_instantiable() -> None:
    """A concrete subclass implementing all 10 abstract methods can be instantiated."""
    # ESCAPE:
    # CLAIM: ConcretePlugin with all 10 methods implemented can be created.
    # PATH: ConcretePlugin.__init__ -> BasePlugin.__init__.
    # CHECK: isinstance of BasePlugin confirms the chain.
    # MUTATION: Adding an 11th abstract method to BasePlugin without implementing it in ConcretePlugin would raise TypeError.
    # ESCAPE: Nothing reasonable — if it raises here, ABC enforcement works, but the test catches both directions.
    # IMPACT: Every plugin subclass would break.
    verifier = _StubVerifier()
    plugin = ConcretePlugin(verifier)
    assert isinstance(plugin, BasePlugin)


def test_constructor_stores_verifier() -> None:
    """BasePlugin.__init__ stores verifier as self.verifier."""
    # ESCAPE:
    # CLAIM: The verifier passed to __init__ is stored on the instance as `.verifier`.
    # PATH: BasePlugin.__init__ assigns self.verifier = verifier.
    # CHECK: Exact identity comparison (is) confirms same object, not a copy.
    # MUTATION: Assigning to a different attribute name (e.g., self._verifier) would fail `plugin.verifier is verifier`.
    # ESCAPE: A subclass that shadows `verifier` with None would pass the isinstance check but fail `is` check here.
    # IMPACT: Plugins would silently lose access to the verifier and fail at runtime with AttributeError.
    verifier = _StubVerifier()
    plugin = ConcretePlugin(verifier)
    assert plugin.verifier is verifier


def test_constructor_calls_register_plugin() -> None:
    """BasePlugin.__init__ calls verifier._register_plugin(self)."""
    # ESCAPE:
    # CLAIM: __init__ registers the plugin with the verifier.
    # PATH: BasePlugin.__init__ -> verifier._register_plugin(plugin_instance).
    # CHECK: verifier.registered_plugins contains exactly the plugin instance.
    # MUTATION: Removing the _register_plugin call in __init__ means registered_plugins stays empty.
    # ESCAPE: A broken impl that calls _register_plugin(None) would put None, not the plugin, in the list.
    # IMPACT: StrictVerifier would never know about the plugin; teardown checks would silently miss it.
    verifier = _StubVerifier()
    plugin = ConcretePlugin(verifier)
    assert verifier.registered_plugins == [plugin]


def test_record_calls_timeline_append() -> None:
    """record() is a concrete method that delegates to self.verifier._timeline.append(interaction)."""
    # ESCAPE:
    # CLAIM: Calling plugin.record(interaction) appends exactly that interaction to the timeline.
    # PATH: BasePlugin.record -> self.verifier._timeline.append(interaction).
    # CHECK: _StubTimeline.appended contains exactly the interaction passed to record().
    # MUTATION: Removing the append call in record() means appended stays empty.
    # ESCAPE: An impl that calls append(copy_of_interaction) instead of append(interaction) — caught by `is` identity.
    # IMPACT: Interactions would silently vanish from the timeline; assertions would never find them.
    verifier = _StubVerifier()
    plugin = ConcretePlugin(verifier)
    interaction = _make_interaction()

    plugin.record(interaction)

    assert verifier._timeline.appended == [interaction]
    assert verifier._timeline.appended[0] is interaction


def test_record_is_not_abstract() -> None:
    """record() is a concrete method — it can be called on a fully-implemented subclass without NotImplementedError."""
    # ESCAPE:
    # CLAIM: record() does not raise NotImplementedError (it is not abstract).
    # PATH: ConcretePlugin.record -> BasePlugin.record (inherited concrete method).
    # CHECK: No exception raised; timeline receives the interaction.
    # MUTATION: Making record() abstract would raise TypeError at ConcretePlugin instantiation (caught by separate test).
    # ESCAPE: An impl that defines record() as `raise NotImplementedError` would fail the append check.
    # IMPACT: Every plugin would need to re-implement the append logic, risking inconsistency.
    verifier = _StubVerifier()
    plugin = ConcretePlugin(verifier)
    interaction = _make_interaction()

    # Should not raise NotImplementedError
    plugin.record(interaction)
    assert verifier._timeline.appended == [interaction]


def test_record_not_in_abstract_methods() -> None:
    """record() does not appear in BasePlugin.__abstractmethods__."""
    # ESCAPE:
    # CLAIM: record is not listed as an abstract method.
    # PATH: Reads BasePlugin.__abstractmethods__ frozenset.
    # CHECK: Exact equality against known set of 9 method names.
    # MUTATION: Decorating record() with @abstractmethod adds it to __abstractmethods__.
    # ESCAPE: Nothing reasonable — frozenset equality is exact.
    # IMPACT: All plugins would be forced to re-implement record(), defeating its purpose.
    assert BasePlugin.__abstractmethods__ == frozenset(
        {
            "activate",
            "deactivate",
            "matches",
            "format_interaction",
            "format_mock_hint",
            "format_unmocked_hint",
            "format_assert_hint",
            "get_unused_mocks",
            "format_unused_mock_hint",
        }
    )


def test_missing_activate_prevents_instantiation() -> None:
    """A subclass missing activate() cannot be instantiated."""
    # ESCAPE:
    # CLAIM: Omitting any single abstract method prevents instantiation.
    # PATH: ABC enforcement via __abstractmethods__.
    # CHECK: TypeError raised when attempting to instantiate the incomplete subclass.
    # MUTATION: Removing @abstractmethod from activate() in BasePlugin would allow this class to instantiate.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Incomplete plugins would be created, failing silently later with AttributeError.

    class MissingActivate(BasePlugin):  # type: ignore[abstract]
        def deactivate(self) -> None:
            pass

        def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
            return True

        def format_interaction(self, interaction: Interaction) -> str:
            return ""

        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""

        def format_unmocked_hint(
            self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            return ""

        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""

        def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
            return frozenset()

        def get_unused_mocks(self) -> list[Any]:
            return []

        def format_unused_mock_hint(self, mock_config: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        MissingActivate(_StubVerifier())  # type: ignore[abstract]


def test_missing_deactivate_prevents_instantiation() -> None:
    """A subclass missing deactivate() cannot be instantiated."""
    # ESCAPE: Same rationale as test_missing_activate_prevents_instantiation.

    class MissingDeactivate(BasePlugin):  # type: ignore[abstract]
        def activate(self) -> None:
            pass

        def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
            return True

        def format_interaction(self, interaction: Interaction) -> str:
            return ""

        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""

        def format_unmocked_hint(
            self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            return ""

        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""

        def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
            return frozenset()

        def get_unused_mocks(self) -> list[Any]:
            return []

        def format_unused_mock_hint(self, mock_config: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        MissingDeactivate(_StubVerifier())  # type: ignore[abstract]


def test_missing_matches_prevents_instantiation() -> None:
    """A subclass missing matches() cannot be instantiated."""

    class MissingMatches(BasePlugin):  # type: ignore[abstract]
        def activate(self) -> None:
            pass

        def deactivate(self) -> None:
            pass

        def format_interaction(self, interaction: Interaction) -> str:
            return ""

        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""

        def format_unmocked_hint(
            self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            return ""

        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""

        def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
            return frozenset()

        def get_unused_mocks(self) -> list[Any]:
            return []

        def format_unused_mock_hint(self, mock_config: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        MissingMatches(_StubVerifier())  # type: ignore[abstract]


def test_missing_format_interaction_prevents_instantiation() -> None:
    """A subclass missing format_interaction() cannot be instantiated."""

    class MissingFormatInteraction(BasePlugin):  # type: ignore[abstract]
        def activate(self) -> None:
            pass

        def deactivate(self) -> None:
            pass

        def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
            return True

        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""

        def format_unmocked_hint(
            self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            return ""

        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""

        def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
            return frozenset()

        def get_unused_mocks(self) -> list[Any]:
            return []

        def format_unused_mock_hint(self, mock_config: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        MissingFormatInteraction(_StubVerifier())  # type: ignore[abstract]


def test_missing_format_mock_hint_prevents_instantiation() -> None:
    """A subclass missing format_mock_hint() cannot be instantiated."""

    class MissingFormatMockHint(BasePlugin):  # type: ignore[abstract]
        def activate(self) -> None:
            pass

        def deactivate(self) -> None:
            pass

        def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
            return True

        def format_interaction(self, interaction: Interaction) -> str:
            return ""

        def format_unmocked_hint(
            self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            return ""

        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""

        def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
            return frozenset()

        def get_unused_mocks(self) -> list[Any]:
            return []

        def format_unused_mock_hint(self, mock_config: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        MissingFormatMockHint(_StubVerifier())  # type: ignore[abstract]


def test_missing_format_unmocked_hint_prevents_instantiation() -> None:
    """A subclass missing format_unmocked_hint() cannot be instantiated."""

    class MissingFormatUnmockedHint(BasePlugin):  # type: ignore[abstract]
        def activate(self) -> None:
            pass

        def deactivate(self) -> None:
            pass

        def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
            return True

        def format_interaction(self, interaction: Interaction) -> str:
            return ""

        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""

        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""

        def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
            return frozenset()

        def get_unused_mocks(self) -> list[Any]:
            return []

        def format_unused_mock_hint(self, mock_config: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        MissingFormatUnmockedHint(_StubVerifier())  # type: ignore[abstract]


def test_missing_format_assert_hint_prevents_instantiation() -> None:
    """A subclass missing format_assert_hint() cannot be instantiated."""

    class MissingFormatAssertHint(BasePlugin):  # type: ignore[abstract]
        def activate(self) -> None:
            pass

        def deactivate(self) -> None:
            pass

        def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
            return True

        def format_interaction(self, interaction: Interaction) -> str:
            return ""

        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""

        def format_unmocked_hint(
            self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            return ""

        def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
            return frozenset()

        def get_unused_mocks(self) -> list[Any]:
            return []

        def format_unused_mock_hint(self, mock_config: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        MissingFormatAssertHint(_StubVerifier())  # type: ignore[abstract]


def test_missing_assertable_fields_uses_default() -> None:
    """A subclass missing assertable_fields() instantiates fine and uses the concrete default."""
    # After Task 3, assertable_fields() is no longer abstract.
    # A subclass that does not override it inherits the default.

    class MissingAssertableFields(BasePlugin):
        def activate(self) -> None:
            pass
        def deactivate(self) -> None:
            pass
        def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
            return True
        def format_interaction(self, interaction: Interaction) -> str:
            return ""
        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""
        def format_unmocked_hint(
            self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            return ""
        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""
        def get_unused_mocks(self) -> list[Any]:
            return []
        def format_unused_mock_hint(self, mock_config: Any) -> str:
            return ""

    verifier = _StubVerifier()
    p = MissingAssertableFields(verifier)
    i = Interaction(source_id="x", sequence=0, details={"k": "v"}, plugin=p)
    assert p.assertable_fields(i) == frozenset({"k"})


def test_missing_get_unused_mocks_prevents_instantiation() -> None:
    """A subclass missing get_unused_mocks() cannot be instantiated."""

    class MissingGetUnusedMocks(BasePlugin):  # type: ignore[abstract]
        def activate(self) -> None:
            pass

        def deactivate(self) -> None:
            pass

        def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
            return True

        def format_interaction(self, interaction: Interaction) -> str:
            return ""

        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""

        def format_unmocked_hint(
            self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            return ""

        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""

        def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
            return frozenset()

        def format_unused_mock_hint(self, mock_config: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        MissingGetUnusedMocks(_StubVerifier())  # type: ignore[abstract]


def test_missing_format_unused_mock_hint_prevents_instantiation() -> None:
    """A subclass missing format_unused_mock_hint() cannot be instantiated."""

    class MissingFormatUnusedMockHint(BasePlugin):  # type: ignore[abstract]
        def activate(self) -> None:
            pass

        def deactivate(self) -> None:
            pass

        def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
            return True

        def format_interaction(self, interaction: Interaction) -> str:
            return ""

        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""

        def format_unmocked_hint(
            self, source_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]
        ) -> str:
            return ""

        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""

        def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
            return frozenset()

        def get_unused_mocks(self) -> list[Any]:
            return []

    with pytest.raises(TypeError):
        MissingFormatUnusedMockHint(_StubVerifier())  # type: ignore[abstract]


def test_record_appends_multiple_interactions_in_order() -> None:
    """record() appends multiple interactions in call order to the timeline."""
    # ESCAPE:
    # CLAIM: Successive calls to record() append in order.
    # PATH: Each BasePlugin.record -> self.verifier._timeline.append(interaction).
    # CHECK: Exact list equality including order.
    # MUTATION: Prepending instead of appending would reverse the list.
    # ESCAPE: Nothing reasonable — list equality with ordering is exact.
    # IMPACT: Timeline ordering would be broken, making interaction assertions fail.
    verifier = _StubVerifier()
    plugin = ConcretePlugin(verifier)
    i1 = _make_interaction()
    i2 = _make_interaction()
    i3 = _make_interaction()

    plugin.record(i1)
    plugin.record(i2)
    plugin.record(i3)

    assert verifier._timeline.appended == [i1, i2, i3]


def test_assertable_fields_has_concrete_default() -> None:
    """A concrete subclass that omits assertable_fields() CAN be instantiated; it inherits the default."""
    from bigfoot._base_plugin import BasePlugin

    class _PluginWithoutAssertableFields(BasePlugin):
        def activate(self) -> None: ...
        def deactivate(self) -> None: ...
        def matches(self, interaction: Interaction, expected: dict) -> bool:
            return True  # type: ignore[override]
        def format_interaction(self, interaction: Interaction) -> str:
            return ""
        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""
        def format_unmocked_hint(self, source_id: str, args: tuple, kwargs: dict) -> str:
            return ""  # type: ignore[override]
        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""
        def get_unused_mocks(self) -> list:
            return []
        def format_unused_mock_hint(self, mock_config: object) -> str:
            return ""
        # assertable_fields deliberately omitted — should use default

    from bigfoot._verifier import StrictVerifier
    v = StrictVerifier()
    p = _PluginWithoutAssertableFields(v)
    # Default implementation returns frozenset of details keys
    interaction = Interaction(source_id="x", sequence=0, details={"a": 1, "b": 2}, plugin=p)
    result = p.assertable_fields(interaction)
    assert result == frozenset({"a", "b"})


def test_assertable_fields_contract_returns_frozenset() -> None:
    """A complete concrete plugin's assertable_fields() returns a frozenset."""
    from bigfoot._base_plugin import BasePlugin

    class _CompletePlugin(BasePlugin):
        def activate(self) -> None: ...
        def deactivate(self) -> None: ...
        def matches(self, interaction: Interaction, expected: dict) -> bool:
            return True  # type: ignore[override]

        def format_interaction(self, interaction: Interaction) -> str:
            return ""

        def format_mock_hint(self, interaction: Interaction) -> str:
            return ""

        def format_unmocked_hint(self, source_id: str, args: tuple, kwargs: dict) -> str:
            return ""  # type: ignore[override]

        def format_assert_hint(self, interaction: Interaction) -> str:
            return ""

        def get_unused_mocks(self) -> list:
            return []

        def format_unused_mock_hint(self, mock_config: object) -> str:
            return ""

        def assertable_fields(self, interaction: Interaction) -> frozenset:
            return frozenset()

    from bigfoot._verifier import StrictVerifier

    v = StrictVerifier()
    p = _CompletePlugin(v)
    result = p.assertable_fields(None)  # type: ignore[arg-type]
    assert isinstance(result, frozenset)
    assert result == frozenset()


def test_assertable_fields_default_returns_details_keys() -> None:
    """Default assertable_fields() returns frozenset of all keys in interaction.details."""
    from bigfoot._base_plugin import BasePlugin

    class _DefaultPlugin(BasePlugin):
        def activate(self) -> None: ...
        def deactivate(self) -> None: ...
        def matches(self, i: Interaction, e: dict) -> bool: return True  # type: ignore[override]
        def format_interaction(self, i: Interaction) -> str: return ""
        def format_mock_hint(self, i: Interaction) -> str: return ""
        def format_unmocked_hint(self, s: str, a: tuple, k: dict) -> str: return ""  # type: ignore[override]
        def format_assert_hint(self, i: Interaction) -> str: return ""
        def get_unused_mocks(self) -> list: return []
        def format_unused_mock_hint(self, m: object) -> str: return ""

    from bigfoot._verifier import StrictVerifier
    v = StrictVerifier()
    p = _DefaultPlugin(v)
    interaction2 = Interaction(source_id="x", sequence=0, details={"x": 1, "y": 2}, plugin=p)
    assert p.assertable_fields(interaction2) == frozenset({"x", "y"})

    # Empty details returns empty frozenset
    interaction3 = Interaction(source_id="x", sequence=0, details={}, plugin=p)
    assert p.assertable_fields(interaction3) == frozenset()
