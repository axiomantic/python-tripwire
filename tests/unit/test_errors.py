"""Tests for Task 2: bigfoot error classes.

All 7 error types plus BigfootError base. Tests follow TDD protocol:
each test must fail before implementation exists.
"""

import pytest

from bigfoot._errors import (
    AssertionInsideSandboxError,
    AutoAssertError,
    BigfootError,
    ConflictError,
    InteractionMismatchError,
    MissingAssertionFieldsError,
    NoActiveVerifierError,
    SandboxNotActiveError,
    UnassertedInteractionsError,
    UnmockedInteractionError,
    UnusedMocksError,
    VerificationError,
)

# ---------------------------------------------------------------------------
# Hierarchy / subclass tests
# ---------------------------------------------------------------------------


def test_bigfoot_error_is_exception() -> None:
    """BigfootError is the base and must be a proper Exception."""
    assert issubclass(BigfootError, Exception)


def test_all_errors_subclass_bigfoot_error() -> None:
    """Every domain error must be catchable as BigfootError."""
    assert issubclass(UnmockedInteractionError, BigfootError)
    assert issubclass(UnassertedInteractionsError, BigfootError)
    assert issubclass(UnusedMocksError, BigfootError)
    assert issubclass(VerificationError, BigfootError)
    assert issubclass(InteractionMismatchError, BigfootError)
    assert issubclass(SandboxNotActiveError, BigfootError)
    assert issubclass(ConflictError, BigfootError)
    assert issubclass(AssertionInsideSandboxError, BigfootError)
    assert issubclass(NoActiveVerifierError, BigfootError)
    assert issubclass(MissingAssertionFieldsError, BigfootError)
    assert issubclass(AutoAssertError, BigfootError)


def test_all_errors_subclass_exception() -> None:
    """Every domain error must be catchable as plain Exception."""
    assert issubclass(UnmockedInteractionError, Exception)
    assert issubclass(UnassertedInteractionsError, Exception)
    assert issubclass(UnusedMocksError, Exception)
    assert issubclass(VerificationError, Exception)
    assert issubclass(InteractionMismatchError, Exception)
    assert issubclass(SandboxNotActiveError, Exception)
    assert issubclass(ConflictError, Exception)
    assert issubclass(AssertionInsideSandboxError, Exception)
    assert issubclass(NoActiveVerifierError, Exception)
    assert issubclass(MissingAssertionFieldsError, Exception)
    assert issubclass(AutoAssertError, Exception)


# ---------------------------------------------------------------------------
# UnmockedInteractionError
# ---------------------------------------------------------------------------


def test_unmocked_interaction_error_fields() -> None:
    """Fields are stored and accessible after construction."""
    err = UnmockedInteractionError(
        source_id="http.get",
        args=("https://example.com",),
        kwargs={"timeout": 5},
        hint="Register a mock with bigfoot.mock('http.get', ...)",
    )
    assert err.source_id == "http.get"
    assert err.args_tuple == ("https://example.com",)
    assert err.kwargs == {"timeout": 5}
    assert err.hint == "Register a mock with bigfoot.mock('http.get', ...)"


def test_unmocked_interaction_error_missing_fields_raises_type_error() -> None:
    """Instantiating without required fields must raise TypeError."""
    with pytest.raises(TypeError):
        UnmockedInteractionError()  # type: ignore[call-arg]


def test_unmocked_interaction_error_is_catchable_as_bigfoot_error() -> None:
    """Must be raiseable and catchable via the base class."""
    with pytest.raises(BigfootError):
        raise UnmockedInteractionError(
            source_id="db.query",
            args=(),
            kwargs={},
            hint="No hint available.",
        )


def test_unmocked_interaction_error_str() -> None:
    """__str__ contains source_id and hint for clear diagnostics."""
    err = UnmockedInteractionError(
        source_id="http.post",
        args=("/api/v1/submit",),
        kwargs={"json": {"key": "val"}},
        hint="Add mock for http.post",
    )
    result = str(err)
    assert result == (
        "UnmockedInteractionError: source_id='http.post', "
        "args=('/api/v1/submit',), kwargs={'json': {'key': 'val'}}, "
        "hint='Add mock for http.post'"
    )


# ---------------------------------------------------------------------------
# UnassertedInteractionsError
# ---------------------------------------------------------------------------


