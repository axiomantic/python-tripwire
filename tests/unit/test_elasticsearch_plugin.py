"""Unit tests for ElasticsearchPlugin."""

from __future__ import annotations

import elasticsearch
import pytest

from bigfoot._context import _current_test_verifier
from bigfoot._errors import (
    InteractionMismatchError,
    MissingAssertionFieldsError,
    UnmockedInteractionError,
)
from bigfoot._timeline import Interaction
from bigfoot._verifier import StrictVerifier
from bigfoot.plugins.elasticsearch_plugin import (
    _ELASTICSEARCH_AVAILABLE,
    ElasticsearchMockConfig,
    ElasticsearchPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verifier_with_plugin() -> tuple[StrictVerifier, ElasticsearchPlugin]:
    v = StrictVerifier()
    for p in v._plugins:
        if isinstance(p, ElasticsearchPlugin):
            return v, p
    p = ElasticsearchPlugin(v)
    return v, p


def _reset_plugin_count() -> None:
    with ElasticsearchPlugin._install_lock:
        ElasticsearchPlugin._install_count = 0
        # Use the plugin's own _restore_patches() to avoid duplicating restoration logic.
        ElasticsearchPlugin.__new__(ElasticsearchPlugin).restore_patches()


@pytest.fixture(autouse=True)
def clean_plugin_counts() -> None:
    _reset_plugin_count()
    yield
    _reset_plugin_count()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_elasticsearch_available_flag() -> None:
    assert _ELASTICSEARCH_AVAILABLE is True


def test_activate_raises_when_elasticsearch_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import bigfoot.plugins.elasticsearch_plugin as _ep

    v, p = _make_verifier_with_plugin()
    monkeypatch.setattr(_ep, "_ELASTICSEARCH_AVAILABLE", False)
    with pytest.raises(ImportError) as exc_info:
        p.activate()
    assert str(exc_info.value) == (
        "Install bigfoot[elasticsearch] to use ElasticsearchPlugin: "
        "pip install bigfoot[elasticsearch]"
    )


# ---------------------------------------------------------------------------
# ElasticsearchMockConfig dataclass
# ---------------------------------------------------------------------------


def test_elasticsearch_mock_config_fields() -> None:
    config = ElasticsearchMockConfig(
        operation="index", returns={"result": "created"}, raises=None, required=False
    )
    assert config.operation == "index"
    assert config.returns == {"result": "created"}
    assert config.raises is None
    assert config.required is False
    lines = config.registration_traceback.splitlines()
    assert lines[0].startswith("  File ")


def test_elasticsearch_mock_config_defaults() -> None:
    config = ElasticsearchMockConfig(operation="search", returns={"hits": {"hits": []}})
    assert config.raises is None
    assert config.required is True


# ---------------------------------------------------------------------------
# Activation and reference counting
# ---------------------------------------------------------------------------


def test_activate_installs_patches() -> None:
    original_index = elasticsearch.Elasticsearch.index
    v, p = _make_verifier_with_plugin()
    p.activate()
    assert elasticsearch.Elasticsearch.index is not original_index
    p.deactivate()


def test_deactivate_restores_patches() -> None:
    original_index = elasticsearch.Elasticsearch.index
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.deactivate()
    assert elasticsearch.Elasticsearch.index is original_index


def test_reference_counting_nested() -> None:
    original_index = elasticsearch.Elasticsearch.index
    v, p = _make_verifier_with_plugin()
    p.activate()
    p.activate()
    assert ElasticsearchPlugin._install_count == 2

    p.deactivate()
    assert ElasticsearchPlugin._install_count == 1
    assert elasticsearch.Elasticsearch.index is not original_index

    p.deactivate()
    assert ElasticsearchPlugin._install_count == 0
    assert elasticsearch.Elasticsearch.index is original_index


# ---------------------------------------------------------------------------
# Basic interception: mock_operation returns value
# ---------------------------------------------------------------------------


def test_mock_operation_index_returns_value() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("index", returns={"result": "created", "_id": "1"})

    with v.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        result = es.index(index="my-index", document={"field": "value"})

    assert result == {"result": "created", "_id": "1"}


def test_mock_operation_search_returns_value() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("search", returns={"hits": {"hits": [{"_source": {"field": "val"}}]}})

    with v.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        result = es.search(index="my-index", query={"match_all": {}})

    assert result == {"hits": {"hits": [{"_source": {"field": "val"}}]}}


# ---------------------------------------------------------------------------
# FIFO ordering
# ---------------------------------------------------------------------------


def test_mock_operation_fifo() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("index", returns={"_id": "1"})
    p.mock_operation("index", returns={"_id": "2"})

    with v.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        first = es.index(index="idx", document={"a": 1})
        second = es.index(index="idx", document={"b": 2})

    assert first == {"_id": "1"}
    assert second == {"_id": "2"}


# ---------------------------------------------------------------------------
# Separate queues per operation
# ---------------------------------------------------------------------------


def test_mock_operation_separate_queues() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("index", returns={"_id": "1"})
    p.mock_operation("get", returns={"_source": {"field": "val"}})

    with v.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        index_result = es.index(index="idx", document={"a": 1})
        get_result = es.get(index="idx", id="1")

    assert index_result == {"_id": "1"}
    assert get_result == {"_source": {"field": "val"}}


# ---------------------------------------------------------------------------
# raises parameter
# ---------------------------------------------------------------------------


def test_mock_operation_raises_exception() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("index", returns=None, raises=ValueError("index failed"))

    with v.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        with pytest.raises(ValueError, match="index failed"):
            es.index(index="idx", document={"a": 1})


# ---------------------------------------------------------------------------
# get_unused_mocks
# ---------------------------------------------------------------------------


def test_get_unused_mocks_returns_unconsumed_required() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("index", returns={"_id": "1"})
    p.mock_operation("index", returns={"_id": "2"})

    with v.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        es.index(index="idx", document={"a": 1})

    unused = p.get_unused_mocks()
    assert len(unused) == 1
    assert unused[0].operation == "index"
    assert unused[0].returns == {"_id": "2"}


def test_get_unused_mocks_excludes_required_false() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("index", returns={}, required=False)

    unused = p.get_unused_mocks()
    assert unused == []


# ---------------------------------------------------------------------------
# UnmockedInteractionError
# ---------------------------------------------------------------------------


def test_unmocked_error_when_queue_empty() -> None:
    v, p = _make_verifier_with_plugin()

    with v.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        with pytest.raises(UnmockedInteractionError) as exc_info:
            es.index(index="idx", document={"a": 1})

    assert exc_info.value.source_id == "elasticsearch:index"


def test_unmocked_error_after_queue_exhausted() -> None:
    v, p = _make_verifier_with_plugin()
    p.mock_operation("get", returns={"_source": {"a": 1}})

    with v.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        first = es.get(index="idx", id="1")

        with pytest.raises(UnmockedInteractionError) as exc_info:
            es.get(index="idx", id="2")

    assert first == {"_source": {"a": 1}}
    assert exc_info.value.source_id == "elasticsearch:get"


# ---------------------------------------------------------------------------
# matches() and assertable_fields()
# ---------------------------------------------------------------------------


def test_matches_field_comparison() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="elasticsearch:index",
        sequence=0,
        details={"index": "my-index", "document": {"field": "val"}, "id": None},
        plugin=p,
    )
    assert p.matches(interaction, {}) is True
    assert p.matches(interaction, {"index": "my-index"}) is True
    assert p.matches(interaction, {"index": "wrong"}) is False


