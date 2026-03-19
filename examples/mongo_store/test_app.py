"""Test MongoDB order creation using bigfoot mongo_mock."""

import logging

import pymongo
import pytest

import bigfoot

from .app import create_order


@pytest.fixture(autouse=True)
def _silence_pymongo():
    """Suppress pymongo DEBUG logs that would generate LoggingPlugin interactions."""
    for name in ("pymongo", "pymongo.topology", "pymongo.connection"):
        logging.getLogger(name).setLevel(logging.WARNING)


def test_create_order():
    mock_result = type("InsertOneResult", (), {"inserted_id": "order_789"})()
    bigfoot.mongo_mock.mock_operation("insert_one", returns=mock_result)
    update_result = type("UpdateResult", (), {"modified_count": 1})()
    bigfoot.mongo_mock.mock_operation("update_one", returns=update_result)

    with bigfoot:
        client = pymongo.MongoClient("mongodb://localhost:27017")
        order_id = create_order(client.shopdb, "cust_123", [{"sku": "WIDGET", "qty": 3}])

    assert order_id == "order_789"

    bigfoot.mongo_mock.assert_insert_one(
        database="shopdb",
        collection="orders",
        document={
            "customer_id": "cust_123",
            "items": [{"sku": "WIDGET", "qty": 3}],
            "status": "pending",
        },
    )
    bigfoot.mongo_mock.assert_update_one(
        database="shopdb",
        collection="customers",
        filter={"_id": "cust_123"},
        update={"$inc": {"order_count": 1}},
    )