def test_unasserted_interactions_error_fields() -> None:
    """Fields are stored and accessible after construction."""
    interactions = [{"source_id": "http.get", "args": (), "kwargs": {}}]
    err = UnassertedInteractionsError(
        interactions=interactions,
        hint="Call assert_interaction() for each recorded interaction.",
    )
    assert err.interactions == [{"source_id": "http.get", "args": (), "kwargs": {}}]
    assert err.hint == "Call assert_interaction() for each recorded interaction."


def test_unasserted_interactions_error_missing_fields_raises_type_error() -> None:
    """Instantiating without required fields must raise TypeError."""
    with pytest.raises(TypeError):
        UnassertedInteractionsError()  # type: ignore[call-arg]


def test_unasserted_interactions_error_is_catchable_as_bigfoot_error() -> None:
    """Must be raiseable and catchable via the base class."""
    with pytest.raises(BigfootError):
        raise UnassertedInteractionsError(interactions=[], hint="No hint.")


def test_unasserted_interactions_error_str() -> None:
    """__str__ contains count of interactions and hint."""
    interactions = [
        {"source_id": "http.get", "args": ("/foo",), "kwargs": {}},
        {"source_id": "db.insert", "args": (), "kwargs": {"table": "users"}},
    ]
    err = UnassertedInteractionsError(
        interactions=interactions,
        hint="Assert all recorded interactions before teardown.",
    )
    result = str(err)
    assert result == (
        "UnassertedInteractionsError: 2 unasserted interaction(s), "
        "hint='Assert all recorded interactions before teardown.'"
    )


# ---------------------------------------------------------------------------
# UnusedMocksError
# ---------------------------------------------------------------------------


def test_unused_mocks_error_fields() -> None:
    """Fields are stored and accessible after construction."""
    mocks = [{"source_id": "http.post", "required": True}]
    err = UnusedMocksError(
        mocks=mocks,
        hint="Remove unused mocks or set required=False.",
    )
    assert err.mocks == [{"source_id": "http.post", "required": True}]
    assert err.hint == "Remove unused mocks or set required=False."


def test_unused_mocks_error_missing_fields_raises_type_error() -> None:
    """Instantiating without required fields must raise TypeError."""
    with pytest.raises(TypeError):
        UnusedMocksError()  # type: ignore[call-arg]


def test_unused_mocks_error_is_catchable_as_bigfoot_error() -> None:
    """Must be raiseable and catchable via the base class."""
    with pytest.raises(BigfootError):
        raise UnusedMocksError(mocks=[], hint="No hint.")


def test_unused_mocks_error_str() -> None:
    """__str__ contains count of unused mocks and hint."""
    mocks = [
        {"source_id": "http.put", "required": True},
    ]
    err = UnusedMocksError(
        mocks=mocks,
        hint="Remove or set required=False for unused mocks.",
    )
    result = str(err)
    assert result == (
        "UnusedMocksError: 1 unused mock(s), hint='Remove or set required=False for unused mocks.'"
    )


# ---------------------------------------------------------------------------
# VerificationError
# ---------------------------------------------------------------------------


def test_verification_error_fields_both_set() -> None:
    """Both unasserted and unused fields stored and accessible."""
    unasserted = UnassertedInteractionsError(interactions=[], hint="h1")
    unused = UnusedMocksError(mocks=[], hint="h2")
    err = VerificationError(unasserted=unasserted, unused=unused)
    assert err.unasserted is unasserted
    assert err.unused is unused


def test_verification_error_fields_only_unasserted() -> None:
    """unasserted set; unused is None."""
    unasserted = UnassertedInteractionsError(interactions=[], hint="h")
    err = VerificationError(unasserted=unasserted, unused=None)
    assert err.unasserted is unasserted
    assert err.unused is None


def test_verification_error_fields_only_unused() -> None:
    """unused set; unasserted is None."""
    unused = UnusedMocksError(mocks=[], hint="h")
    err = VerificationError(unasserted=None, unused=unused)
    assert err.unasserted is None
    assert err.unused is unused


def test_verification_error_fields_neither_set() -> None:
    """Both fields None is a valid construction (degenerate case)."""
    err = VerificationError(unasserted=None, unused=None)
    assert err.unasserted is None
    assert err.unused is None


def test_verification_error_missing_fields_raises_type_error() -> None:
    """Instantiating without required fields must raise TypeError."""
    with pytest.raises(TypeError):
        VerificationError()  # type: ignore[call-arg]


