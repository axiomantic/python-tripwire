"""Tests for the enforce flag on Interaction."""

from bigfoot._base_plugin import BasePlugin
from bigfoot._timeline import Interaction, Timeline
from bigfoot._verifier import StrictVerifier


class _MinimalPlugin(BasePlugin):
    """Minimal plugin for enforce flag tests."""

    def matches(self, interaction: Interaction, expected: dict) -> bool:
        for key, val in expected.items():
            if interaction.details.get(key) != val:
                return False
        return True

    def format_interaction(self, interaction: Interaction) -> str:
        return f"[Test] {interaction.source_id}"

    def format_mock_hint(self, interaction: Interaction) -> str:
        return ""

    def format_unmocked_hint(self, source_id: str, args: tuple, kwargs: dict) -> str:
        return ""

    def format_assert_hint(self, interaction: Interaction) -> str:
        return ""

    def get_unused_mocks(self) -> list:
        return []

    def format_unused_mock_hint(self, mock_config: object) -> str:
        return ""

    def activate(self) -> None:
        pass

    def deactivate(self) -> None:
        pass


def test_interaction_enforce_defaults_to_true() -> None:
    """New interactions have enforce=True by default."""
    interaction = Interaction(
        source_id="test:op",
        sequence=0,
        details={"key": "value"},
        plugin=None,  # type: ignore[arg-type]
    )
    assert interaction.enforce is True


def test_interaction_enforce_can_be_set_to_false() -> None:
    """enforce can be set to False after creation."""
    interaction = Interaction(
        source_id="test:op",
        sequence=0,
        details={"key": "value"},
        plugin=None,  # type: ignore[arg-type]
    )
    interaction.enforce = False
    assert interaction.enforce is False


def test_verify_all_skips_non_enforced_interactions(bigfoot_verifier: StrictVerifier) -> None:
    """verify_all() does not raise for unasserted interactions with enforce=False."""
    plugin = _MinimalPlugin(bigfoot_verifier)
    interaction = Interaction(
        source_id="test:op",
        sequence=0,
        details={"key": "value"},
        plugin=plugin,
    )
    interaction.enforce = False
    bigfoot_verifier._timeline.append(interaction)

    # Should not raise -- the interaction is unasserted but enforce=False
    bigfoot_verifier.verify_all()


def test_verify_all_raises_for_enforced_unasserted(bigfoot_verifier: StrictVerifier) -> None:
    """verify_all() raises for unasserted interactions with enforce=True (default)."""
    import pytest
    from bigfoot._errors import UnassertedInteractionsError

    plugin = _MinimalPlugin(bigfoot_verifier)
    interaction = Interaction(
        source_id="test:op",
        sequence=0,
        details={"key": "value"},
        plugin=plugin,
    )
    # enforce defaults to True
    bigfoot_verifier._timeline.append(interaction)

    with pytest.raises(UnassertedInteractionsError):
        bigfoot_verifier.verify_all()

    # Mark asserted so the auto-teardown verify_all() does not re-raise
    bigfoot_verifier._timeline.mark_asserted(interaction)


def test_verify_all_mixed_enforce_flags(bigfoot_verifier: StrictVerifier) -> None:
    """verify_all() only reports enforced unasserted interactions."""
    import pytest
    from bigfoot._errors import UnassertedInteractionsError

    plugin = _MinimalPlugin(bigfoot_verifier)

    # Non-enforced interaction
    i1 = Interaction(source_id="test:setup", sequence=0, details={}, plugin=plugin)
    i1.enforce = False
    bigfoot_verifier._timeline.append(i1)

    # Enforced interaction
    i2 = Interaction(source_id="test:real", sequence=0, details={"x": 1}, plugin=plugin)
    bigfoot_verifier._timeline.append(i2)

    with pytest.raises(UnassertedInteractionsError) as exc_info:
        bigfoot_verifier.verify_all()

    # Only the enforced interaction should be in the error
    assert len(exc_info.value.interactions) == 1
    assert exc_info.value.interactions[0].source_id == "test:real"

    # Mark asserted so the auto-teardown verify_all() does not re-raise
    bigfoot_verifier._timeline.mark_asserted(i2)