def test_assertable_fields_only_provided_keys() -> None:
    """Only kwargs actually provided are stored in details and assertable."""
    v, p = _make_verifier_with_plugin()
    # Simulate an interaction where only index and document were provided (no id)
    interaction = Interaction(
        source_id="elasticsearch:index",
        sequence=0,
        details={"index": "my-index", "document": {"a": 1}},
        plugin=p,
    )
    fields = p.assertable_fields(interaction)
    assert fields == frozenset({"index", "document"})


def test_assertable_fields_all_keys() -> None:
    """When all detail keys are provided, all are assertable."""
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="elasticsearch:index",
        sequence=0,
        details={"index": "my-index", "document": {"a": 1}, "id": "doc-1"},
        plugin=p,
    )
    fields = p.assertable_fields(interaction)
    assert fields == frozenset({"index", "document", "id"})


# ---------------------------------------------------------------------------
# format_* methods
# ---------------------------------------------------------------------------


def test_format_interaction() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="elasticsearch:index",
        sequence=0,
        details={"index": "my-index", "document": {"a": 1}, "id": None},
        plugin=p,
    )
    result = p.format_interaction(interaction)
    assert result == "[ElasticsearchPlugin] elasticsearch.index(index='my-index')"


def test_format_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="elasticsearch:search",
        sequence=0,
        details={"index": "my-index", "query": {"match_all": {}}, "size": None, "from_": None},
        plugin=p,
    )
    result = p.format_mock_hint(interaction)
    assert result == "    bigfoot.elasticsearch_mock.mock_operation('search', returns=...)"


def test_format_unmocked_hint() -> None:
    v, p = _make_verifier_with_plugin()
    result = p.format_unmocked_hint("elasticsearch:index", (), {})
    assert result == (
        "elasticsearch.index(...) was called but no mock was registered.\n"
        "Register a mock with:\n"
        "    bigfoot.elasticsearch_mock.mock_operation('index', returns=...)"
    )


def test_format_assert_hint() -> None:
    v, p = _make_verifier_with_plugin()
    interaction = Interaction(
        source_id="elasticsearch:index",
        sequence=0,
        details={"index": "my-index", "document": {"a": 1}, "id": None},
        plugin=p,
    )
    result = p.format_assert_hint(interaction)
    assert result == (
        "    bigfoot.elasticsearch_mock.assert_index(\n"
        "        index='my-index',\n"
        "        document={'a': 1},\n"
        "    )"
    )


