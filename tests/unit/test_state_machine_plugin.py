"""Tests for Task 1.2: StateMachinePlugin base class."""

from __future__ import annotations

from collections import deque
from typing import Any

import pytest

from tripwire._errors import InvalidStateError, UnmockedInteractionError
from tripwire._state_machine_plugin import ScriptStep, SessionHandle, StateMachinePlugin
from tripwire._timeline import Interaction
from tripwire._verifier import StrictVerifier

# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
#
# States:  "a" --go--> "b"
#          "b" --reset--> "a"
# Methods: "go" (only valid from state "a")
#          "reset" (only valid from state "b")
# ---------------------------------------------------------------------------


class _TestPlugin(StateMachinePlugin):
    """Minimal two-state plugin: a --go--> b --reset--> a."""

    # Class-level reference counting required by BasePlugin pattern
    _install_count: int = 0

    def _initial_state(self) -> str:
        return "a"

    def _transitions(self) -> dict[str, dict[str, str]]:
        return {
            "go": {"a": "b"},
            "reset": {"b": "a"},
        }

    def _unmocked_source_id(self) -> str:
        return "test_plugin:connection"

    def activate(self) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def format_interaction(self, interaction: Interaction) -> str:
        return f"[_TestPlugin] {interaction.details.get('method', '?')}"

    def format_mock_hint(self, interaction: Interaction) -> str:
        return "plugin.new_session().expect(...)"

    def format_unmocked_hint(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        return f"Unexpected call to {source_id}"

    def format_assert_hint(self, interaction: Interaction) -> str:
        return "verifier.assert_interaction(...)"

    def format_unused_mock_hint(self, mock_config: object) -> str:
        return "Unused mock"

    def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier() -> StrictVerifier:
    return StrictVerifier()


def _make_plugin(verifier: StrictVerifier | None = None) -> _TestPlugin:
    if verifier is None:
        verifier = _make_verifier()
    return _TestPlugin(verifier)


def _make_connection_obj() -> object:
    """Return a unique object to serve as a connection identity."""
    return object()


# ---------------------------------------------------------------------------
# ScriptStep tests
# ---------------------------------------------------------------------------


def test_script_step_stores_method_and_returns() -> None:
    """ScriptStep stores method and returns exactly as given."""
    # ESCAPE analysis:
    # CLAIM: ScriptStep stores method and returns as provided.
    # PATH: dataclass field assignment.
    # CHECK: Exact equality on both fields.
    # MUTATION: Swapping field names (storing returns in method) would fail these checks.
    # ESCAPE: Nothing reasonable passes both assertions if fields are swapped.
    # IMPACT: Steps would be matched against wrong methods and return wrong values.
    step = ScriptStep(method="go", returns=42)
    assert step.method == "go"
    assert step.returns == 42


def test_script_step_defaults() -> None:
    """ScriptStep defaults: raises=None, required=True."""
    # ESCAPE:
    # CLAIM: Default values for raises and required are None and True respectively.
    # PATH: dataclass defaults.
    # CHECK: Exact equality.
    # MUTATION: Defaulting required=False or raises=SomeException would fail.
    # ESCAPE: Nothing reasonable without explicit change.
    # IMPACT: Steps would be optional by default (required=False) causing missed-mock checks to silently pass.
    step = ScriptStep(method="go", returns="x")
    assert step.raises is None
    assert step.required is True


def test_script_step_optional_required_false() -> None:
    """ScriptStep accepts required=False."""
    # ESCAPE:
    # CLAIM: ScriptStep.required can be set to False.
    # PATH: dataclass field.
    # CHECK: step.required == False.
    # MUTATION: Ignoring the required parameter would leave it True.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Optional steps would be incorrectly treated as required.
    step = ScriptStep(method="go", returns="x", required=False)
    assert step.required is False


def test_script_step_raises_field() -> None:
    """ScriptStep.raises stores the provided exception."""
    # ESCAPE:
    # CLAIM: raises field stores exactly the provided exception instance.
    # PATH: dataclass field assignment.
    # CHECK: Identity check (is) confirms same object.
    # MUTATION: Storing a copy of the exception would fail the `is` check.
    # ESCAPE: Nothing reasonable.
    # IMPACT: _execute_step would raise a copy instead of the registered exception.
    exc = ValueError("boom")
    step = ScriptStep(method="go", returns=None, raises=exc)
    assert step.raises is exc


def test_script_step_captures_registration_traceback() -> None:
    """ScriptStep.registration_traceback is auto-captured as a non-empty string."""
    # ESCAPE:
    # CLAIM: registration_traceback is captured at construction time and is non-empty.
    # PATH: default_factory calls traceback.format_stack().
    # CHECK: isinstance str and non-empty.
    # MUTATION: Setting default_factory=lambda: "" would fail the non-empty check.
    # ESCAPE: An empty-string factory would pass isinstance but fail the truthiness check.
    # IMPACT: Error messages would have empty tracebacks, making debugging impossible.
    step = ScriptStep(method="go", returns=None)
    assert isinstance(step.registration_traceback, str)
    assert step.registration_traceback != ""


# ---------------------------------------------------------------------------
# SessionHandle tests
# ---------------------------------------------------------------------------


def test_session_handle_initial_state() -> None:
    """SessionHandle stores initial_state correctly."""
    # ESCAPE:
    # CLAIM: SessionHandle._state == initial_state at construction.
    # PATH: SessionHandle.__init__ assigns self._state = initial_state.
    # CHECK: Exact equality.
    # MUTATION: Storing initial_state in wrong attribute or hardcoding "a" would still pass
    #           this test but fail tests with other initial states.
    # ESCAPE: Hardcoding "_state = 'a'" would fail a test with initial_state='z'.
    # IMPACT: All state machine logic would start from wrong state.
    handle = SessionHandle("idle")
    assert handle._state == "idle"


def test_session_handle_empty_script_at_start() -> None:
    """SessionHandle._script starts empty."""
    # ESCAPE:
    # CLAIM: _script is an empty list at construction.
    # PATH: SessionHandle.__init__ assigns self._script = [].
    # CHECK: Exact equality to empty list.
    # MUTATION: Pre-populating _script with a dummy step would fail equality.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Sessions would have phantom steps, causing unexpected returns or raises.
    handle = SessionHandle("a")
    assert handle._script == []


def test_session_handle_expect_appends_step() -> None:
    """expect() appends a ScriptStep to _script."""
    # ESCAPE:
    # CLAIM: expect() appends exactly one ScriptStep to _script.
    # PATH: SessionHandle.expect -> appends ScriptStep.
    # CHECK: len == 1 AND exact field values on the step.
    # MUTATION: Prepending or clearing would fail length/content checks.
    # ESCAPE: A step with wrong method or returns would fail the content checks.
    # IMPACT: Steps would not execute in registration order.
    handle = SessionHandle("a")
    handle.expect("go", returns=99)
    assert len(handle._script) == 1
    assert handle._script[0].method == "go"
    assert handle._script[0].returns == 99
    assert handle._script[0].raises is None
    assert handle._script[0].required is True


def test_session_handle_expect_returns_self_for_chaining() -> None:
    """expect() returns self for method chaining."""
    # ESCAPE:
    # CLAIM: expect() returns the SessionHandle instance itself.
    # PATH: return self at end of expect().
    # CHECK: `is` identity check.
    # MUTATION: Returning None or a new SessionHandle would fail `is`.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Chaining like .expect(...).expect(...) would raise AttributeError on None.
    handle = SessionHandle("a")
    result = handle.expect("go", returns=42)
    assert result is handle


def test_session_handle_expect_chaining_multiple() -> None:
    """Multiple chained expect() calls accumulate steps in order."""
    # ESCAPE:
    # CLAIM: Three chained expect() calls produce three ScriptSteps in order.
    # PATH: Each expect() appends one step; chaining is enabled by return self.
    # CHECK: Exact list contents and order.
    # MUTATION: Reversing append order would flip the list.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Steps would execute in wrong order, returning wrong values.
    handle = SessionHandle("a")
    handle.expect("go", returns=1).expect("go", returns=2).expect("reset", returns=3)
    assert len(handle._script) == 3
    assert handle._script[0].method == "go"
    assert handle._script[0].returns == 1
    assert handle._script[1].method == "go"
    assert handle._script[1].returns == 2
    assert handle._script[2].method == "reset"
    assert handle._script[2].returns == 3


def test_session_handle_expect_captures_traceback() -> None:
    """expect() captures a registration traceback on the ScriptStep."""
    # ESCAPE:
    # CLAIM: The ScriptStep created by expect() has a non-empty string registration_traceback.
    # PATH: expect() creates ScriptStep, which auto-captures traceback via default_factory.
    # CHECK: isinstance str AND non-empty.
    # MUTATION: Clearing the traceback after creation would fail non-empty check.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Unused mock error messages would have empty tracebacks.
    handle = SessionHandle("a")
    handle.expect("go", returns=0)
    assert isinstance(handle._script[0].registration_traceback, str)
    assert handle._script[0].registration_traceback != ""


def test_session_handle_expect_raises_stored() -> None:
    """expect() with raises= stores the exception on ScriptStep."""
    # ESCAPE:
    # CLAIM: raises= kwarg is stored in the ScriptStep.
    # PATH: expect() passes raises to ScriptStep constructor.
    # CHECK: Identity (`is`) confirms same exception object.
    # MUTATION: Dropping the raises kwarg would store None.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Steps configured to raise would silently return instead.
    exc = RuntimeError("fail")
    handle = SessionHandle("a")
    handle.expect("go", returns=None, raises=exc)
    assert handle._script[0].raises is exc


def test_session_handle_expect_required_false() -> None:
    """expect() with required=False stores required=False on ScriptStep."""
    # ESCAPE:
    # CLAIM: required=False is passed through to ScriptStep.
    # PATH: expect() passes required to ScriptStep constructor.
    # CHECK: Exact equality False.
    # MUTATION: Ignoring the required kwarg and always setting required=True would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Optional steps would be treated as required, failing verify_all().
    handle = SessionHandle("a")
    handle.expect("go", returns=0, required=False)
    assert handle._script[0].required is False


# ---------------------------------------------------------------------------
# StateMachinePlugin structural tests
# ---------------------------------------------------------------------------


def test_state_machine_plugin_registers_on_verifier() -> None:
    """StateMachinePlugin registers itself on the verifier at construction."""
    # ESCAPE:
    # CLAIM: Plugin registers via BasePlugin.__init__ -> verifier._register_plugin(self).
    # PATH: StateMachinePlugin.__init__ -> super().__init__ -> verifier._register_plugin.
    # CHECK: plugin in verifier._plugins.
    # MUTATION: Not calling super().__init__ would leave _plugins empty.
    # ESCAPE: Nothing reasonable.
    # IMPACT: teardown verify_all() would skip this plugin's unused mock checks.
    v = _make_verifier()
    plugin = _TestPlugin(v)
    assert plugin in v._plugins


def test_state_machine_plugin_initializes_empty_session_queue() -> None:
    """StateMachinePlugin._session_queue starts empty."""
    # ESCAPE:
    # CLAIM: _session_queue is a deque and starts empty.
    # PATH: __init__ assigns self._session_queue = deque().
    # CHECK: isinstance deque AND len == 0.
    # MUTATION: Pre-populating queue would fail the len check.
    # ESCAPE: Using a list instead of deque would fail isinstance.
    # IMPACT: _bind_connection would incorrectly believe sessions are available.
    plugin = _make_plugin()
    assert isinstance(plugin._session_queue, deque)
    assert len(plugin._session_queue) == 0


def test_state_machine_plugin_initializes_empty_active_sessions() -> None:
    """StateMachinePlugin._active_sessions starts empty."""
    # ESCAPE:
    # CLAIM: _active_sessions is a dict and starts empty.
    # PATH: __init__ assigns self._active_sessions = {}.
    # CHECK: isinstance dict AND == {}.
    # MUTATION: Pre-populating would fail equality check.
    # ESCAPE: Nothing reasonable.
    # IMPACT: _lookup_session would find stale sessions from previous test.
    plugin = _make_plugin()
    assert isinstance(plugin._active_sessions, dict)
    assert plugin._active_sessions == {}


def test_state_machine_plugin_initializes_empty_connection_refs() -> None:
    """StateMachinePlugin._connection_refs starts empty."""
    # ESCAPE:
    # CLAIM: _connection_refs is a dict and starts empty.
    # PATH: __init__ assigns self._connection_refs = {}.
    # CHECK: isinstance dict AND == {}.
    # MUTATION: Pre-populating would fail equality check.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Objects would not be held alive; GC could recycle IDs, causing ghost lookups.
    plugin = _make_plugin()
    assert isinstance(plugin._connection_refs, dict)
    assert plugin._connection_refs == {}


def test_state_machine_plugin_matches_always_returns_true() -> None:
    """matches() always returns True regardless of interaction or expected."""
    # ESCAPE:
    # CLAIM: matches() returns True for any input.
    # PATH: matches() implementation returns True unconditionally.
    # CHECK: Exact True for two different argument sets.
    # MUTATION: Returning False unconditionally would fail.
    # ESCAPE: A conditional that returns True for these specific inputs but False for others
    #         would escape, but the test uses two different inputs to make that harder.
    # IMPACT: State machine interactions would never match in assert_interaction().
    plugin = _make_plugin()
    interaction = Interaction(source_id="test", sequence=0, details={}, plugin=plugin)
    assert plugin.matches(interaction, {}) is True
    assert plugin.matches(interaction, {"method": "go", "state": "b"}) is True


def test_state_machine_plugin_assertable_fields_returns_empty_frozenset() -> None:
    """assertable_fields() always returns frozenset()."""
    # ESCAPE:
    # CLAIM: assertable_fields() returns an empty frozenset.
    # PATH: assertable_fields() implementation returns frozenset().
    # CHECK: Exact equality to frozenset().
    # MUTATION: Returning frozenset({"method"}) would cause MissingAssertionFieldsError in verifier.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Every assert_interaction() call on a state machine interaction would require "method"
    #         field, breaking the API.
    plugin = _make_plugin()
    interaction = Interaction(source_id="test", sequence=0, details={}, plugin=plugin)
    assert plugin.assertable_fields(interaction) == frozenset()


# ---------------------------------------------------------------------------
# new_session() tests
# ---------------------------------------------------------------------------


def test_new_session_returns_session_handle() -> None:
    """new_session() returns a SessionHandle instance."""
    # ESCAPE:
    # CLAIM: new_session() returns a SessionHandle.
    # PATH: new_session() creates SessionHandle and returns it.
    # CHECK: isinstance(result, SessionHandle).
    # MUTATION: Returning a raw dict would fail isinstance.
    # ESCAPE: A subclass of SessionHandle would pass; that's fine.
    # IMPACT: Callers would get AttributeError when calling .expect() on non-SessionHandle.
    plugin = _make_plugin()
    handle = plugin.new_session()
    assert isinstance(handle, SessionHandle)


def test_new_session_uses_initial_state() -> None:
    """new_session() creates a SessionHandle with the plugin's initial state."""
    # ESCAPE:
    # CLAIM: The returned handle starts in the state from _initial_state().
    # PATH: new_session() -> SessionHandle(self._initial_state()) -> handle._state == "a".
    # CHECK: Exact equality.
    # MUTATION: Hardcoding "b" as initial state would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: All state machine calls would start from wrong state.
    plugin = _make_plugin()
    handle = plugin.new_session()
    assert handle._state == "a"


def test_new_session_enqueues_in_session_queue() -> None:
    """new_session() appends the handle to _session_queue."""
    # ESCAPE:
    # CLAIM: _session_queue contains the returned handle after new_session().
    # PATH: new_session() appends to self._session_queue.
    # CHECK: len == 1 AND identity check on the element.
    # MUTATION: Not appending to the queue would leave it empty.
    # ESCAPE: Appending a copy (not the returned handle) would fail the `is` identity check.
    # IMPACT: _bind_connection would not find the session and raise UnmockedInteractionError.
    plugin = _make_plugin()
    handle = plugin.new_session()
    assert len(plugin._session_queue) == 1
    assert plugin._session_queue[0] is handle


def test_new_session_multiple_sessions_queued_in_order() -> None:
    """Multiple new_session() calls enqueue in FIFO order."""
    # ESCAPE:
    # CLAIM: Three calls to new_session() produce three handles queued in creation order.
    # PATH: Each new_session() appends to the deque.
    # CHECK: Exact list of all three handles in order via list(queue).
    # MUTATION: Using appendleft instead of append would reverse order.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Sessions would be assigned to connections in wrong order.
    plugin = _make_plugin()
    h1 = plugin.new_session()
    h2 = plugin.new_session()
    h3 = plugin.new_session()
    assert list(plugin._session_queue) == [h1, h2, h3]


# ---------------------------------------------------------------------------
# _bind_connection() tests
# ---------------------------------------------------------------------------


def test_bind_connection_returns_session_handle() -> None:
    """_bind_connection() returns the SessionHandle from the queue."""
    # ESCAPE:
    # CLAIM: _bind_connection() returns the handle that was in the queue.
    # PATH: _bind_connection() pops from queue and returns the handle.
    # CHECK: returned handle `is` the one created by new_session().
    # MUTATION: Returning a new SessionHandle instead of the queued one would fail `is`.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Connection would be bound to wrong session; steps would not match.
    plugin = _make_plugin()
    handle = plugin.new_session()
    conn = _make_connection_obj()
    result = plugin._bind_connection(conn)
    assert result is handle


def test_bind_connection_removes_from_queue() -> None:
    """_bind_connection() pops the first session from _session_queue."""
    # ESCAPE:
    # CLAIM: After _bind_connection(), the session_queue is empty (one session was queued).
    # PATH: _bind_connection() pops from deque front.
    # CHECK: len == 0 after bind.
    # MUTATION: Not popping from queue would leave it with length 1.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Re-binding would hand out the same session to multiple connections.
    plugin = _make_plugin()
    plugin.new_session()
    conn = _make_connection_obj()
    plugin._bind_connection(conn)
    assert len(plugin._session_queue) == 0


def test_bind_connection_stores_in_active_sessions() -> None:
    """_bind_connection() stores handle in _active_sessions keyed by id(conn)."""
    # ESCAPE:
    # CLAIM: _active_sessions[id(conn)] is the bound handle.
    # PATH: _bind_connection() sets _active_sessions[id(connection_obj)] = handle.
    # CHECK: Key exists and identity check on value.
    # MUTATION: Using conn itself as key instead of id(conn) would fail the int key lookup.
    # ESCAPE: Storing handle under wrong id would fail identity check.
    # IMPACT: _lookup_session would never find the handle.
    plugin = _make_plugin()
    handle = plugin.new_session()
    conn = _make_connection_obj()
    plugin._bind_connection(conn)
    assert id(conn) in plugin._active_sessions
    assert plugin._active_sessions[id(conn)] is handle


def test_bind_connection_stores_strong_ref() -> None:
    """_bind_connection() stores a strong reference to conn in _connection_refs."""
    # ESCAPE:
    # CLAIM: _connection_refs[id(conn)] is the conn object itself.
    # PATH: _bind_connection() sets _connection_refs[id(conn)] = conn.
    # CHECK: Identity check.
    # MUTATION: Not storing in _connection_refs would allow GC to collect conn,
    #           invalidating the id-based lookup.
    # ESCAPE: Nothing reasonable passes `is conn` without storing conn.
    # IMPACT: GC could reuse the memory address, causing mismatched lookups.
    plugin = _make_plugin()
    plugin.new_session()
    conn = _make_connection_obj()
    plugin._bind_connection(conn)
    assert id(conn) in plugin._connection_refs
    assert plugin._connection_refs[id(conn)] is conn


def test_bind_connection_sets_connection_obj_on_handle() -> None:
    """_bind_connection() sets handle._connection_obj = conn."""
    # ESCAPE:
    # CLAIM: The bound handle has _connection_obj pointing to the connection.
    # PATH: _bind_connection() sets handle._connection_obj = connection_obj.
    # CHECK: Identity check.
    # MUTATION: Not setting _connection_obj would leave it None.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Code that reads handle._connection_obj to identify the connection would fail.
    plugin = _make_plugin()
    handle = plugin.new_session()
    conn = _make_connection_obj()
    plugin._bind_connection(conn)
    assert handle._connection_obj is conn


def test_bind_connection_raises_when_queue_empty() -> None:
    """_bind_connection() raises UnmockedInteractionError when session queue is empty."""
    # ESCAPE:
    # CLAIM: Calling _bind_connection() with no queued sessions raises UnmockedInteractionError.
    # PATH: _bind_connection() checks empty queue -> raises UnmockedInteractionError.
    # CHECK: pytest.raises verifies the exception type exactly.
    # MUTATION: Raising a different exception type would fail pytest.raises.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Users would get an obscure error instead of a helpful tripwire message.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    with pytest.raises(UnmockedInteractionError):
        plugin._bind_connection(conn)


def test_bind_connection_fifo_order() -> None:
    """_bind_connection() binds sessions in FIFO order."""
    # ESCAPE:
    # CLAIM: The first session queued is the first one returned by _bind_connection().
    # PATH: deque.popleft() removes from the front.
    # CHECK: First bind returns h1, second returns h2.
    # MUTATION: Using pop() instead of popleft() would reverse FIFO order.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Connections would receive sessions out of test-specified order.
    plugin = _make_plugin()
    h1 = plugin.new_session()
    h2 = plugin.new_session()
    conn1 = _make_connection_obj()
    conn2 = _make_connection_obj()
    result1 = plugin._bind_connection(conn1)
    result2 = plugin._bind_connection(conn2)
    assert result1 is h1
    assert result2 is h2


# ---------------------------------------------------------------------------
# _lookup_session() tests
# ---------------------------------------------------------------------------


def test_lookup_session_returns_bound_handle() -> None:
    """_lookup_session() returns the handle previously bound to that connection."""
    # ESCAPE:
    # CLAIM: _lookup_session(conn) returns the handle bound to conn.
    # PATH: _lookup_session() -> _active_sessions.get(id(conn)).
    # CHECK: Identity check.
    # MUTATION: Returning the wrong handle would fail `is`.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Method calls would execute against wrong session.
    plugin = _make_plugin()
    handle = plugin.new_session()
    conn = _make_connection_obj()
    plugin._bind_connection(conn)
    result = plugin._lookup_session(conn)
    assert result is handle


def test_lookup_session_raises_when_not_bound() -> None:
    """_lookup_session() raises UnmockedInteractionError for unknown connection."""
    # ESCAPE:
    # CLAIM: _lookup_session() raises UnmockedInteractionError when conn is not in _active_sessions.
    # PATH: _active_sessions.get(id(conn)) returns None -> raise UnmockedInteractionError.
    # CHECK: pytest.raises verifies exception type.
    # MUTATION: Returning None instead of raising would fail pytest.raises.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Methods on unregistered connections would silently return None.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    with pytest.raises(UnmockedInteractionError):
        plugin._lookup_session(conn)


# ---------------------------------------------------------------------------
# _release_session() tests
# ---------------------------------------------------------------------------


def test_release_session_removes_from_active_sessions() -> None:
    """_release_session() removes the entry from _active_sessions."""
    # ESCAPE:
    # CLAIM: After _release_session(), id(conn) is not in _active_sessions.
    # PATH: _release_session() deletes from _active_sessions.
    # CHECK: `id(conn) not in plugin._active_sessions`.
    # MUTATION: Not deleting from _active_sessions would leave the entry.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Released sessions would remain findable, causing ghost lookups.
    plugin = _make_plugin()
    plugin.new_session()
    conn = _make_connection_obj()
    plugin._bind_connection(conn)
    plugin._release_session(conn)
    assert id(conn) not in plugin._active_sessions


def test_release_session_removes_from_connection_refs() -> None:
    """_release_session() removes the entry from _connection_refs."""
    # ESCAPE:
    # CLAIM: After _release_session(), id(conn) is not in _connection_refs.
    # PATH: _release_session() deletes from _connection_refs.
    # CHECK: `id(conn) not in plugin._connection_refs`.
    # MUTATION: Not deleting from _connection_refs would keep a strong reference alive.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Connections would never be garbage collected, causing memory leaks.
    plugin = _make_plugin()
    plugin.new_session()
    conn = _make_connection_obj()
    plugin._bind_connection(conn)
    plugin._release_session(conn)
    assert id(conn) not in plugin._connection_refs


# ---------------------------------------------------------------------------
# _execute_step() tests
# ---------------------------------------------------------------------------


def test_execute_step_happy_path_returns_value() -> None:
    """_execute_step() returns step.returns for a valid method in the correct state."""
    # ESCAPE:
    # CLAIM: _execute_step() returns the value from the script step.
    # PATH: _execute_step() -> pops step -> returns step.returns.
    # CHECK: Exact equality on return value.
    # MUTATION: Returning step.raises or None would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: All state machine calls would return wrong values.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=99)
    plugin._bind_connection(conn)

    result = plugin._execute_step(handle, "go", (), {}, "test:source")
    assert result == 99


def test_execute_step_advances_state() -> None:
    """_execute_step() advances _state according to _transitions()."""
    # ESCAPE:
    # CLAIM: After executing "go" from state "a", handle._state == "b".
    # PATH: _execute_step() -> _transitions()["go"]["a"] == "b" -> handle._state = "b".
    # CHECK: Exact equality.
    # MUTATION: Not updating _state would leave it as "a".
    # ESCAPE: Nothing reasonable.
    # IMPACT: Subsequent calls would use wrong from-state, raising InvalidStateError.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=None)
    plugin._bind_connection(conn)

    plugin._execute_step(handle, "go", (), {}, "test:source")
    assert handle._state == "b"


def test_execute_step_pops_step_from_script() -> None:
    """_execute_step() removes the executed step from handle._script (FIFO)."""
    # ESCAPE:
    # CLAIM: After execute_step, the step is removed from _script.
    # PATH: _execute_step() pops from front of handle._script.
    # CHECK: len == 0 after executing the only step.
    # MUTATION: Not popping would leave the step in place for re-execution.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Every call would re-execute the first step instead of advancing.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=None)
    plugin._bind_connection(conn)

    plugin._execute_step(handle, "go", (), {}, "test:source")
    assert len(handle._script) == 0


def test_execute_step_records_interaction_on_timeline() -> None:
    """_execute_step() records an Interaction on the verifier timeline."""
    # ESCAPE:
    # CLAIM: One interaction is appended to the verifier's timeline after execute_step.
    # PATH: _execute_step() creates Interaction and calls self.record(interaction)
    #       -> self.verifier._timeline.append(interaction).
    # CHECK: len(timeline) == 1; source_id matches.
    # MUTATION: Not calling record() would leave timeline empty.
    # ESCAPE: Calling record() with wrong source_id would pass length check but fail source check.
    # IMPACT: Interactions would never be assertable.
    v = _make_verifier()
    plugin = _TestPlugin(v)
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=None)
    plugin._bind_connection(conn)

    plugin._execute_step(handle, "go", (), {}, "test:source")

    all_interactions = v._timeline._interactions
    assert len(all_interactions) == 1
    assert all_interactions[0].source_id == "test:source"


