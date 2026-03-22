"""Unit tests for MongoPlugin."""

from __future__ import annotations

from unittest.mock import MagicMock

import pymongo
import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.mongo_plugin import (
    _PYMONGO_AVAILABLE,
    MongoMockConfig,
    MongoPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, MongoPlugin]:
    """Return (verifier, plugin) with MongoPlugin registered but NOT activated.

    The verifier auto-instantiates plugins, so we retrieve the existing
    MongoPlugin rather than creating a duplicate.
    """
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, MongoPlugin):
            return v, p
    p = MongoPlugin(v)
    return v, p


def _make_collection(db_name: str = "testdb", coll_name: str = "testcoll") -> pymongo.collection.Collection:
    """Create a pymongo Collection object with controlled database/collection names."""
    from bson.codec_options import CodecOptions
    from pymongo.read_concern import ReadConcern
    from pymongo.read_preferences import Primary
    from pymongo.synchronous.database import Database
    from pymongo.write_concern import WriteConcern

    db = MagicMock(spec=Database)
    db.name = db_name
    db.codec_options = CodecOptions()
    db.read_preference = Primary()
    db.write_concern = WriteConcern()
    db.read_concern = ReadConcern()
    coll = pymongo.collection.Collection(db, coll_name)
    return coll


def _reset_plugin_count() -> None:
    """Force-reset the class-level install count to 0 and restore patches if leaked."""
    with MongoPlugin._install_lock:
        MongoPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        MongoPlugin.__new__(MongoPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    """Ensure plugin install count starts and ends at 0 for every test."""
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


# ESCAPE: test_pymongo_available_flag
#   CLAIM: _PYMONGO_AVAILABLE is True when pymongo is importable.
#   PATH:  Module-level try/except import guard in mongo_plugin.py.
#   CHECK: _PYMONGO_AVAILABLE is True (since pymongo is installed).
#   MUTATION: Setting it to False when pymongo IS importable fails the equality check.
#   ESCAPE: Nothing reasonable -- exact boolean equality.
def test_pymongo_available_flag() -> None:
    assert _PYMONGO_AVAILABLE is True


# ESCAPE: test_activate_raises_when_pymongo_unavailable
#   CLAIM: If _PYMONGO_AVAILABLE is False, calling activate() raises ImportError
#          with the exact installation hint message.
#   PATH:  activate() -> check _PYMONGO_AVAILABLE -> False -> raise ImportError.
#   CHECK: ImportError raised; str(exc) == exact message string.
#   MUTATION: Not checking the flag and proceeding normally would not raise.
#   ESCAPE: Raising ImportError with a different message fails the exact string check.
def test_activate_raises_when_pymongo_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import bigfoot.plugins.mongo_plugin as _mp

    v, p = _make_verifier_with_plugin()
    monkeypatch.setattr(_mp, "_PYMONGO_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install bigfoot[mongo] to use MongoPlugin: pip install bigfoot[mongo]"
    )


# ---------------------------------------------------------------------------
# MongoMockConfig dataclass
# ---------------------------------------------------------------------------


# ESCAPE: test_mongo_mock_config_fields
#   CLAIM: MongoMockConfig stores operation, returns, raises, required correctly.
#   PATH:  Dataclass construction.
#   CHECK: All fields equal their expected values.
#   MUTATION: Wrong field name or default value fails equality check.
#   ESCAPE: Nothing reasonable -- exact equality on all fields.
def test_mongo_mock_config_fields() -> None:
    err = ValueError("bad document")
    config = MongoMockConfig(operation="find", returns=[{"a": 1}], raises=err, required=False)
    assert config.operation == "find"
    assert config.returns == [{"a": 1}]
    assert config.raises is err
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


# ESCAPE: test_mongo_mock_config_defaults
#   CLAIM: MongoMockConfig defaults: raises=None, required=True.
#   PATH:  Dataclass construction with minimal arguments.
#   CHECK: raises is None; required is True.
#   MUTATION: Wrong default for required fails equality check.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_mongo_mock_config_defaults() -> None:
    config = MongoMockConfig(operation="find_one", returns={"x": 1})
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


# ESCAPE: test_activate_installs_patch
#   CLAIM: After activate(), pymongo.collection.Collection.find is replaced with bigfoot interceptor.
#   PATH:  activate() -> _install_count == 0 -> store originals -> install interceptors.
#   CHECK: pymongo.collection.Collection.find is not the original after activate().
#   MUTATION: Skipping patch installation leaves original in place; identity check fails.
#   ESCAPE: Nothing reasonable -- identity comparison proves replacement.
def test_activate_installs_patch() -> None:
    original_find = pymongo.collection.Collection.find
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert pymongo.collection.Collection.find is not original_find
    p.deactivate()


# ESCAPE: test_deactivate_restores_patch
#   CLAIM: After activate() then deactivate(), pymongo.collection.Collection.find is restored.
#   PATH:  deactivate() -> _install_count reaches 0 -> restore originals.
#   CHECK: pymongo.collection.Collection.find is the original after deactivate().
#   MUTATION: Not restoring in deactivate() leaves bigfoot's interceptor in place.
#   ESCAPE: Nothing reasonable -- identity comparison against saved original.
def test_deactivate_restores_patch() -> None:
    original_find = pymongo.collection.Collection.find
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert pymongo.collection.Collection.find is original_find


# ESCAPE: test_reference_counting_nested
#   CLAIM: Two activate() calls require two deactivate() calls before patch is removed.
#   PATH:  First activate -> _install_count=1; second -> _install_count=2.
#          First deactivate -> _install_count=1 (patch remains).
#          Second deactivate -> _install_count=0 (originals restored).
#   CHECK: After first deactivate, find is still patched. After second, it is original.
#   MUTATION: Restoring on first deactivate fails the mid-point identity check.
#   ESCAPE: Nothing reasonable -- sequential identity checks prove count-controlled restoration.
def test_reference_counting_nested() -> None:
    original_find = pymongo.collection.Collection.find
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert MongoPlugin._install_count == 2

    p.deactivate()
    assert MongoPlugin._install_count == 1
    assert pymongo.collection.Collection.find is not original_find

    p.deactivate()
    assert MongoPlugin._install_count == 0
    assert pymongo.collection.Collection.find is original_find


# ---------------------------------------------------------------------------
# Basic interception: find_one returns mocked value
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_operation_find_one_returns_value
#   CLAIM: mock_operation("find_one", returns={"_id": 1}) -> Collection.find_one() returns that doc.
#   PATH:  mock_operation -> appends MongoMockConfig to _queues["find_one"] ->
#          patched find_one() -> interceptor pops from queue -> returns doc.
#   CHECK: result == {"_id": 1, "name": "test"}.
#   MUTATION: Returning wrong value from config fails the equality check.
#   ESCAPE: Nothing reasonable -- exact dict equality.
def test_mock_operation_find_one_returns_value() -> None:
    v, p = _make_verifier_with_plugin()
    expected_doc = {"_id": 1, "name": "test"}
    p.mock_operation("find_one", returns=expected_doc)

    with v.sandbox():
        coll = _make_collection("mydb", "users")
        result = coll.find_one({"_id": 1})

    assert result == expected_doc


# ---------------------------------------------------------------------------
# FIFO ordering
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_operation_fifo_same_operation
#   CLAIM: Two mock_operation("find_one", ...) calls are consumed in FIFO order.
#   PATH:  mock_operation x2 -> two configs in deque for "find_one".
#          First find_one() -> popleft -> returns first. Second -> returns second.
#   CHECK: first_result == {"a": 1}; second_result == {"a": 2}.
#   MUTATION: Reversing FIFO order (LIFO) swaps the values; both checks fail.
#   ESCAPE: Nothing reasonable -- exact equality on distinct values.
def test_mock_operation_fifo_same_operation() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("find_one", returns={"a": 1})
    p.mock_operation("find_one", returns={"a": 2})

    with v.sandbox():
        coll = _make_collection()
        first_result = coll.find_one({"x": 1})
        second_result = coll.find_one({"x": 2})

    assert first_result == {"a": 1}
    assert second_result == {"a": 2}


# ---------------------------------------------------------------------------
# Different operations have separate queues
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_operation_separate_queues
#   CLAIM: mock_operation("find_one", ...) and mock_operation("insert_one", ...) use separate queues.
#   PATH:  "find_one" and "insert_one" are different keys in _queues dict.
#   CHECK: find_result == {"found": True}; insert_result == {"inserted": True}.
#   MUTATION: Single shared queue would fail the ordering/value checks.
#   ESCAPE: Nothing reasonable -- exact equality on distinct values from distinct queues.
def test_mock_operation_separate_queues() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("insert_one", returns=MagicMock(inserted_id="abc"))
    p.mock_operation("find_one", returns={"found": True})

    with v.sandbox():
        coll = _make_collection()
        insert_result = coll.insert_one({"doc": 1})
        find_result = coll.find_one({"doc": 1})

    assert insert_result.inserted_id == "abc"
    assert find_result == {"found": True}


# ---------------------------------------------------------------------------
# raises parameter
# ---------------------------------------------------------------------------


# ESCAPE: test_mock_operation_raises_exception
#   CLAIM: mock_operation("find_one", returns=None, raises=ValueError("bad")) raises on call.
#   PATH:  interceptor pops config with raises set -> raises config.raises.
#   CHECK: ValueError raised; str(exc) == "bad".
#   MUTATION: Not raising when config.raises is set returns None instead.
#   ESCAPE: Raising a different exception type fails the type check.
def test_mock_operation_raises_exception() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("find_one", returns=None, raises=ValueError("bad document"))

    with v.sandbox():
        coll = _make_collection()
        with pytest.raises(ValueError) as exc_info:
            coll.find_one({"_id": 1})

    assert str(exc_info.value) == "bad document"


# ---------------------------------------------------------------------------
# get_unused_mocks
# ---------------------------------------------------------------------------


# ESCAPE: test_get_unused_mocks_returns_unconsumed_required
#   CLAIM: get_unused_mocks() returns all MongoMockConfig with required=True still in queues.
#   PATH:  Two mock_operation("find_one") registered; only first consumed.
#   CHECK: len(unused) == 1; unused[0].operation == "find_one"; unused[0].returns == {"b": 2}.
#   MUTATION: Returning all configs (including consumed) fails the length check.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_get_unused_mocks_returns_unconsumed_required() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("find_one", returns={"b": 1})
    p.mock_operation("find_one", returns={"b": 2})

    with v.sandbox():
        coll = _make_collection()
        coll.find_one({"x": 1})

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].operation == "find_one"
    assert unused[0].returns == {"b": 2}


