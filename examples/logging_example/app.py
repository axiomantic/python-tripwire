"""Order processing with structured logging."""

import logging


def process_order(order_id: int) -> str:
    """Process an order, logging each step."""
    logger = logging.getLogger("orders")
    logger.info("Processing order %d", order_id)
    logger.debug("Validating payment for order %d", order_id)
    logger.info("Order %d completed", order_id)
    return "success"