def test_execute_step_does_not_auto_mark_interaction_asserted() -> None:
    """_execute_step() does NOT auto-mark the recorded interaction as asserted.

    Test authors must call assert_interaction() explicitly. Auto-assert is prohibited.
    """
    # ESCAPE:
    # CLAIM: The interaction recorded by _execute_step() has _asserted=False.
    # PATH: _execute_step() calls self.record() but NOT mark_asserted().
    # CHECK: interaction._asserted is False.
    # MUTATION: Adding a mark_asserted() call would set _asserted=True, defeating tripwire's
    #           certainty guarantee.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Test authors could no longer trust that unasserted interactions cause test failures.
    v = _make_verifier()
    plugin = _TestPlugin(v)
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=None)
    plugin._bind_connection(conn)

    plugin._execute_step(handle, "go", (), {}, "test:source")

    interaction = v._timeline._interactions[0]
    assert interaction._asserted is False


def test_execute_step_raises_configured_exception() -> None:
    """_execute_step() raises step.raises when step.raises is not None."""
    # ESCAPE:
    # CLAIM: When step.raises is set, _execute_step() raises that exception.
    # PATH: _execute_step() checks step.raises is not None -> raise step.raises.
    # CHECK: pytest.raises catches the exact exception instance type.
    # MUTATION: Not checking step.raises would return step.returns silently.
    # ESCAPE: Raising a different exception type would fail pytest.raises(ValueError).
    # IMPACT: Error steps would silently succeed instead of propagating the configured error.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=None, raises=ValueError("oops"))
    plugin._bind_connection(conn)

    with pytest.raises(ValueError, match="oops"):
        plugin._execute_step(handle, "go", (), {}, "test:source")