# ESCAPE: test_get_unused_mocks_excludes_required_false
#   CLAIM: get_unused_mocks() excludes configs with required=False even if unconsumed.
#   PATH:  mock_operation("find_one", ..., required=False) registered but never consumed.
#   CHECK: get_unused_mocks() == [].
#   MUTATION: Not filtering by required=False returns the config; list equality fails.
#   ESCAPE: Nothing reasonable -- exact equality with empty list.
def test_get_unused_mocks_excludes_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("find_one", returns={"x": 1}, required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# UnmockedInteractionError when no mock registered
# ---------------------------------------------------------------------------


# ESCAPE: test_unmocked_error_when_queue_empty
#   CLAIM: When a mongo operation fires with no mock registered, UnmockedInteractionError raised
#          with source_id == "mongo:find_one".
#   PATH:  interceptor -> _queues.get("find_one") is empty -> raise UnmockedInteractionError.
#   CHECK: UnmockedInteractionError raised; exc.source_id == "mongo:find_one".
#   MUTATION: Silently returning None instead of raising; no exception.
#   ESCAPE: Raising with wrong source_id fails equality.
def test_unmocked_error_when_queue_empty() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        coll = _make_collection()
        with pytest.raises(UnmockedInteractionError) as exc_info:
            coll.find_one({"_id": 1})

    assert exc_info.value.source_id == "mongo:find_one"


# ESCAPE: test_unmocked_error_after_queue_exhausted
#   CLAIM: After the only queued mock is consumed, a second call raises UnmockedInteractionError.
#   PATH:  First find_one pops the single mock; second call finds empty queue -> raises.
#   CHECK: UnmockedInteractionError raised on second call; source_id == "mongo:find_one".
#   MUTATION: Silently returning None or reusing mock fails either value or raise check.
#   ESCAPE: Nothing reasonable -- exact exception type and source_id.
def test_unmocked_error_after_queue_exhausted() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("find_one", returns={"x": 1})

    with v.sandbox():
        coll = _make_collection()
        first_result = coll.find_one({"k": 1})

        with pytest.raises(UnmockedInteractionError) as exc_info:
            coll.find_one({"k": 2})

    assert first_result == {"x": 1}
    assert exc_info.value.source_id == "mongo:find_one"


# ---------------------------------------------------------------------------
# assertable_fields: must return frozenset(interaction.details.keys())
# ---------------------------------------------------------------------------


# ESCAPE: test_assertable_fields_returns_all_detail_keys
#   CLAIM: assertable_fields() returns frozenset(interaction.details.keys()).
#   PATH:  BasePlugin.assertable_fields default implementation.
#   CHECK: result == frozenset({"database", "collection", "operation", "filter", "projection"})
#          for a find_one interaction.
#   MUTATION: Returning frozenset() skips completeness enforcement entirely.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_assertable_fields_returns_all_detail_keys() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mongo:find_one",
        sequence=0,
        details={
            "database": "testdb",
            "collection": "testcoll",
            "operation": "find_one",
            "filter": {"_id": 1},
            "projection": None,
        },
        plugin=p,
    )
    result = p.assertable_fields(interaction)
    assert result == frozenset({"database", "collection", "operation", "filter", "projection"})