def test_verification_error_is_catchable_as_bigfoot_error() -> None:
    """Must be raiseable and catchable via the base class."""
    with pytest.raises(BigfootError):
        raise VerificationError(unasserted=None, unused=None)


def test_verification_error_str_both_set() -> None:
    """__str__ with both fields produces a combined two-section report."""
    unasserted = UnassertedInteractionsError(
        interactions=[{"source_id": "http.get", "args": (), "kwargs": {}}],
        hint="Assert each interaction.",
    )
    unused = UnusedMocksError(
        mocks=[{"source_id": "db.write", "required": True}],
        hint="Remove or set required=False.",
    )
    err = VerificationError(unasserted=unasserted, unused=unused)
    result = str(err)
    assert result == (
        "VerificationError:\n"
        "  [UnassertedInteractions] UnassertedInteractionsError: 1 unasserted interaction(s), "
        "hint='Assert each interaction.'\n"
        "  [UnusedMocks] UnusedMocksError: 1 unused mock(s), "
        "hint='Remove or set required=False.'"
    )


def test_verification_error_str_only_unasserted() -> None:
    """__str__ with only unasserted set omits the unused section."""
    unasserted = UnassertedInteractionsError(
        interactions=[],
        hint="Nothing to assert.",
    )
    err = VerificationError(unasserted=unasserted, unused=None)
    result = str(err)
    assert result == (
        "VerificationError:\n"
        "  [UnassertedInteractions] UnassertedInteractionsError: 0 unasserted interaction(s), "
        "hint='Nothing to assert.'"
    )


def test_verification_error_str_only_unused() -> None:
    """__str__ with only unused set omits the unasserted section."""
    unused = UnusedMocksError(
        mocks=[{"source_id": "x", "required": True}],
        hint="Fix unused.",
    )
    err = VerificationError(unasserted=None, unused=unused)
    result = str(err)
    assert result == (
        "VerificationError:\n  [UnusedMocks] UnusedMocksError: 1 unused mock(s), hint='Fix unused.'"
    )


def test_verification_error_str_neither_set() -> None:
    """__str__ with neither field set produces a minimal report."""
    err = VerificationError(unasserted=None, unused=None)
    result = str(err)
    assert result == "VerificationError: (no details)"


# ---------------------------------------------------------------------------
# InteractionMismatchError
# ---------------------------------------------------------------------------


def test_interaction_mismatch_error_fields() -> None:
    """Fields are stored and accessible after construction."""
    actual_interaction = {"source_id": "db.read", "args": (42,), "kwargs": {}}
    err = InteractionMismatchError(
        expected={"source_id": "http.get", "url": "/api/items"},
        actual=actual_interaction,
        hint="Check assert_interaction() call order.",
    )
    assert err.expected == {"source_id": "http.get", "url": "/api/items"}
    assert err.actual == {"source_id": "db.read", "args": (42,), "kwargs": {}}
    assert err.hint == "Check assert_interaction() call order."


def test_interaction_mismatch_error_missing_fields_raises_type_error() -> None:
    """Instantiating without required fields must raise TypeError."""
    with pytest.raises(TypeError):
        InteractionMismatchError()  # type: ignore[call-arg]


def test_interaction_mismatch_error_is_catchable_as_bigfoot_error() -> None:
    """Must be raiseable and catchable via the base class."""
    with pytest.raises(BigfootError):
        raise InteractionMismatchError(
            expected={"source_id": "http.get"},
            actual={"source_id": "db.read"},
            hint="Mismatch.",
        )


def test_interaction_mismatch_error_str() -> None:
    """__str__ contains expected, actual, and hint."""
    err = InteractionMismatchError(
        expected={"source_id": "http.get"},
        actual={"source_id": "db.read"},
        hint="Check order of assert_interaction() calls.",
    )
    result = str(err)
    assert result == (
        "InteractionMismatchError: "
        "expected={'source_id': 'http.get'}, "
        "actual={'source_id': 'db.read'}, "
        "hint='Check order of assert_interaction() calls.'"
    )


# ---------------------------------------------------------------------------
# SandboxNotActiveError
# ---------------------------------------------------------------------------


def test_sandbox_not_active_error_fields() -> None:
    """source_id field is stored and accessible after construction."""
    err = SandboxNotActiveError(source_id="http.get")
    assert err.source_id == "http.get"