def test_execute_step_raises_invalid_state_error_on_wrong_state() -> None:
    """_execute_step() raises InvalidStateError when called from an invalid state."""
    # ESCAPE:
    # CLAIM: Calling "reset" from state "a" (not valid per transitions) raises InvalidStateError.
    # PATH: _execute_step() checks _transitions()["reset"] -> "b" not in {from-states} -> raise.
    # CHECK: pytest.raises(InvalidStateError) verifies type.
    # MUTATION: Not checking state validity would execute the step from wrong state.
    # ESCAPE: Raising a different error type would fail pytest.raises.
    # IMPACT: State machine invariants would be violated silently.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    # "reset" is only valid from state "b", but we start in "a"
    handle.expect("reset", returns=None)
    plugin._bind_connection(conn)

    with pytest.raises(InvalidStateError) as exc_info:
        plugin._execute_step(handle, "reset", (), {}, "test:source")

    err = exc_info.value
    assert err.source_id == "test:source"
    assert err.method == "reset"
    assert err.current_state == "a"
    assert err.valid_states == frozenset({"b"})


def test_execute_step_raises_invalid_state_error_for_unknown_method() -> None:
    """_execute_step() raises InvalidStateError for a method not in _transitions()."""
    # ESCAPE:
    # CLAIM: A method not in _transitions() raises InvalidStateError.
    # PATH: _execute_step() checks method in _transitions() -> not found -> raises.
    # CHECK: pytest.raises(InvalidStateError).
    # MUTATION: Silently ignoring unknown methods would not raise.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Typos in method names would silently do nothing.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    plugin._bind_connection(conn)

    with pytest.raises(InvalidStateError):
        plugin._execute_step(handle, "unknown_method", (), {}, "test:source")