# ---------------------------------------------------------------------------
# matches()
# ---------------------------------------------------------------------------


# ESCAPE: test_matches_field_comparison
#   CLAIM: matches() does field-by-field comparison; returns True when fields match, False otherwise.
#   PATH:  matches(interaction, expected) -> compare each expected key against details.
#   CHECK: Empty expected matches; non-matching field returns False; matching field True.
#   MUTATION: Returning True always fails the non-matching field check.
#   ESCAPE: Nothing reasonable -- exact boolean equality on distinct cases.
def test_matches_field_comparison() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mongo:find_one",
        sequence=0,
        details={
            "database": "testdb",
            "collection": "testcoll",
            "operation": "find_one",
            "filter": {"_id": 1},
            "projection": None,
        },
        plugin=p,
    )
    assert p.matches(interaction, {}) is True
    assert p.matches(interaction, {"operation": "find_one"}) is True
    assert p.matches(interaction, {"operation": "insert_one"}) is False
    assert p.matches(interaction, {"foo": "bar"}) is False


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


# ESCAPE: test_format_interaction
#   CLAIM: format_interaction returns a human-readable string for the given interaction.
#   PATH:  format_interaction(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Returning wrong format string fails equality check.
#   ESCAPE: Different order or missing fields fails equality.
def test_format_interaction() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mongo:find_one",
        sequence=0,
        details={
            "database": "testdb",
            "collection": "testcoll",
            "operation": "find_one",
            "filter": {"_id": 1},
            "projection": None,
        },
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[MongoPlugin] testdb.testcoll.find_one(filter={'_id': 1})"


# ESCAPE: test_format_interaction_insert_one
#   CLAIM: format_interaction for insert_one shows the document.
#   PATH:  format_interaction(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Returning wrong format fails equality.
#   ESCAPE: Different format fails equality.
def test_format_interaction_insert_one() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mongo:insert_one",
        sequence=0,
        details={
            "database": "testdb",
            "collection": "testcoll",
            "operation": "insert_one",
            "document": {"name": "Alice"},
        },
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[MongoPlugin] testdb.testcoll.insert_one(document={'name': 'Alice'})"


