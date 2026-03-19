"""Test gRPC service calls using bigfoot grpc_mock."""

import bigfoot

from .app import fetch_user_orders


def test_fetch_user_orders():
    bigfoot.grpc_mock.mock_unary_unary(
        "/commerce.UserService/GetUser",
        returns={"id": 7, "name": "Alice", "email": "alice@example.com"},
    )
    bigfoot.grpc_mock.mock_unary_stream(
        "/commerce.OrderService/ListOrders",
        returns=[
            {"order_id": "A1", "total": 29.99},
            {"order_id": "A2", "total": 149.00},
        ],
    )

    with bigfoot:
        user, orders = fetch_user_orders(7)

    assert user["name"] == "Alice"
    assert len(orders) == 2
    assert orders[0]["order_id"] == "A1"

    bigfoot.grpc_mock.assert_unary_unary(
        "/commerce.UserService/GetUser", request={"id": 7},
    )
    bigfoot.grpc_mock.assert_unary_stream(
        "/commerce.OrderService/ListOrders", request={"user_id": 7},
    )