def test_execute_step_raises_unmocked_when_script_empty() -> None:
    """_execute_step() raises UnmockedInteractionError when handle._script is empty."""
    # ESCAPE:
    # CLAIM: Calling _execute_step() with no queued steps raises UnmockedInteractionError.
    # PATH: _execute_step() checks len(handle._script) == 0 -> raises.
    # CHECK: pytest.raises(UnmockedInteractionError).
    # MUTATION: Raising a different error or returning None would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Users would get confusing errors instead of "you forgot to mock this step".
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    # no expect() calls — script is empty
    plugin._bind_connection(conn)

    with pytest.raises(UnmockedInteractionError):
        plugin._execute_step(handle, "go", (), {}, "test:source")


def test_execute_step_unasserted_interaction_raises_at_teardown() -> None:
    """verify_all() raises UnassertedInteractionsError when _execute_step() interactions are not asserted.

    Auto-assert is prohibited. Test authors must call assert_interaction() explicitly.
    Without explicit assertions, verify_all() correctly detects unasserted interactions.
    """
    # ESCAPE:
    # CLAIM: verify_all() raises UnassertedInteractionsError after _execute_step() when
    #        no assert_interaction() call is made.
    # PATH: _execute_step() records but does NOT mark asserted; verify_all() finds
    #       unasserted interactions and raises.
    # CHECK: pytest.raises(UnassertedInteractionsError) confirms the error fires.
    # MUTATION: Adding mark_asserted() back to _execute_step() would suppress the error.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Tests that forgot assert_interaction() would silently pass instead of failing.
    from tripwire._errors import UnassertedInteractionsError

    v = _make_verifier()
    plugin = _TestPlugin(v)
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=None)
    plugin._bind_connection(conn)

    plugin._execute_step(handle, "go", (), {}, "test:source")

    # Must raise — the interaction was recorded but NOT asserted.
    with pytest.raises(UnassertedInteractionsError):
        v.verify_all()