# ESCAPE: test_format_mock_hint
#   CLAIM: format_mock_hint returns copy-pasteable code to mock the interaction.
#   PATH:  format_mock_hint(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_mock_hint() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mongo:find_one",
        sequence=0,
        details={"operation": "find_one"},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.mongo_mock.mock_operation('find_one', returns=...)"


# ESCAPE: test_format_unmocked_hint
#   CLAIM: format_unmocked_hint returns copy-pasteable code for an unmocked call.
#   PATH:  format_unmocked_hint(source_id, args, kwargs) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("mongo:find_one", (), {})
    assert result == (
        "mongo.find_one(...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    bigfoot.mongo_mock.mock_operation('find_one', returns=...)"
    )


# ESCAPE: test_format_assert_hint
#   CLAIM: format_assert_hint returns assert_find_one() syntax with all fields.
#   PATH:  format_assert_hint(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_assert_hint() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mongo:find_one",
        sequence=0,
        details={
            "database": "testdb",
            "collection": "testcoll",
            "operation": "find_one",
            "filter": {"_id": 1},
            "projection": None,
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.mongo_mock.assert_find_one(\n"
        "        database='testdb',\n"
        "        collection='testcoll',\n"
        "        filter={'_id': 1},\n"
        "        projection=None,\n"
        "    )"
    )


# ESCAPE: test_format_assert_hint_insert_one
#   CLAIM: format_assert_hint for insert_one shows assert_insert_one syntax.
#   PATH:  format_assert_hint(interaction) -> string.
#   CHECK: result == exact expected string.
#   MUTATION: Wrong hint text fails equality check.
#   ESCAPE: Different format fails the equality check.
def test_format_assert_hint_insert_one() -> None:
    from bigfoot._timeline import Interaction

    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="mongo:insert_one",
        sequence=0,
        details={
            "database": "testdb",
            "collection": "testcoll",
            "operation": "insert_one",
            "document": {"name": "Alice"},
        },
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.mongo_mock.assert_insert_one(\n"
        "        database='testdb',\n"
        "        collection='testcoll',\n"
        "        document={'name': 'Alice'},\n"
        "    )"
    )


# ESCAPE: test_format_unused_mock_hint
#   CLAIM: format_unused_mock_hint returns hint containing operation name and traceback.
#   PATH:  format_unused_mock_hint(mock_config) -> string.
#   CHECK: result == exact expected prefix + registration_traceback.
#   MUTATION: Wrong prefix text fails the equality check.
#   ESCAPE: Not including registration_traceback fails equality.
def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = MongoMockConfig(operation="find_one", returns={"x": 1})
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "mongo.find_one(...) was mocked (required=True) but never called.\nRegistered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# Typed assertion helpers
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_find_typed_helper
#   CLAIM: assert_find() asserts the next find interaction with all required fields.
#   PATH:  assert_find() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping in assert_find raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_find_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("find", returns=[{"x": 1}])
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.find({"active": True}, {"name": 1})

    bigfoot.mongo_mock.assert_find(
        database="mydb",
        collection="users",
        filter={"active": True},
        projection={"name": 1},
    )


# ESCAPE: test_assert_find_one_typed_helper
#   CLAIM: assert_find_one() asserts the next find_one interaction with all required fields.
#   PATH:  assert_find_one() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable.
def test_assert_find_one_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("find_one", returns={"_id": 1})
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.find_one({"_id": 1}, {"name": 1})

    bigfoot.mongo_mock.assert_find_one(
        database="mydb",
        collection="users",
        filter={"_id": 1},
        projection={"name": 1},
    )


# ESCAPE: test_assert_insert_one_typed_helper
#   CLAIM: assert_insert_one() asserts the next insert_one interaction.
#   PATH:  assert_insert_one() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable.
def test_assert_insert_one_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("insert_one", returns=MagicMock(inserted_id="abc"))
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.insert_one({"name": "Alice"})

    bigfoot.mongo_mock.assert_insert_one(
        database="mydb",
        collection="users",
        document={"name": "Alice"},
    )


# ESCAPE: test_assert_update_one_typed_helper
#   CLAIM: assert_update_one() asserts the next update_one interaction.
#   PATH:  assert_update_one() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable.
def test_assert_update_one_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("update_one", returns=MagicMock(modified_count=1))
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.update_one({"_id": 1}, {"$set": {"name": "Bob"}})

    bigfoot.mongo_mock.assert_update_one(
        database="mydb",
        collection="users",
        filter={"_id": 1},
        update={"$set": {"name": "Bob"}},
    )


# ESCAPE: test_assert_delete_one_typed_helper
#   CLAIM: assert_delete_one() asserts the next delete_one interaction.
#   PATH:  assert_delete_one() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable.
def test_assert_delete_one_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("delete_one", returns=MagicMock(deleted_count=1))
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.delete_one({"_id": 1})

    bigfoot.mongo_mock.assert_delete_one(
        database="mydb",
        collection="users",
        filter={"_id": 1},
    )


# ESCAPE: test_assert_aggregate_typed_helper
#   CLAIM: assert_aggregate() asserts the next aggregate interaction.
#   PATH:  assert_aggregate() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable.
def test_assert_aggregate_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    pipeline = [{"$match": {"active": True}}, {"$group": {"_id": "$type"}}]
    bigfoot.mongo_mock.mock_operation("aggregate", returns=[{"_id": "A"}])
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.aggregate(pipeline)

    bigfoot.mongo_mock.assert_aggregate(
        database="mydb",
        collection="users",
        pipeline=pipeline,
    )


# ESCAPE: test_assert_insert_many_typed_helper
#   CLAIM: assert_insert_many() asserts the next insert_many interaction with all required fields.
#   PATH:  assert_insert_many() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping in assert_insert_many raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_insert_many_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("insert_many", returns=MagicMock(inserted_ids=["a", "b"]))
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "items")
        coll.insert_many([{"x": 1}, {"x": 2}])

    bigfoot.mongo_mock.assert_insert_many(
        database="mydb",
        collection="items",
        documents=[{"x": 1}, {"x": 2}],
    )