def test_sandbox_not_active_error_missing_fields_raises_type_error() -> None:
    """Instantiating without required fields must raise TypeError."""
    with pytest.raises(TypeError):
        SandboxNotActiveError()  # type: ignore[call-arg]


def test_sandbox_not_active_error_is_catchable_as_bigfoot_error() -> None:
    """Must be raiseable and catchable via the base class."""
    with pytest.raises(BigfootError):
        raise SandboxNotActiveError(source_id="db.write")


def test_sandbox_not_active_error_str() -> None:
    """__str__ contains source_id and the standard diagnostic hint."""
    err = SandboxNotActiveError(source_id="http.get")
    result = str(err)
    assert result == (
        "SandboxNotActiveError: source_id='http.get', "
        "hint='Did you forget bigfoot_verifier fixture or sandbox() CM?'"
    )


# ---------------------------------------------------------------------------
# ConflictError
# ---------------------------------------------------------------------------


def test_conflict_error_fields() -> None:
    """target and patcher fields are stored and accessible after construction."""
    err = ConflictError(target="urllib.request.urlopen", patcher="responses")
    assert err.target == "urllib.request.urlopen"
    assert err.patcher == "responses"


def test_conflict_error_missing_fields_raises_type_error() -> None:
    """Instantiating without required fields must raise TypeError."""
    with pytest.raises(TypeError):
        ConflictError()  # type: ignore[call-arg]


def test_conflict_error_is_catchable_as_bigfoot_error() -> None:
    """Must be raiseable and catchable via the base class."""
    with pytest.raises(BigfootError):
        raise ConflictError(target="httpx.Client.send", patcher="httpretty")


def test_conflict_error_str() -> None:
    """__str__ names the conflicting target and patcher library."""
    err = ConflictError(target="urllib.request.urlopen", patcher="responses")
    result = str(err)
    assert result == ("ConflictError: target='urllib.request.urlopen', patcher='responses'")


# ---------------------------------------------------------------------------
# AssertionInsideSandboxError
# ---------------------------------------------------------------------------


def test_assertion_inside_sandbox_error_takes_no_arguments() -> None:
    """AssertionInsideSandboxError must be constructable with no arguments."""
    err = AssertionInsideSandboxError()
    assert str(err) == (
        "AssertionInsideSandboxError: assert_interaction(), in_any_order(), and verify_all() "
        "must be called after the sandbox has exited, not while it is active. "
        "Exit the sandbox first, then make assertions."
    )


def test_assertion_inside_sandbox_error_is_catchable_as_bigfoot_error() -> None:
    """Must be raiseable and catchable via the base class."""
    with pytest.raises(BigfootError):
        raise AssertionInsideSandboxError()


def test_assertion_inside_sandbox_error_str() -> None:
    """__str__ mentions all three guarded methods and explains the constraint."""
    err = AssertionInsideSandboxError()
    result = str(err)
    assert result == (
        "AssertionInsideSandboxError: assert_interaction(), in_any_order(), and verify_all() "
        "must be called after the sandbox has exited, not while it is active. "
        "Exit the sandbox first, then make assertions."
    )


# ---------------------------------------------------------------------------
# NoActiveVerifierError
# ---------------------------------------------------------------------------


def test_no_active_verifier_error_takes_no_arguments() -> None:
    """NoActiveVerifierError must be constructable with no arguments."""
    err = NoActiveVerifierError()
    assert str(err) == (
        "NoActiveVerifierError: no active bigfoot verifier. "
        "Module-level bigfoot functions (mock, sandbox, assert_interaction, etc.) "
        "require an active test context. Ensure bigfoot is installed as a pytest "
        "plugin (it registers automatically) and you are running inside a pytest test."
    )


def test_no_active_verifier_error_is_catchable_as_bigfoot_error() -> None:
    """Must be raiseable and catchable via the base class."""
    with pytest.raises(BigfootError):
        raise NoActiveVerifierError()


def test_no_active_verifier_error_str() -> None:
    """__str__ explains the missing verifier context and how to fix it."""
    err = NoActiveVerifierError()
    result = str(err)
    assert result == (
        "NoActiveVerifierError: no active bigfoot verifier. "
        "Module-level bigfoot functions (mock, sandbox, assert_interaction, etc.) "
        "require an active test context. Ensure bigfoot is installed as a pytest "
        "plugin (it registers automatically) and you are running inside a pytest test."
    )