def test_execute_step_sequential_fifo_order() -> None:
    """_execute_step() executes steps in FIFO registration order."""
    # ESCAPE:
    # CLAIM: Two steps are executed in the order they were registered.
    # PATH: _execute_step() pops from front of _script (list[0]).
    # CHECK: First execution returns 10, second returns 20.
    # MUTATION: Using pop() (LIFO) instead of pop(0) (FIFO) would swap returns.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Steps would execute in reverse order.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=10)
    # After "go", state is "b"; "reset" takes us back to "a"
    handle.expect("reset", returns=20)
    plugin._bind_connection(conn)

    r1 = plugin._execute_step(handle, "go", (), {}, "src")
    r2 = plugin._execute_step(handle, "reset", (), {}, "src")
    assert r1 == 10
    assert r2 == 20


# ---------------------------------------------------------------------------
# get_unused_mocks() tests
# ---------------------------------------------------------------------------


def test_get_unused_mocks_empty_when_no_sessions() -> None:
    """get_unused_mocks() returns empty list when no sessions exist."""
    # ESCAPE:
    # CLAIM: No sessions means no unused mocks.
    # PATH: get_unused_mocks() iterates queued and active sessions, finds none.
    # CHECK: == [] exact equality.
    # MUTATION: Returning a non-empty sentinel list would fail.
    # ESCAPE: Nothing reasonable.
    # IMPACT: verify_all() would spuriously raise UnusedMocksError.
    plugin = _make_plugin()
    assert plugin.get_unused_mocks() == []