def test_format_unused_mock_hint() -> None:
    v, p = _make_verifier_with_plugin()
    config = ElasticsearchMockConfig(operation="index", returns={})
    result = p.format_unused_mock_hint(config)
    expected_prefix = (
        "elasticsearch.index(...) was mocked (required=True) but never called.\nRegistered at:\n"
    )
    assert result == expected_prefix + config.registration_traceback


# ---------------------------------------------------------------------------
# Module-level proxy: bigfoot.elasticsearch_mock
# ---------------------------------------------------------------------------


def test_elasticsearch_mock_proxy_mock_operation(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.elasticsearch_mock.mock_operation("index", returns={"_id": "1"})

    with bigfoot.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        result = es.index(index="my-index", document={"field": "val"})

    assert result == {"_id": "1"}
    bigfoot.elasticsearch_mock.assert_index(index="my-index", document={"field": "val"})


def test_elasticsearch_mock_proxy_raises_outside_context() -> None:
    import bigfoot
    from bigfoot._errors import NoActiveVerifierError

    token = _current_test_verifier.set(None)
    try:
        with pytest.raises(NoActiveVerifierError):
            _ = bigfoot.elasticsearch_mock.mock_operation
    finally:
        _current_test_verifier.reset(token)


# ---------------------------------------------------------------------------
# ElasticsearchPlugin in __all__
# ---------------------------------------------------------------------------


def test_elasticsearch_plugin_in_all() -> None:
    import bigfoot
    from bigfoot.plugins.elasticsearch_plugin import (
        ElasticsearchPlugin as _ElasticsearchPlugin,
    )

    assert bigfoot.ElasticsearchPlugin is _ElasticsearchPlugin
    assert type(bigfoot.elasticsearch_mock).__name__ == "_ElasticsearchProxy"


# ---------------------------------------------------------------------------
# No auto-assert, typed assertion helpers
# ---------------------------------------------------------------------------


def test_elasticsearch_interactions_not_auto_asserted(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.elasticsearch_mock.mock_operation("index", returns={"_id": "1"})
    with bigfoot.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        es.index(index="idx", document={"a": 1})

    timeline = bigfoot_verifier._timeline
    interactions = timeline.all_unasserted()
    assert len(interactions) == 1
    assert interactions[0].source_id == "elasticsearch:index"
    bigfoot.elasticsearch_mock.assert_index(index="idx", document={"a": 1})


def test_assert_index_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.elasticsearch_mock.mock_operation("index", returns={"_id": "1"})
    with bigfoot.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        es.index(index="idx", document={"a": 1})
    bigfoot.elasticsearch_mock.assert_index(index="idx", document={"a": 1})


def test_assert_search_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.elasticsearch_mock.mock_operation("search", returns={"hits": {"hits": []}})
    with bigfoot.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        es.search(index="idx", query={"match_all": {}})
    bigfoot.elasticsearch_mock.assert_search(index="idx", query={"match_all": {}})


def test_assert_get_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.elasticsearch_mock.mock_operation("get", returns={"_source": {"a": 1}})
    with bigfoot.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        es.get(index="idx", id="1")
    bigfoot.elasticsearch_mock.assert_get(index="idx", id="1")


def test_assert_delete_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.elasticsearch_mock.mock_operation("delete", returns={"result": "deleted"})
    with bigfoot.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        es.delete(index="idx", id="1")
    bigfoot.elasticsearch_mock.assert_delete(index="idx", id="1")


def test_assert_bulk_typed_helper(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    ops = [{"index": {"_index": "idx", "_id": "1"}}, {"field": "val"}]
    bigfoot.elasticsearch_mock.mock_operation("bulk", returns={"items": []})
    with bigfoot.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        es.bulk(operations=ops)
    bigfoot.elasticsearch_mock.assert_bulk(operations=ops)


def test_assert_index_wrong_params_raises(bigfoot_verifier: StrictVerifier) -> None:
    import bigfoot

    bigfoot.elasticsearch_mock.mock_operation("index", returns={"_id": "1"})
    with bigfoot.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        es.index(index="idx", document={"a": 1})
    with pytest.raises(InteractionMismatchError):
        bigfoot.elasticsearch_mock.assert_index(index="wrong", document={"a": 1})
    # Assert correctly so teardown passes
    bigfoot.elasticsearch_mock.assert_index(index="idx", document={"a": 1})


def test_missing_assertion_fields_raises(bigfoot_verifier: StrictVerifier) -> None:
    """Incomplete fields in assert_interaction raises MissingAssertionFieldsError."""
    import bigfoot

    bigfoot.elasticsearch_mock.mock_operation("get", returns={"_source": {"a": 1}})
    with bigfoot.sandbox():
        es = elasticsearch.Elasticsearch("http://localhost:9200")
        es.get(index="idx", id="1")

    from bigfoot.plugins.elasticsearch_plugin import _ElasticsearchSentinel

    sentinel = _ElasticsearchSentinel("get")
    with pytest.raises(MissingAssertionFieldsError):
        # Only providing index, missing id
        bigfoot.assert_interaction(sentinel, index="idx")
    # Assert correctly so teardown passes
    bigfoot.elasticsearch_mock.assert_get(index="idx", id="1")
