"""StateMachinePlugin: base class for state-machine-driven bigfoot plugins."""

import threading
import traceback
from abc import abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bigfoot._base_plugin import BasePlugin
from bigfoot._errors import InvalidStateError, UnmockedInteractionError
from bigfoot._timeline import Interaction

if TYPE_CHECKING:
    from bigfoot._verifier import StrictVerifier


# ---------------------------------------------------------------------------
# ScriptStep
# ---------------------------------------------------------------------------


@dataclass
class ScriptStep:
    """A single expected call in a SessionHandle script.

    Attributes:
        method: The method name expected to be called.
        returns: The value to return when this step executes.
            There is no default; callers must be explicit.
        raises: If not None, this exception is raised instead of returning.
        required: If True, the step is reported as unused if never executed.
        registration_traceback: Captured automatically at creation time
            for use in error messages.
    """

    method: str
    returns: Any  # noqa: ANN401
    raises: BaseException | None = None
    required: bool = True
    registration_traceback: str = field(default_factory=lambda: "".join(traceback.format_stack()))


# ---------------------------------------------------------------------------
# SessionHandle
# ---------------------------------------------------------------------------


class SessionHandle:
    """Holds the state and script for a single mocked connection session.

    Not a dataclass — uses explicit __init__ for clarity.
    """

    def __init__(self, initial_state: str) -> None:
        self._state: str = initial_state
        self._script: list[ScriptStep] = []
        self._lock: threading.Lock = threading.Lock()
        self._connection_obj: object | None = None

    def expect(
        self,
        method: str,
        *,
        returns: Any,  # noqa: ANN401
        raises: BaseException | None = None,
        required: bool = True,
    ) -> "SessionHandle":
        """Append an expected call to the script.

        Args:
            method: Name of the method expected to be called.
            returns: Value to return when this step executes.
                This is a required keyword argument — callers must be explicit.
            raises: If provided, this exception is raised instead of returning.
            required: If False, the step is not reported as unused at teardown.

        Returns:
            self, for method chaining.
        """
        step = ScriptStep(
            method=method,
            returns=returns,
            raises=raises,
            required=required,
        )
        self._script.append(step)
        return self


# ---------------------------------------------------------------------------
# StateMachinePlugin
# ---------------------------------------------------------------------------