def test_get_unused_mocks_returns_required_steps_from_unbound_session() -> None:
    """get_unused_mocks() includes required steps from sessions still in the queue."""
    # ESCAPE:
    # CLAIM: A session in _session_queue with required steps appears in get_unused_mocks().
    # PATH: get_unused_mocks() iterates _session_queue, collects required ScriptSteps.
    # CHECK: Exact list with the one step.
    # MUTATION: Only checking _active_sessions would miss queued sessions.
    # ESCAPE: Returning the full list (required + optional) would include the optional step.
    # IMPACT: verify_all() would miss unused mocks from unbound sessions.
    plugin = _make_plugin()
    handle = plugin.new_session()
    step = handle.expect("go", returns=1)._script[0]

    unused = plugin.get_unused_mocks()
    assert unused == [step]


def test_get_unused_mocks_excludes_optional_steps() -> None:
    """get_unused_mocks() excludes steps with required=False."""
    # ESCAPE:
    # CLAIM: Optional steps (required=False) are not in get_unused_mocks().
    # PATH: get_unused_mocks() filters by step.required.
    # CHECK: Empty list when all steps are optional.
    # MUTATION: Not filtering by required would include optional steps.
    # ESCAPE: Nothing reasonable.
    # IMPACT: verify_all() would report optional steps as errors.
    plugin = _make_plugin()
    handle = plugin.new_session()
    handle.expect("go", returns=1, required=False)

    unused = plugin.get_unused_mocks()
    assert unused == []


