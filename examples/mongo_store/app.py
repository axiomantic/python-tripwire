"""Order creation with MongoDB."""


def create_order(db, customer_id, items):
    """Insert an order document and update the customer's order count."""
    order = {"customer_id": customer_id, "items": items, "status": "pending"}
    result = db.orders.insert_one(order)
    db.customers.update_one(
        {"_id": customer_id},
        {"$inc": {"order_count": 1}},
    )
    return str(result.inserted_id)