class StateMachinePlugin(BasePlugin):
    """Abstract base for state-machine-driven plugins.

    Concrete subclasses define:
    - States and transitions via _initial_state() and _transitions()
    - Connection lifecycle via activate() / deactivate()
    - Error message formatting via the format_* methods

    Session lifecycle:
    1. Test code calls new_session() to create and queue a SessionHandle.
    2. A connection is established; the concrete plugin calls _bind_connection(conn)
       to pop a handle from the queue and register it with the connection object.
       (Plugins where the connection object is created AFTER the queue pop -- such
       as WebSocket plugins -- may instead pop the queue manually and call
       _register_connection(handle, conn) to complete the binding.)
    3. Each method call on the connection delegates to _execute_step().
    4. When the connection closes, the concrete plugin calls _release_session(conn).
    """

    def __init__(self, verifier: "StrictVerifier") -> None:
        super().__init__(verifier)
        self._session_queue: deque[SessionHandle] = deque()
        self._active_sessions: dict[int, SessionHandle] = {}
        self._connection_refs: dict[int, object] = {}
        self._registry_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Abstract methods (plugin authors must implement these)
    # ------------------------------------------------------------------

    @abstractmethod
    def _initial_state(self) -> str:
        """Return the name of the initial state for new sessions."""

    @abstractmethod
    def _transitions(self) -> dict[str, dict[str, str]]:
        """Return the transitions table.

        Structure: {method_name: {from_state: to_state}}
        """

    @abstractmethod
    def _unmocked_source_id(self) -> str:
        """Return the source_id string used when raising UnmockedInteractionError
        for an empty session queue."""

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def new_session(self) -> SessionHandle:
        """Create a new SessionHandle and enqueue it for the next connection.

        Returns:
            The new SessionHandle. The caller should chain .expect() calls
            on the returned handle to configure the script.
        """
        handle = SessionHandle(self._initial_state())
        self._session_queue.append(handle)
        return handle

    def _register_connection(self, handle: SessionHandle, connection_obj: object) -> None:
        """Register an already-popped session handle with a connection object.

        Use this when the session handle was obtained separately (e.g., popped from
        the queue before the connection object was created). This completes the
        binding established by _bind_connection() for the cases where the queue pop
        must happen before the connection object exists.
        """
        with self._registry_lock:
            handle._connection_obj = connection_obj
            self._active_sessions[id(connection_obj)] = handle
            self._connection_refs[id(connection_obj)] = connection_obj

    def _bind_connection(self, connection_obj: object) -> SessionHandle:
        """Pop the next queued SessionHandle and bind it to connection_obj.

        Raises:
            UnmockedInteractionError: If the session queue is empty.
        """
        with self._registry_lock:
            if not self._session_queue:
                source_id = self._unmocked_source_id()
                hint = self.format_unmocked_hint(source_id, (), {})
                raise UnmockedInteractionError(
                    source_id=source_id,
                    args=(),
                    kwargs={},
                    hint=hint,
                )
            handle = self._session_queue.popleft()
        self._register_connection(handle, connection_obj)
        return handle

    def _lookup_session(self, connection_obj: object) -> SessionHandle:
        """Return the SessionHandle bound to connection_obj.

        Raises:
            UnmockedInteractionError: If no session is bound to this connection.
        """
        handle = self._active_sessions.get(id(connection_obj))
        if handle is None:
            source_id = self._unmocked_source_id()
            hint = self.format_unmocked_hint(source_id, (), {})
            raise UnmockedInteractionError(
                source_id=source_id,
                args=(),
                kwargs={},
                hint=hint,
            )
        return handle

    def _release_session(self, connection_obj: object) -> None:
        """Remove the session associated with connection_obj from active tracking.

        Drops both the handle and the strong reference to connection_obj,
        allowing the connection object to be garbage collected.
        """
        with self._registry_lock:
            key = id(connection_obj)
            self._active_sessions.pop(key, None)
            self._connection_refs.pop(key, None)

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    def _execute_step(
        self,
        handle: SessionHandle,
        method: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        source_id: str,
    ) -> Any:  # noqa: ANN401
        """Execute the next script step for the given handle and method.

        Steps:
        1. Validate that method is allowed from the current state.
        2. Pop the next ScriptStep (FIFO).
        3. Advance handle._state.
        4. Record the Interaction on the timeline and immediately mark it asserted.
        5. If step.raises is set, raise it; otherwise return step.returns.

        Raises:
            InvalidStateError: If the current state is not a valid from-state
                for this method, or if the method is not in _transitions().
            UnmockedInteractionError: If handle._script is empty.
        """
        with handle._lock:
            transitions = self._transitions()

            # Validate method is registered in the transition table
            if method not in transitions:
                raise InvalidStateError(
                    source_id=source_id,
                    method=method,
                    current_state=handle._state,
                    valid_states=frozenset(),
                )

            # Validate current state is a valid from-state for this method
            method_transitions = transitions[method]
            valid_from_states = frozenset(method_transitions.keys())
            if handle._state not in method_transitions:
                raise InvalidStateError(
                    source_id=source_id,
                    method=method,
                    current_state=handle._state,
                    valid_states=valid_from_states,
                )

            # Pop next step (FIFO)
            if not handle._script:
                hint = self.format_unmocked_hint(source_id, args, kwargs)
                raise UnmockedInteractionError(
                    source_id=source_id,
                    args=args,
                    kwargs=kwargs,
                    hint=hint,
                )

            step = handle._script.pop(0)

            # Advance state
            handle._state = method_transitions[handle._state]

            # Record interaction and immediately mark asserted
            interaction = Interaction(
                source_id=source_id,
                sequence=0,
                details={"method": method, "args": args, "kwargs": kwargs},
                plugin=self,
            )
            self.record(interaction)
            self.verifier._timeline.mark_asserted(interaction)

            # Execute step
            if step.raises is not None:
                raise step.raises
            return step.returns

    # ------------------------------------------------------------------
    # BasePlugin: overridden concrete methods
    # ------------------------------------------------------------------

    def matches(self, interaction: Interaction, expected: dict[str, Any]) -> bool:
        """Always returns True — state machine interactions are auto-matched."""
        return True

    def assertable_fields(self, interaction: Interaction) -> frozenset[str]:
        """Returns empty frozenset — no fields are required in assert_interaction()."""
        return frozenset()

    # ------------------------------------------------------------------
    # BasePlugin: get_unused_mocks
    # ------------------------------------------------------------------

    def get_unused_mocks(self) -> list[ScriptStep]:
        """Return all required ScriptSteps that were never executed.

        Includes steps from both:
        - Sessions still in _session_queue (never bound to a connection)
        - Sessions in _active_sessions (bound but not fully consumed)
        """
        unused: list[ScriptStep] = []
        for handle in self._session_queue:
            for step in handle._script:
                if step.required:
                    unused.append(step)
        for handle in self._active_sessions.values():
            for step in handle._script:
                if step.required:
                    unused.append(step)
        return unused
