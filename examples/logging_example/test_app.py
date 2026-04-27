"""Test process_order using tripwire log_mock."""

import tripwire

from .app import process_order


def test_process_order():
    with tripwire:
        result = process_order(42)

    assert result == "success"

    tripwire.log_mock.assert_info("Processing order 42", "orders")
    tripwire.log_mock.assert_debug("Validating payment for order 42", "orders")
    tripwire.log_mock.assert_info("Order 42 completed", "orders")
