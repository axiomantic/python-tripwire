"""Tests for Task 4: _timeline.py — Interaction dataclass and Timeline class."""

import threading
from unittest.mock import MagicMock

from tripwire._timeline import Interaction, Timeline


def _make_interaction(source_id: str = "mock:Svc.method", seq: int = 0) -> Interaction:
    plugin = MagicMock()
    return Interaction(source_id=source_id, sequence=seq, details={}, plugin=plugin)


def test_timeline_append_assigns_sequence() -> None:
    tl = Timeline()
    i1 = _make_interaction()
    i2 = _make_interaction()
    tl.append(i1)
    tl.append(i2)
    assert i1.sequence == 0
    assert i2.sequence == 1


def test_peek_next_unasserted_returns_first_unasserted() -> None:
    tl = Timeline()
    i1 = _make_interaction("src:a")
    i2 = _make_interaction("src:b")
    tl.append(i1)
    tl.append(i2)
    assert tl.peek_next_unasserted() is i1
    tl.mark_asserted(i1)
    assert tl.peek_next_unasserted() is i2
    tl.mark_asserted(i2)
    assert tl.peek_next_unasserted() is None


def test_find_any_unasserted_skips_asserted() -> None:
    tl = Timeline()
    i1 = _make_interaction("src:a")
    i2 = _make_interaction("src:b")
    tl.append(i1)
    tl.append(i2)
    tl.mark_asserted(i1)
    result = tl.find_any_unasserted(lambda i: i.source_id == "src:a")
    assert result is None
    result = tl.find_any_unasserted(lambda i: i.source_id == "src:b")
    assert result is i2


def test_all_unasserted_returns_only_unasserted() -> None:
    tl = Timeline()
    i1 = _make_interaction("src:a")
    i2 = _make_interaction("src:b")
    i3 = _make_interaction("src:c")
    tl.append(i1)
    tl.append(i2)
    tl.append(i3)
    tl.mark_asserted(i2)
    result = tl.all_unasserted()
    assert result == [i1, i3]


def test_timeline_thread_safe_append() -> None:
    """Multiple threads appending simultaneously should not corrupt sequence numbers."""
    tl = Timeline()
    errors: list[Exception] = []
    barrier = threading.Barrier(10)

    def append_many() -> None:
        try:
            barrier.wait()
            for _ in range(50):
                tl.append(_make_interaction())
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=append_many) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    interactions = tl.all_unasserted()
    assert len(interactions) == 500
    sequences = [i.sequence for i in interactions]
    assert sorted(sequences) == list(range(500))


def test_interaction_asserted_flag_defaults_false() -> None:
    i = _make_interaction()
    assert i._asserted is False


def test_mark_asserted_outside_record_succeeds() -> None:
    """mark_asserted() called after record() has returned succeeds normally."""
    from tripwire._timeline import Interaction, Timeline

    # We need a real plugin-like object that uses BasePlugin.record()
    # Use ConcretePlugin-style stub with a real Timeline
    timeline = Timeline()

    class _StubPlugin:
        def __init__(self) -> None:
            class _V:
                _timeline = timeline
                def _register_plugin(self, p: object) -> None:
                    pass
            self.verifier = _V()

        def record(self, interaction: Interaction) -> None:
            from tripwire._recording import _recording_in_progress
            token = _recording_in_progress.set(True)
            try:
                self.verifier._timeline.append(interaction)
            finally:
                _recording_in_progress.reset(token)

    plugin = _StubPlugin()
    interaction = Interaction(source_id="test:x", sequence=0, details={}, plugin=MagicMock())
    plugin.record(interaction)
    # Now outside record() — mark_asserted should succeed
    timeline.mark_asserted(interaction)
    assert interaction._asserted is True


def test_mark_asserted_inside_record_raises_auto_assert_error() -> None:
    """mark_asserted() called while _recording_in_progress is True raises AutoAssertError."""
    import pytest

    from tripwire._errors import AutoAssertError
    from tripwire._recording import _recording_in_progress
    from tripwire._timeline import Interaction, Timeline

    timeline = Timeline()
    interaction = Interaction(source_id="test:y", sequence=0, details={}, plugin=MagicMock())
    # Manually set the ContextVar to simulate record() being in progress
    token = _recording_in_progress.set(True)
    try:
        with pytest.raises(AutoAssertError):
            timeline.mark_asserted(interaction)
    finally:
        _recording_in_progress.reset(token)
