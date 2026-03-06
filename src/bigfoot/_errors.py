"""All bigfoot exception classes.

This module imports NOTHING from other bigfoot modules to prevent circular imports.
"""

from __future__ import annotations

from typing import Any


class BigfootError(Exception):
    """Base class for all bigfoot errors."""


class UnmockedInteractionError(BigfootError):
    """Raised at call time: an interaction fired with no matching registered mock.

    Message includes: source description, args/kwargs, copy-pasteable mock hint.
    """

    def __init__(
        self,
        source_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        hint: str,
    ) -> None:
        self.source_id = source_id
        self.args_tuple = args
        self.kwargs = kwargs
        self.hint = hint
        super().__init__(
            f"UnmockedInteractionError: source_id={source_id!r}, "
            f"args={args!r}, kwargs={kwargs!r}, "
            f"hint={hint!r}"
        )


class UnassertedInteractionsError(BigfootError):
    """Raised at teardown: timeline contains interactions not matched by assert_interaction().

    Message lists each unasserted interaction with copy-pasteable assert hint.
    """

    def __init__(self, interactions: list[Any], hint: str) -> None:
        self.interactions = interactions
        self.hint = hint
        super().__init__(
            f"UnassertedInteractionsError: {len(interactions)} unasserted interaction(s), "
            f"hint={hint!r}"
        )


class UnusedMocksError(BigfootError):
    """Raised at teardown: registered mocks with required=True were never triggered.

    Message lists each unused mock with hint to either remove or set required=False.
    """

    def __init__(self, mocks: list[Any], hint: str) -> None:
        self.mocks = mocks
        self.hint = hint
        super().__init__(f"UnusedMocksError: {len(mocks)} unused mock(s), hint={hint!r}")


class VerificationError(BigfootError):
    """Raised at teardown when BOTH UnassertedInteractionsError and UnusedMocksError apply.

    Contains both reports in separate sections.
    """

    def __init__(
        self,
        unasserted: UnassertedInteractionsError | None,
        unused: UnusedMocksError | None,
    ) -> None:
        self.unasserted = unasserted
        self.unused = unused

        parts: list[str] = []
        if unasserted is not None:
            parts.append(f"  [UnassertedInteractions] {unasserted}")
        if unused is not None:
            parts.append(f"  [UnusedMocks] {unused}")

        if parts:
            body = "\n".join(parts)
            message = f"VerificationError:\n{body}"
        else:
            message = "VerificationError: (no details)"

        super().__init__(message)


class InteractionMismatchError(BigfootError):
    """Raised by assert_interaction() when expected source/fields don't match
    the next interaction in the timeline.

    Message includes: expected description, actual next interaction, remaining timeline.
    """

    def __init__(
        self,
        expected: object,
        actual: object,
        hint: str,
    ) -> None:
        self.expected = expected
        self.actual = actual
        self.hint = hint
        super().__init__(
            f"InteractionMismatchError: expected={expected!r}, actual={actual!r}, hint={hint!r}"
        )


class SandboxNotActiveError(BigfootError):
    """Raised when an intercepted call fires but no sandbox is active.

    Attributes:
        source_id: Identifier of the interceptor that fired without a sandbox.

    Message includes hint: 'Did you forget bigfoot_verifier fixture or sandbox() CM?'
    """

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        super().__init__(
            f"SandboxNotActiveError: source_id={source_id!r}, "
            "hint='Did you forget bigfoot_verifier fixture or sandbox() CM?'"
        )


class AssertionInsideSandboxError(BigfootError):
    """Raised when assert_interaction(), in_any_order(), or verify_all() is called
    while a sandbox is active on that verifier instance.

    Assertions must be made after the sandbox exits, not during it.
    """

    def __init__(self) -> None:
        super().__init__(
            "AssertionInsideSandboxError: assert_interaction(), in_any_order(), and verify_all() "
            "must be called after the sandbox has exited, not while it is active. "
            "Exit the sandbox first, then make assertions."
        )


class NoActiveVerifierError(BigfootError):
    """Raised when a module-level bigfoot function is called outside a test context."""

    def __str__(self) -> str:
        return (
            "NoActiveVerifierError: no active bigfoot verifier. "
            "Module-level bigfoot functions (mock, sandbox, assert_interaction, etc.) "
            "require an active test context. Ensure bigfoot is installed as a pytest "
            "plugin (it registers automatically) and you are running inside a pytest test."
        )


class ConflictError(BigfootError):
    """Raised at activate() time if target method is already patched by another library.

    Message names the conflicting library and the patched target.
    """

    def __init__(self, target: str, patcher: str) -> None:
        self.target = target
        self.patcher = patcher
        super().__init__(f"ConflictError: target={target!r}, patcher={patcher!r}")


class MissingAssertionFieldsError(BigfootError):
    """Raised by assert_interaction() when the caller omits one or more assertable
    fields from **expected.

    Attributes:
        missing_fields: frozenset of field names that were required but absent.
    """

    def __init__(self, missing_fields: frozenset[str]) -> None:
        self.missing_fields = missing_fields
        fields_str = ", ".join(sorted(missing_fields))
        super().__init__(
            f"MissingAssertionFieldsError: the following assertable fields were not "
            f"included in the assertion: {fields_str}. "
            f"Include them in **expected or use a dirty-equals matcher (e.g., IsAnything()) "
            f"if the value is not the focus of this assertion."
        )


class AutoAssertError(BigfootError):
    """Raised when mark_asserted() is called while record() is in progress.

    This indicates the auto-assert anti-pattern: a plugin calling
    timeline.mark_asserted() immediately after record() inside its intercept
    hook, bypassing the requirement for explicit test assertions.
    """


class InvalidStateError(BigfootError):
    """Raised when a state-machine method is called from an invalid state.

    Attributes:
        source_id: Identifier of the source that triggered the call.
        method: Name of the method that was called.
        current_state: The state the machine was in when the call was made.
        valid_states: The frozenset of states from which the call is permitted.
    """

    def __init__(
        self,
        source_id: str,
        method: str,
        current_state: str,
        valid_states: frozenset[str],
    ) -> None:
        self.source_id = source_id
        self.method = method
        self.current_state = current_state
        self.valid_states = valid_states
        super().__init__(
            f"'{method}' called in state '{current_state}'; valid from: {valid_states!r}"
        )
