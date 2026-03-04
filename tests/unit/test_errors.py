"""Tests for Task 2: bigfoot error classes.

All 7 error types plus bigfootError base. Tests follow TDD protocol:
each test must fail before implementation exists.
"""

import pytest

from bigfoot._errors import (
    ConflictError,
    InteractionMismatchError,
    bigfootError,
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
    """bigfootError is the base and must be a proper Exception."""
    assert issubclass(bigfootError, Exception)


def test_all_errors_subclass_bigfoot_error() -> None:
    """Every domain error must be catchable as bigfootError."""
    assert issubclass(UnmockedInteractionError, bigfootError)
    assert issubclass(UnassertedInteractionsError, bigfootError)
    assert issubclass(UnusedMocksError, bigfootError)
    assert issubclass(VerificationError, bigfootError)
    assert issubclass(InteractionMismatchError, bigfootError)
    assert issubclass(SandboxNotActiveError, bigfootError)
    assert issubclass(ConflictError, bigfootError)


def test_all_errors_subclass_exception() -> None:
    """Every domain error must be catchable as plain Exception."""
    assert issubclass(UnmockedInteractionError, Exception)
    assert issubclass(UnassertedInteractionsError, Exception)
    assert issubclass(UnusedMocksError, Exception)
    assert issubclass(VerificationError, Exception)
    assert issubclass(InteractionMismatchError, Exception)
    assert issubclass(SandboxNotActiveError, Exception)
    assert issubclass(ConflictError, Exception)


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
    with pytest.raises(bigfootError):
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
    with pytest.raises(bigfootError):
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
    with pytest.raises(bigfootError):
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
        "UnusedMocksError: 1 unused mock(s), "
        "hint='Remove or set required=False for unused mocks.'"
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
    with pytest.raises(bigfootError):
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
        "VerificationError:\n"
        "  [UnusedMocks] UnusedMocksError: 1 unused mock(s), "
        "hint='Fix unused.'"
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
    with pytest.raises(bigfootError):
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
    with pytest.raises(bigfootError):
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
    with pytest.raises(bigfootError):
        raise ConflictError(target="httpx.Client.send", patcher="httpretty")


def test_conflict_error_str() -> None:
    """__str__ names the conflicting target and patcher library."""
    err = ConflictError(target="urllib.request.urlopen", patcher="responses")
    result = str(err)
    assert result == (
        "ConflictError: target='urllib.request.urlopen', patcher='responses'"
    )