# ---------------------------------------------------------------------------
# MissingAssertionFieldsError
# ---------------------------------------------------------------------------


def test_missing_assertion_fields_error_fields() -> None:
    """missing_fields attribute stores the frozenset passed at construction."""
    err = MissingAssertionFieldsError(frozenset({"args", "kwargs"}))
    assert err.missing_fields == frozenset({"args", "kwargs"})


def test_missing_assertion_fields_error_is_bigfoot_error() -> None:
    """MissingAssertionFieldsError must be a subclass of BigfootError."""
    assert issubclass(MissingAssertionFieldsError, BigfootError)


def test_missing_assertion_fields_error_is_exception() -> None:
    """MissingAssertionFieldsError must be catchable as Exception."""
    assert issubclass(MissingAssertionFieldsError, Exception)


def test_missing_assertion_fields_error_str_single_field() -> None:
    """__str__ lists the missing field name alphabetically."""
    err = MissingAssertionFieldsError(frozenset({"args"}))
    result = str(err)
    assert result == (
        "MissingAssertionFieldsError: the following assertable fields were not "
        "included in the assertion: args. "
        "Include them in **expected or use a dirty-equals matcher (e.g., IsAnything()) "
        "if the value is not the focus of this assertion."
    )


def test_missing_assertion_fields_error_str_multiple_fields_sorted() -> None:
    """__str__ sorts multiple field names alphabetically."""
    err = MissingAssertionFieldsError(frozenset({"kwargs", "args"}))
    result = str(err)
    assert result == (
        "MissingAssertionFieldsError: the following assertable fields were not "
        "included in the assertion: args, kwargs. "
        "Include them in **expected or use a dirty-equals matcher (e.g., IsAnything()) "
        "if the value is not the focus of this assertion."
    )


def test_missing_assertion_fields_error_is_raiseable() -> None:
    """Must be raiseable and catchable via the base class."""
    with pytest.raises(BigfootError):
        raise MissingAssertionFieldsError(frozenset({"args"}))


# ---------------------------------------------------------------------------
# InvalidStateError
# ---------------------------------------------------------------------------


def test_invalid_state_error_message_format() -> None:
    """__str__ matches the exact required format."""
    from bigfoot._errors import InvalidStateError

    err = InvalidStateError(
        source_id="my_source",
        method="start",
        current_state="stopped",
        valid_states=frozenset({"idle", "paused"}),
    )
    result = str(err)
    assert result == (
        f"'start' called in state 'stopped'; valid from: {frozenset({'idle', 'paused'})!r}"
    )


def test_invalid_state_error_attributes() -> None:
    """All four constructor arguments are stored as attributes."""
    from bigfoot._errors import InvalidStateError

    err = InvalidStateError(
        source_id="src_abc",
        method="stop",
        current_state="running",
        valid_states=frozenset({"idle"}),
    )
    assert err.source_id == "src_abc"
    assert err.method == "stop"
    assert err.current_state == "running"
    assert err.valid_states == frozenset({"idle"})


def test_invalid_state_error_catchable_as_bigfoot_error() -> None:
    """InvalidStateError must be catchable as BigfootError."""
    from bigfoot._errors import InvalidStateError

    with pytest.raises(BigfootError):
        raise InvalidStateError(
            source_id="s",
            method="m",
            current_state="c",
            valid_states=frozenset({"v"}),
        )


# ---------------------------------------------------------------------------
# AutoAssertError
# ---------------------------------------------------------------------------


def test_auto_assert_error_is_bigfoot_error() -> None:
    """AutoAssertError is a subclass of BigfootError."""
    from bigfoot._errors import AutoAssertError, BigfootError
    assert issubclass(AutoAssertError, BigfootError)


def test_auto_assert_error_message() -> None:
    """AutoAssertError stores the message passed to it."""
    from bigfoot._errors import AutoAssertError
    err = AutoAssertError("test message")
    assert "test message" in str(err)


def test_auto_assert_error_exported_from_bigfoot() -> None:
    """AutoAssertError is accessible from the top-level bigfoot module."""
    import bigfoot
    assert hasattr(bigfoot, "AutoAssertError")
    from bigfoot import AutoAssertError
    assert AutoAssertError is not None