# ESCAPE: test_assert_update_many_typed_helper
#   CLAIM: assert_update_many() asserts the next update_many interaction with all required fields.
#   PATH:  assert_update_many() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping in assert_update_many raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_update_many_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("update_many", returns=MagicMock(modified_count=5))
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "items")
        coll.update_many({"status": "old"}, {"$set": {"status": "new"}})

    bigfoot.mongo_mock.assert_update_many(
        database="mydb",
        collection="items",
        filter={"status": "old"},
        update={"$set": {"status": "new"}},
    )


# ESCAPE: test_assert_delete_many_typed_helper
#   CLAIM: assert_delete_many() asserts the next delete_many interaction with all required fields.
#   PATH:  assert_delete_many() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping in assert_delete_many raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_delete_many_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("delete_many", returns=MagicMock(deleted_count=3))
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "items")
        coll.delete_many({"status": "old"})

    bigfoot.mongo_mock.assert_delete_many(
        database="mydb",
        collection="items",
        filter={"status": "old"},
    )


# ESCAPE: test_assert_count_documents_typed_helper
#   CLAIM: assert_count_documents() asserts the next count_documents interaction with all required fields.
#   PATH:  assert_count_documents() -> builds expected dict -> calls verifier.assert_interaction().
#   CHECK: No error raised when fields match.
#   MUTATION: Wrong field mapping in assert_count_documents raises InteractionMismatchError.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_assert_count_documents_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("count_documents", returns=42)
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "items")
        coll.count_documents({"active": True})

    bigfoot.mongo_mock.assert_count_documents(
        database="mydb",
        collection="items",
        filter={"active": True},
    )


# ---------------------------------------------------------------------------
# Typed helper with wrong args raises InteractionMismatchError
# ---------------------------------------------------------------------------


