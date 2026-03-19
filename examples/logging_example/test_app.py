"""Test process_order using bigfoot log_mock."""

import bigfoot

from .app import process_order


def test_process_order():
    with bigfoot:
        result = process_order(42)

    assert result == "success"

    bigfoot.log_mock.assert_info("Processing order 42", "orders")
    bigfoot.log_mock.assert_debug("Validating payment for order 42", "orders")
    bigfoot.log_mock.assert_info("Order 42 completed", "orders")