def test_get_unused_mocks_returns_unexecuted_steps_from_active_session() -> None:
    """get_unused_mocks() includes remaining required steps from bound (active) sessions."""
    # ESCAPE:
    # CLAIM: A step on a bound-but-not-consumed session appears in get_unused_mocks().
    # PATH: get_unused_mocks() iterates _active_sessions.values(), collects remaining steps.
    # CHECK: Exact list with the one unconsumed step.
    # MUTATION: Only checking _session_queue would miss active sessions.
    # ESCAPE: Returning all steps (including consumed ones) would include more steps.
    # IMPACT: Required steps that were never executed would not be reported as unused.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=1)
    plugin._bind_connection(conn)

    # The step is registered but not yet executed
    step = handle._script[0]
    unused = plugin.get_unused_mocks()
    assert unused == [step]


def test_get_unused_mocks_empty_after_all_steps_consumed() -> None:
    """get_unused_mocks() returns empty list after all steps have been executed."""
    # ESCAPE:
    # CLAIM: A step that was executed is not in get_unused_mocks().
    # PATH: _execute_step() pops the step; get_unused_mocks() sees empty _script.
    # CHECK: == [] exact equality.
    # MUTATION: Not popping the step in _execute_step would leave it in _script.
    # ESCAPE: Nothing reasonable.
    # IMPACT: verify_all() would always fail after any execution.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=1)
    plugin._bind_connection(conn)

    plugin._execute_step(handle, "go", (), {}, "src")

    assert plugin.get_unused_mocks() == []


