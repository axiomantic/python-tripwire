"""Fetch user and their orders via gRPC."""

import grpc


def fetch_user_orders(user_id):
    """Look up a user and list their orders over gRPC."""
    channel = grpc.insecure_channel("api.example.com:50051")
    user_stub = channel.unary_unary("/commerce.UserService/GetUser")
    order_stub = channel.unary_stream("/commerce.OrderService/ListOrders")

    user = user_stub({"id": user_id})
    orders = list(order_stub({"user_id": user_id}))
    channel.close()
    return user, orders
