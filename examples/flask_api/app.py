"""Minimal Flask API that charges a payment provider and logs the result."""

import logging

import httpx

logger = logging.getLogger("payments")


def create_charge(amount: int, currency: str) -> dict:
    """Call external payment API and return charge details."""
    response = httpx.post(
        "https://api.stripe.com/v1/charges",
        json={"amount": amount, "currency": currency},
    )
    data = response.json()
    logger.info(f"Charge created: {data['id']} for {amount} {currency}")
    return data