def test_get_unused_mocks_only_required_steps_from_mixed_session() -> None:
    """get_unused_mocks() returns only required=True steps when session has both kinds."""
    # ESCAPE:
    # CLAIM: A session with one required and one optional step produces only the required step.
    # PATH: get_unused_mocks() filters steps by required == True.
    # CHECK: Exact list of length 1 with the required step.
    # MUTATION: Including optional steps would produce a length-2 list.
    # ESCAPE: Returning the optional step instead of the required one would still be length 1
    #         but the step identity would differ.
    # IMPACT: verify_all() would miss required unused steps.
    plugin = _make_plugin()
    conn = _make_connection_obj()
    handle = plugin.new_session()
    handle.expect("go", returns=1)  # required=True by default
    handle.expect("reset", returns=2, required=False)
    plugin._bind_connection(conn)

    required_step = handle._script[0]
    unused = plugin.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0] is required_step


# ---------------------------------------------------------------------------
# Abstract method enforcement tests
# ---------------------------------------------------------------------------


def test_state_machine_plugin_cannot_be_instantiated_without_initial_state() -> None:
    """A subclass missing _initial_state() cannot be instantiated."""
    # ESCAPE:
    # CLAIM: _initial_state is abstract; missing it prevents instantiation.
    # PATH: ABC enforcement.
    # CHECK: TypeError on instantiation.
    # MUTATION: Removing @abstractmethod from _initial_state would allow instantiation.
    # ESCAPE: Nothing reasonable.
    # IMPACT: Plugins without initial state would start in undefined state.

    class _Missing(StateMachinePlugin):  # type: ignore[abstract]
        def _transitions(self) -> dict[str, dict[str, str]]:
            return {}

        def _unmocked_source_id(self) -> str:
            return "x"

        def activate(self) -> None:
            pass

        def deactivate(self) -> None:
            pass

        def format_interaction(self, i: Any) -> str:
            return ""

        def format_mock_hint(self, i: Any) -> str:
            return ""

        def format_unmocked_hint(self, s: str, a: tuple, k: dict) -> str:
            return ""  # type: ignore[override]

        def format_assert_hint(self, i: Any) -> str:
            return ""

        def format_unused_mock_hint(self, m: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        _Missing(_make_verifier())  # type: ignore[abstract]


def test_state_machine_plugin_cannot_be_instantiated_without_transitions() -> None:
    """A subclass missing _transitions() cannot be instantiated."""

    class _Missing(StateMachinePlugin):  # type: ignore[abstract]
        def _initial_state(self) -> str:
            return "a"

        def _unmocked_source_id(self) -> str:
            return "x"

        def activate(self) -> None:
            pass

        def deactivate(self) -> None:
            pass

        def format_interaction(self, i: Any) -> str:
            return ""

        def format_mock_hint(self, i: Any) -> str:
            return ""

        def format_unmocked_hint(self, s: str, a: tuple, k: dict) -> str:
            return ""  # type: ignore[override]

        def format_assert_hint(self, i: Any) -> str:
            return ""

        def format_unused_mock_hint(self, m: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        _Missing(_make_verifier())  # type: ignore[abstract]


def test_state_machine_plugin_cannot_be_instantiated_without_unmocked_source_id() -> None:
    """A subclass missing _unmocked_source_id() cannot be instantiated."""

    class _Missing(StateMachinePlugin):  # type: ignore[abstract]
        def _initial_state(self) -> str:
            return "a"

        def _transitions(self) -> dict[str, dict[str, str]]:
            return {}

        def activate(self) -> None:
            pass

        def deactivate(self) -> None:
            pass

        def format_interaction(self, i: Any) -> str:
            return ""

        def format_mock_hint(self, i: Any) -> str:
            return ""

        def format_unmocked_hint(self, s: str, a: tuple, k: dict) -> str:
            return ""  # type: ignore[override]

        def format_assert_hint(self, i: Any) -> str:
            return ""

        def format_unused_mock_hint(self, m: Any) -> str:
            return ""

    with pytest.raises(TypeError):
        _Missing(_make_verifier())  # type: ignore[abstract]
