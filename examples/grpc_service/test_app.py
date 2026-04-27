"""Test gRPC service calls using tripwire grpc_mock."""

import tripwire

from .app import fetch_user_orders


def test_fetch_user_orders():
    tripwire.grpc_mock.mock_unary_unary(
        "/commerce.UserService/GetUser",
        returns={"id": 7, "name": "Alice", "email": "alice@example.com"},
    )
    tripwire.grpc_mock.mock_unary_stream(
        "/commerce.OrderService/ListOrders",
        returns=[
            {"order_id": "A1", "total": 29.99},
            {"order_id": "A2", "total": 149.00},
        ],
    )

    with tripwire:
        user, orders = fetch_user_orders(7)

    assert user["name"] == "Alice"
    assert len(orders) == 2
    assert orders[0]["order_id"] == "A1"

    tripwire.grpc_mock.assert_unary_unary(
        "/commerce.UserService/GetUser", request={"id": 7},
    )
    tripwire.grpc_mock.assert_unary_stream(
        "/commerce.OrderService/ListOrders", request={"user_id": 7},
    )