# ESCAPE: test_assert_find_one_wrong_filter_raises
#   CLAIM: assert_find_one() with wrong filter raises InteractionMismatchError.
#   PATH:  assert_find_one() -> mismatch detected -> InteractionMismatchError.
#   CHECK: InteractionMismatchError raised.
#   MUTATION: Not checking fields means no error raised.
#   ESCAPE: Nothing reasonable -- exception proves mismatch detection.
def test_assert_find_one_wrong_filter_raises(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("find_one", returns={"x": 1})
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.find_one({"_id": 1})

    with pytest.raises(InteractionMismatchError):
        bigfoot.mongo_mock.assert_find_one(
            database="mydb",
            collection="users",
            filter={"_id": 999},
            projection=None,
        )
    # Now assert correctly so teardown passes
    bigfoot.mongo_mock.assert_find_one(
        database="mydb",
        collection="users",
        filter={"_id": 1},
        projection=None,
    )


# ---------------------------------------------------------------------------
# Interactions not auto-asserted
# ---------------------------------------------------------------------------


# ESCAPE: test_mongo_interactions_not_auto_asserted
#   CLAIM: Mongo interactions are NOT auto-asserted; they land on timeline unasserted.
#   PATH:  Patched method -> record() called -> no mark_asserted().
#   CHECK: timeline.all_unasserted() contains the interaction.
#   MUTATION: Auto-asserting in the interceptor means all_unasserted() would be empty.
#   ESCAPE: Nothing reasonable -- exact check on unasserted list.
def test_mongo_interactions_not_auto_asserted(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("find_one", returns={"x": 1})
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.find_one({"_id": 1})

    timeline = bigfoot_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "mongo:find_one"
    # Assert it so verify_all() at teardown succeeds
    bigfoot.mongo_mock.assert_find_one(
        database="mydb",
        collection="users",
        filter={"_id": 1},
        projection=None,
    )


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.mongo_mock
# ---------------------------------------------------------------------------


# ESCAPE: test_mongo_mock_proxy_mock_operation
#   CLAIM: bigfoot.mongo_mock.mock_operation("find_one", returns=...) works when verifier active.
#   PATH:  _MongoProxy.__getattr__("mock_operation") -> get verifier ->
#          find/create MongoPlugin -> return plugin.mock_operation.
#   CHECK: The proxy call does not raise and the mock is registered.
#   MUTATION: Returning None instead of the plugin fails with AttributeError.
#   ESCAPE: Nothing reasonable -- call succeeds or raises.
def test_mongo_mock_proxy_mock_operation(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("find_one", returns={"proxy": True})
    with bigfoot.sandbox():
        coll = _make_collection("proxydb", "proxycoll")
        result = coll.find_one({"k": 1})

    assert result == {"proxy": True}
    bigfoot.mongo_mock.assert_find_one(
        database="proxydb",
        collection="proxycoll",
        filter={"k": 1},
        projection=None,
    )


# ESCAPE: test_mongo_mock_proxy_raises_outside_context
#   CLAIM: Accessing bigfoot.mongo_mock outside a test context raises NoActiveVerifierError.
#   PATH:  _MongoProxy.__getattr__ -> _get_test_verifier_or_raise -> NoActiveVerifierError.
#   CHECK: NoActiveVerifierError raised.
#   MUTATION: Silently returning None would not raise.
#   ESCAPE: Nothing reasonable -- exact exception type.
def test_mongo_mock_proxy_raises_outside_context() -> None:
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.mongo_mock.mock_operation
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# MongoPlugin in __all__
# ---------------------------------------------------------------------------


# ESCAPE: test_mongo_plugin_in_all
#   CLAIM: MongoPlugin and mongo_mock are exported from bigfoot.__all__.
#   PATH:  bigfoot.__all__ contains "MongoPlugin" and "mongo_mock".
#   CHECK: "MongoPlugin" in bigfoot.__all__; "mongo_mock" in bigfoot.__all__.
#   MUTATION: Omitting either from __all__ fails the membership check.
#   ESCAPE: Nothing reasonable -- exact membership check.
def test_mongo_plugin_in_all() -> None:
    import bigfoot

    assert "MongoPlugin" in bigfoot.__all__
    assert "mongo_mock" in bigfoot.__all__


# ---------------------------------------------------------------------------
# MissingAssertionFieldsError when fields omitted
# ---------------------------------------------------------------------------


# ESCAPE: test_missing_fields_raises_error
#   CLAIM: Asserting with incomplete fields raises MissingAssertionFieldsError.
#   PATH:  assert_interaction() checks assertable_fields() -> finds missing -> raises.
#   CHECK: MissingAssertionFieldsError raised with correct missing_fields.
#   MUTATION: Returning frozenset() from assertable_fields would never raise.
#   ESCAPE: Nothing reasonable -- exact exception type and field check.
def test_missing_fields_raises_error(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot
    from bigfoot.plugins.mongo_plugin import _MongoSentinel

    bigfoot.mongo_mock.mock_operation("find_one", returns={"x": 1})
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.find_one({"_id": 1})

    sentinel = _MongoSentinel("mongo:find_one")
    with pytest.raises(MissingAssertionFieldsError) as exc_info:
        bigfoot_verifier.assert_interaction(
            sentinel,
            database="mydb",
            # Missing: collection, operation, filter, projection
        )
    assert "collection" in exc_info.value.missing_fields
    assert "operation" in exc_info.value.missing_fields
    assert "filter" in exc_info.value.missing_fields

    # Now assert correctly so teardown passes
    bigfoot.mongo_mock.assert_find_one(
        database="mydb",
        collection="users",
        filter={"_id": 1},
        projection=None,
    )


# ---------------------------------------------------------------------------
# All intercepted operations work
# ---------------------------------------------------------------------------


# ESCAPE: test_find_interception
#   CLAIM: Collection.find() is intercepted and returns mocked value.
#   PATH:  patched find -> interceptor -> queue lookup -> return.
#   CHECK: result == expected list.
#   MUTATION: find not being patched means original pymongo code runs and fails.
#   ESCAPE: Nothing reasonable -- exact equality.
def test_find_interception() -> None:
    v, p = _make_verifier_with_plugin()
    expected = [{"_id": 1}, {"_id": 2}]
    p.mock_operation("find", returns=expected)

    with v.sandbox():
        coll = _make_collection("db1", "c1")
        result = coll.find({"active": True}, {"_id": 1})

    assert result == expected


# ESCAPE: test_insert_many_interception
#   CLAIM: Collection.insert_many() is intercepted and returns mocked value.
#   PATH:  patched insert_many -> interceptor -> queue lookup -> return.
#   CHECK: result.inserted_ids == ["a", "b"].
#   MUTATION: insert_many not being patched means original pymongo code runs and fails.
#   ESCAPE: Nothing reasonable.
def test_insert_many_interception() -> None:
    v, p = _make_verifier_with_plugin()
    mock_result = MagicMock(inserted_ids=["a", "b"])
    p.mock_operation("insert_many", returns=mock_result)

    with v.sandbox():
        coll = _make_collection("db1", "c1")
        result = coll.insert_many([{"x": 1}, {"x": 2}])

    assert result.inserted_ids == ["a", "b"]


# ESCAPE: test_update_many_interception
#   CLAIM: Collection.update_many() is intercepted and returns mocked value.
#   PATH:  patched update_many -> interceptor -> queue lookup -> return.
#   CHECK: result.modified_count == 5.
#   MUTATION: update_many not being patched means original pymongo code runs and fails.
#   ESCAPE: Nothing reasonable.
def test_update_many_interception() -> None:
    v, p = _make_verifier_with_plugin()
    mock_result = MagicMock(modified_count=5)
    p.mock_operation("update_many", returns=mock_result)

    with v.sandbox():
        coll = _make_collection("db1", "c1")
        result = coll.update_many({"status": "old"}, {"$set": {"status": "new"}})

    assert result.modified_count == 5


# ESCAPE: test_delete_many_interception
#   CLAIM: Collection.delete_many() is intercepted and returns mocked value.
#   PATH:  patched delete_many -> interceptor -> queue lookup -> return.
#   CHECK: result.deleted_count == 3.
#   MUTATION: delete_many not being patched means original pymongo code runs and fails.
#   ESCAPE: Nothing reasonable.
def test_delete_many_interception() -> None:
    v, p = _make_verifier_with_plugin()
    mock_result = MagicMock(deleted_count=3)
    p.mock_operation("delete_many", returns=mock_result)

    with v.sandbox():
        coll = _make_collection("db1", "c1")
        result = coll.delete_many({"status": "old"})

    assert result.deleted_count == 3


# ESCAPE: test_count_documents_interception
#   CLAIM: Collection.count_documents() is intercepted and returns mocked value.
#   PATH:  patched count_documents -> interceptor -> queue lookup -> return.
#   CHECK: result == 42.
#   MUTATION: count_documents not being patched means original pymongo code runs and fails.
#   ESCAPE: Nothing reasonable -- exact integer equality.
def test_count_documents_interception() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("count_documents", returns=42)

    with v.sandbox():
        coll = _make_collection("db1", "c1")
        result = coll.count_documents({"active": True})

    assert result == 42


# ESCAPE: test_aggregate_interception
#   CLAIM: Collection.aggregate() is intercepted and returns mocked value.
#   PATH:  patched aggregate -> interceptor -> queue lookup -> return.
#   CHECK: result == expected list.
#   MUTATION: aggregate not being patched means original pymongo code runs and fails.
#   ESCAPE: Nothing reasonable.
def test_aggregate_interception() -> None:
    v, p = _make_verifier_with_plugin()
    expected = [{"_id": "A", "count": 10}]
    p.mock_operation("aggregate", returns=expected)

    with v.sandbox():
        coll = _make_collection("db1", "c1")
        result = coll.aggregate([{"$group": {"_id": "$type", "count": {"$sum": 1}}}])

    assert result == expected


# ---------------------------------------------------------------------------
# Interaction details per operation type
# ---------------------------------------------------------------------------


# ESCAPE: test_find_one_records_correct_details
#   CLAIM: find_one records database, collection, operation, filter, projection in details.
#   PATH:  patched find_one -> interceptor records interaction with correct details.
#   CHECK: All detail fields match expected values.
#   MUTATION: Missing a field in details fails the assertable_fields check at assertion time.
#   ESCAPE: Nothing reasonable -- typed helper covers all fields.
def test_find_one_records_correct_details(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("find_one", returns={"x": 1})
    with bigfoot.sandbox():
        coll = _make_collection("recdb", "reccoll")
        coll.find_one({"_id": 1}, {"name": 1})

    # This asserts ALL required fields
    bigfoot.mongo_mock.assert_find_one(
        database="recdb",
        collection="reccoll",
        filter={"_id": 1},
        projection={"name": 1},
    )


# ESCAPE: test_insert_one_records_correct_details
#   CLAIM: insert_one records database, collection, operation, document in details.
#   PATH:  patched insert_one -> interceptor records interaction with correct details.
#   CHECK: Typed helper asserts all fields.
#   MUTATION: Missing a field fails assertable_fields check.
#   ESCAPE: Nothing reasonable.
def test_insert_one_records_correct_details(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("insert_one", returns=MagicMock(inserted_id="abc"))
    with bigfoot.sandbox():
        coll = _make_collection("recdb", "reccoll")
        coll.insert_one({"name": "Alice", "age": 30})

    bigfoot.mongo_mock.assert_insert_one(
        database="recdb",
        collection="reccoll",
        document={"name": "Alice", "age": 30},
    )


# ESCAPE: test_update_one_records_correct_details
#   CLAIM: update_one records database, collection, operation, filter, update in details.
#   PATH:  patched update_one -> interceptor records interaction with correct details.
#   CHECK: Typed helper asserts all fields.
#   MUTATION: Missing a field fails assertable_fields check.
#   ESCAPE: Nothing reasonable.
def test_update_one_records_correct_details(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("update_one", returns=MagicMock(modified_count=1))
    with bigfoot.sandbox():
        coll = _make_collection("recdb", "reccoll")
        coll.update_one({"_id": 1}, {"$set": {"name": "Bob"}})

    bigfoot.mongo_mock.assert_update_one(
        database="recdb",
        collection="reccoll",
        filter={"_id": 1},
        update={"$set": {"name": "Bob"}},
    )


# ESCAPE: test_delete_one_records_correct_details
#   CLAIM: delete_one records database, collection, operation, filter in details.
#   PATH:  patched delete_one -> interceptor records interaction with correct details.
#   CHECK: Typed helper asserts all fields.
#   MUTATION: Missing a field fails assertable_fields check.
#   ESCAPE: Nothing reasonable.
def test_delete_one_records_correct_details(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("delete_one", returns=MagicMock(deleted_count=1))
    with bigfoot.sandbox():
        coll = _make_collection("recdb", "reccoll")
        coll.delete_one({"_id": 1})

    bigfoot.mongo_mock.assert_delete_one(
        database="recdb",
        collection="reccoll",
        filter={"_id": 1},
    )


# ESCAPE: test_aggregate_records_correct_details
#   CLAIM: aggregate records database, collection, operation, pipeline in details.
#   PATH:  patched aggregate -> interceptor records interaction with correct details.
#   CHECK: Typed helper asserts all fields.
#   MUTATION: Missing a field fails assertable_fields check.
#   ESCAPE: Nothing reasonable.
def test_aggregate_records_correct_details(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    pipeline = [{"$match": {"active": True}}]
    bigfoot.mongo_mock.mock_operation("aggregate", returns=[])
    with bigfoot.sandbox():
        coll = _make_collection("recdb", "reccoll")
        coll.aggregate(pipeline)

    bigfoot.mongo_mock.assert_aggregate(
        database="recdb",
        collection="reccoll",
        pipeline=pipeline,
    )


# ESCAPE: test_count_documents_records_correct_details
#   CLAIM: count_documents records database, collection, operation, filter in details.
#   PATH:  patched count_documents -> interceptor records interaction with correct details.
#   CHECK: All detail fields verified via assert_interaction.
#   MUTATION: Missing a field fails assertable_fields check.
#   ESCAPE: Nothing reasonable.
def test_count_documents_records_correct_details(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot
    from bigfoot.plugins.mongo_plugin import _MongoSentinel

    bigfoot.mongo_mock.mock_operation("count_documents", returns=42)
    with bigfoot.sandbox():
        coll = _make_collection("recdb", "reccoll")
        coll.count_documents({"active": True})

    sentinel = _MongoSentinel("mongo:count_documents")
    bigfoot_verifier.assert_interaction(
        sentinel,
        database="recdb",
        collection="reccoll",
        operation="count_documents",
        filter={"active": True},
    )


# ESCAPE: test_insert_many_records_correct_details
#   CLAIM: insert_many records database, collection, operation, documents in details.
#   PATH:  patched insert_many -> interceptor records interaction with correct details.
#   CHECK: Typed helper asserts all fields.
#   MUTATION: Missing a field fails assertable_fields check.
#   ESCAPE: Nothing reasonable.
def test_insert_many_records_correct_details(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    docs = [{"x": 1}, {"x": 2}]
    bigfoot.mongo_mock.mock_operation("insert_many", returns=MagicMock(inserted_ids=["a", "b"]))
    with bigfoot.sandbox():
        coll = _make_collection("recdb", "reccoll")
        coll.insert_many(docs)

    bigfoot.mongo_mock.assert_insert_many(
        database="recdb",
        collection="reccoll",
        documents=docs,
    )


# ESCAPE: test_update_many_records_correct_details
#   CLAIM: update_many records database, collection, operation, filter, update in details.
#   PATH:  patched update_many -> interceptor records interaction with correct details.
#   CHECK: Typed helper asserts all fields.
#   MUTATION: Missing a field fails assertable_fields check.
#   ESCAPE: Nothing reasonable.
def test_update_many_records_correct_details(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("update_many", returns=MagicMock(modified_count=5))
    with bigfoot.sandbox():
        coll = _make_collection("recdb", "reccoll")
        coll.update_many({"status": "old"}, {"$set": {"status": "new"}})

    bigfoot.mongo_mock.assert_update_many(
        database="recdb",
        collection="reccoll",
        filter={"status": "old"},
        update={"$set": {"status": "new"}},
    )


# ESCAPE: test_delete_many_records_correct_details
#   CLAIM: delete_many records database, collection, operation, filter in details.
#   PATH:  patched delete_many -> interceptor records interaction with correct details.
#   CHECK: Typed helper asserts all fields.
#   MUTATION: Missing a field fails assertable_fields check.
#   ESCAPE: Nothing reasonable.
def test_delete_many_records_correct_details(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("delete_many", returns=MagicMock(deleted_count=3))
    with bigfoot.sandbox():
        coll = _make_collection("recdb", "reccoll")
        coll.delete_many({"status": "old"})

    bigfoot.mongo_mock.assert_delete_many(
        database="recdb",
        collection="reccoll",
        filter={"status": "old"},
    )


# ---------------------------------------------------------------------------
# kwargs fallback paths in _extract_details
# ---------------------------------------------------------------------------


# ESCAPE: test_find_one_kwargs_extraction
#   CLAIM: find_one with keyword arguments records correct details via kwargs fallback.
#   PATH:  patched find_one -> _extract_details kwargs.get() paths -> correct details.
#   CHECK: Typed helper asserts all fields match kwargs values.
#   MUTATION: Broken kwargs.get() records None instead of actual values.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_find_one_kwargs_extraction(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("find_one", returns={"x": 1})
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.find_one(filter={"_id": 1}, projection={"name": 1})

    bigfoot.mongo_mock.assert_find_one(
        database="mydb",
        collection="users",
        filter={"_id": 1},
        projection={"name": 1},
    )


# ESCAPE: test_insert_one_kwargs_extraction
#   CLAIM: insert_one with keyword arguments records correct details via kwargs fallback.
#   PATH:  patched insert_one -> _extract_details kwargs.get("document") -> correct details.
#   CHECK: Typed helper asserts all fields match kwargs values.
#   MUTATION: Broken kwargs.get() records None instead of actual document.
#   ESCAPE: Nothing reasonable -- exact field matching.
def test_insert_one_kwargs_extraction(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.mongo_mock.mock_operation("insert_one", returns=MagicMock(inserted_id="abc"))
    with bigfoot.sandbox():
        coll = _make_collection("mydb", "users")
        coll.insert_one(document={"name": "Alice"})

    bigfoot.mongo_mock.assert_insert_one(
        database="mydb",
        collection="users",
        document={"name": "Alice"},
    )
