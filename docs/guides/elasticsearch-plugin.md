# ElasticsearchPlugin Guide

`ElasticsearchPlugin` intercepts `elasticsearch.Elasticsearch` methods at the class level. It covers indexing (`index`), search (`search`, `msearch`), document retrieval (`get`, `mget`), deletion (`delete`), updates (`update`), bulk operations (`bulk`), and counting (`count`). Each operation name has its own independent FIFO queue. Only keyword arguments that are actually passed by your code are stored in interaction details.

## Installation

```bash
pip install python-tripwire[elasticsearch]
```

This installs `elasticsearch`.

## Setup

In pytest, access `ElasticsearchPlugin` through the `tripwire.elasticsearch` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_index_document():
    tripwire.elasticsearch.mock_operation(
        "index",
        returns={"_id": "doc_1", "result": "created"},
    )

    with tripwire:
        from elasticsearch import Elasticsearch
        es = Elasticsearch("http://localhost:9200")
        result = es.index(index="products", document={"name": "Widget", "price": 9.99}, id="doc_1")

    assert result["result"] == "created"

    tripwire.elasticsearch.assert_index(
        index="products",
        document={"name": "Widget", "price": 9.99},
        id="doc_1",
    )
```

For manual use outside pytest, construct `ElasticsearchPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.elasticsearch_plugin import ElasticsearchPlugin

verifier = StrictVerifier()
es_mock = ElasticsearchPlugin(verifier)
```

Each verifier may have at most one `ElasticsearchPlugin`. A second `ElasticsearchPlugin(verifier)` raises `ValueError`.

## Registering mock operations

Use `tripwire.elasticsearch.mock_operation(operation, *, returns, ...)` to register a mock before entering the sandbox:

```python
tripwire.elasticsearch.mock_operation("search", returns={"hits": {"hits": [], "total": {"value": 0}}})
tripwire.elasticsearch.mock_operation("index", returns={"_id": "1", "result": "created"})
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `operation` | `str` | required | Elasticsearch method name (e.g., `"search"`, `"index"`, `"get"`) |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### Supported operations and their detail fields

Only kwargs that are actually passed by your code are captured in interaction details. If your code does not pass `size` to `search()`, then `size` is not present in the details and does not need to be asserted.

| Operation | Captured kwargs |
|---|---|
| `index` | `index`, `document`, `id` |
| `search` | `index`, `query`, `size`, `from_` |
| `get` | `index`, `id` |
| `delete` | `index`, `id` |
| `update` | `index`, `id`, `doc` |
| `bulk` | `operations` |
| `count` | `index`, `query` |
| `mget` | `index`, `docs` |
| `msearch` | `searches` |

## Per-operation FIFO queues

Each operation name has its own independent FIFO queue. Multiple `mock_operation("search", ...)` calls are consumed in registration order:

```python
def test_paginated_search():
    tripwire.elasticsearch.mock_operation(
        "search",
        returns={"hits": {"hits": [{"_id": "1"}], "total": {"value": 25}}},
    )
    tripwire.elasticsearch.mock_operation(
        "search",
        returns={"hits": {"hits": [{"_id": "11"}], "total": {"value": 25}}},
    )

    with tripwire:
        from elasticsearch import Elasticsearch
        es = Elasticsearch()
        page1 = es.search(index="logs", query={"match_all": {}}, size=10)
        page2 = es.search(index="logs", query={"match_all": {}}, size=10, from_=10)

    assert page1["hits"]["hits"][0]["_id"] == "1"
    assert page2["hits"]["hits"][0]["_id"] == "11"

    tripwire.elasticsearch.assert_search(index="logs", query={"match_all": {}}, size=10)
    tripwire.elasticsearch.assert_search(index="logs", query={"match_all": {}}, size=10, from_=10)
```

## Asserting interactions

Use the typed assertion helpers on `tripwire.elasticsearch`. Each helper accepts keyword arguments matching the detail fields captured for that operation.

### `assert_index(*, index, document, id=None)`

```python
tripwire.elasticsearch.assert_index(
    index="products",
    document={"name": "Widget", "price": 9.99},
    id="doc_1",
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `index` | `str` | required | The target index |
| `document` | `Any` | required | The document body |
| `id` | `str \| None` | `None` | Document ID (omit if not passed in the call) |

### `assert_search(*, index=None, query=None, size=None, from_=None)`

```python
tripwire.elasticsearch.assert_search(
    index="logs",
    query={"match": {"level": "error"}},
    size=50,
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `index` | `str \| None` | `None` | The target index |
| `query` | `Any` | `None` | The search query body |
| `size` | `int \| None` | `None` | Number of results to return |
| `from_` | `int \| None` | `None` | Offset for pagination |

### `assert_get(*, index, id)`

```python
tripwire.elasticsearch.assert_get(index="products", id="doc_1")
```

| Parameter | Type | Description |
|---|---|---|
| `index` | `str` | The target index |
| `id` | `str` | The document ID |

### `assert_delete(*, index, id)`

```python
tripwire.elasticsearch.assert_delete(index="products", id="doc_1")
```

| Parameter | Type | Description |
|---|---|---|
| `index` | `str` | The target index |
| `id` | `str` | The document ID |

### `assert_bulk(*, operations)`

```python
tripwire.elasticsearch.assert_bulk(
    operations=[
        {"index": {"_index": "logs", "_id": "1"}},
        {"message": "first log entry"},
    ],
)
```

| Parameter | Type | Description |
|---|---|---|
| `operations` | `Any` | The bulk operations list |

## Simulating errors

Use the `raises` parameter to simulate Elasticsearch errors:

```python
from elasticsearch import NotFoundError
import tripwire

def test_document_not_found():
    tripwire.elasticsearch.mock_operation(
        "get",
        returns=None,
        raises=NotFoundError(404, "document_missing_exception", {"_index": "products", "_id": "missing"}),
    )

    with tripwire:
        from elasticsearch import Elasticsearch
        es = Elasticsearch()
        with pytest.raises(NotFoundError):
            es.get(index="products", id="missing")

    tripwire.elasticsearch.assert_get(index="products", id="missing")
```

## Full example

**Production code** (`examples/elasticsearch_search/app.py`):

```python
--8<-- "examples/elasticsearch_search/app.py"
```

**Test** (`examples/elasticsearch_search/test_app.py`):

```python
--8<-- "examples/elasticsearch_search/test_app.py"
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.elasticsearch.mock_operation("count", returns={"count": 0}, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls an Elasticsearch method that has no remaining mocks in its queue, tripwire raises `UnmockedInteractionError`:

```
elasticsearch.search(...) was called but no mock was registered.
Register a mock with:
    tripwire.elasticsearch.mock_operation('search', returns=...)
```
