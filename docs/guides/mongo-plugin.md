# MongoPlugin Guide

`MongoPlugin` intercepts `pymongo.collection.Collection` methods at the class level. It covers query operations (`find`, `find_one`), writes (`insert_one`, `insert_many`, `update_one`, `update_many`), deletes (`delete_one`, `delete_many`), and aggregation (`aggregate`, `count_documents`). Each operation name has its own independent FIFO queue.

## Installation

```bash
pip install python-tripwire[pymongo]
```

This installs `pymongo`.

## Setup

In pytest, access `MongoPlugin` through the `tripwire.mongo` proxy. It auto-creates the plugin for the current test on first use:

```python
import tripwire

def test_find_user():
    tripwire.mongo.mock_operation("find_one", returns={"_id": "abc", "name": "Alice"})

    with tripwire:
        import pymongo
        client = pymongo.MongoClient("mongodb://localhost:27017")
        user = client.mydb.users.find_one({"email": "alice@example.com"})

    assert user == {"_id": "abc", "name": "Alice"}

    tripwire.mongo.assert_find_one(
        database="mydb",
        collection="users",
        filter={"email": "alice@example.com"},
        projection=None,
    )
```

For manual use outside pytest, construct `MongoPlugin` explicitly:

```python
from tripwire import StrictVerifier
from tripwire.plugins.mongo_plugin import MongoPlugin

verifier = StrictVerifier()
mongo = MongoPlugin(verifier)
```

Each verifier may have at most one `MongoPlugin`. A second `MongoPlugin(verifier)` raises `ValueError`.

## Registering mock operations

Use `tripwire.mongo.mock_operation(operation, *, returns, ...)` to register a mock before entering the sandbox:

```python
tripwire.mongo.mock_operation("find_one", returns={"_id": "1", "status": "active"})
tripwire.mongo.mock_operation("insert_one", returns=type("Result", (), {"inserted_id": "2"})())
```

### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `operation` | `str` | required | MongoDB operation name (e.g., `"find_one"`, `"insert_one"`, `"update_many"`) |
| `returns` | `Any` | required | Value to return when this mock is consumed |
| `raises` | `BaseException \| None` | `None` | Exception to raise instead of returning |
| `required` | `bool` | `True` | Whether an unused mock causes `UnusedMocksError` at teardown |

### Supported operations

| Operation | Detail fields |
|---|---|
| `find` | `database`, `collection`, `filter`, `projection` |
| `find_one` | `database`, `collection`, `filter`, `projection` |
| `insert_one` | `database`, `collection`, `document` |
| `insert_many` | `database`, `collection`, `documents` |
| `update_one` | `database`, `collection`, `filter`, `update` |
| `update_many` | `database`, `collection`, `filter`, `update` |
| `delete_one` | `database`, `collection`, `filter` |
| `delete_many` | `database`, `collection`, `filter` |
| `aggregate` | `database`, `collection`, `pipeline` |
| `count_documents` | `database`, `collection`, `filter` |

## Per-operation FIFO queues

Each operation name has its own independent FIFO queue. Multiple `mock_operation("find_one", ...)` calls are consumed in registration order:

```python
def test_sequential_queries():
    tripwire.mongo.mock_operation("find_one", returns={"_id": "1", "role": "admin"})
    tripwire.mongo.mock_operation("find_one", returns=None)

    with tripwire:
        import pymongo
        client = pymongo.MongoClient()
        db = client.mydb

        admin = db.users.find_one({"role": "admin"})
        ghost = db.users.find_one({"role": "ghost"})

    assert admin == {"_id": "1", "role": "admin"}
    assert ghost is None

    tripwire.mongo.assert_find_one(
        database="mydb", collection="users",
        filter={"role": "admin"}, projection=None,
    )
    tripwire.mongo.assert_find_one(
        database="mydb", collection="users",
        filter={"role": "ghost"}, projection=None,
    )
```

## Asserting interactions

Use the typed assertion helpers on `tripwire.mongo`. Each helper requires `database` and `collection` plus the operation-specific fields.

### `assert_find(database, collection, filter, projection=None)`

```python
tripwire.mongo.assert_find(
    database="mydb", collection="users",
    filter={"active": True}, projection=None,
)
```

### `assert_find_one(database, collection, filter, projection=None)`

```python
tripwire.mongo.assert_find_one(
    database="mydb", collection="users",
    filter={"_id": "abc"}, projection=None,
)
```

### `assert_insert_one(database, collection, document)`

```python
tripwire.mongo.assert_insert_one(
    database="mydb", collection="users",
    document={"name": "Alice", "email": "alice@example.com"},
)
```

### `assert_insert_many(database, collection, documents)`

```python
tripwire.mongo.assert_insert_many(
    database="mydb", collection="events",
    documents=[{"type": "click"}, {"type": "view"}],
)
```

### `assert_update_one(database, collection, filter, update)`

```python
tripwire.mongo.assert_update_one(
    database="mydb", collection="users",
    filter={"_id": "abc"},
    update={"$set": {"last_login": "2025-01-15"}},
)
```

### `assert_update_many(database, collection, filter, update)`

```python
tripwire.mongo.assert_update_many(
    database="mydb", collection="sessions",
    filter={"expired": True},
    update={"$set": {"cleaned": True}},
)
```

### `assert_delete_one(database, collection, filter)`

```python
tripwire.mongo.assert_delete_one(
    database="mydb", collection="users",
    filter={"_id": "abc"},
)
```

### `assert_delete_many(database, collection, filter)`

```python
tripwire.mongo.assert_delete_many(
    database="mydb", collection="sessions",
    filter={"expired": True},
)
```

### `assert_aggregate(database, collection, pipeline)`

```python
tripwire.mongo.assert_aggregate(
    database="mydb", collection="orders",
    pipeline=[{"$match": {"status": "complete"}}, {"$group": {"_id": "$customer", "total": {"$sum": "$amount"}}}],
)
```

### `assert_count_documents(database, collection, filter)`

```python
tripwire.mongo.assert_count_documents(
    database="mydb", collection="users",
    filter={"active": True},
)
```

## Simulating errors

Use the `raises` parameter to simulate MongoDB errors:

```python
import pymongo.errors
import tripwire

def test_duplicate_key():
    tripwire.mongo.mock_operation(
        "insert_one",
        returns=None,
        raises=pymongo.errors.DuplicateKeyError("E11000 duplicate key error"),
    )

    with tripwire:
        import pymongo
        client = pymongo.MongoClient()
        with pytest.raises(pymongo.errors.DuplicateKeyError):
            client.mydb.users.insert_one({"_id": "abc", "name": "Alice"})

    tripwire.mongo.assert_insert_one(
        database="mydb", collection="users",
        document={"_id": "abc", "name": "Alice"},
    )
```

## Full example

**Production code** (`examples/mongo_store/app.py`):

```python
--8<-- "examples/mongo_store/app.py"
```

**Test** (`examples/mongo_store/test_app.py`):

```python
--8<-- "examples/mongo_store/test_app.py"
```

## Optional mocks

Mark a mock as optional with `required=False`:

```python
tripwire.mongo.mock_operation("count_documents", returns=0, required=False)
```

An optional mock that is never triggered does not cause `UnusedMocksError` at teardown.

## UnmockedInteractionError

When code calls a MongoDB collection method that has no remaining mocks in its queue, tripwire raises `UnmockedInteractionError`:

```
mongo.find_one(...) was called but no mock was registered.
Register a mock with:
    tripwire.mongo.mock_operation('find_one', returns=...)
```
