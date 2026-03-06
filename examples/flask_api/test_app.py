"""Test create_charge using bigfoot HTTP mocking and log assertions."""

import pytest

httpx = pytest.importorskip("httpx")

from dirty_equals import IsInstance  # noqa: E402

import bigfoot  # noqa: E402

from .app import create_charge  # noqa: E402


def test_create_charge_posts_to_stripe_and_logs():
    bigfoot.http.mock_response(
        "POST",
        "https://api.stripe.com/v1/charges",
        json={"id": "ch_test_123", "amount": 5000, "currency": "usd"},
        status=200,
    )

    with bigfoot:
        result = create_charge(amount=5000, currency="usd")

    assert result == {"id": "ch_test_123", "amount": 5000, "currency": "usd"}

    bigfoot.http.assert_request(
        method="POST",
        url="https://api.stripe.com/v1/charges",
        headers=IsInstance(dict),
        body=IsInstance(str),
    )
    bigfoot.log_mock.assert_info(
        'HTTP Request: POST https://api.stripe.com/v1/charges "HTTP/1.1 200 OK"',
        "httpx",
    )
    bigfoot.log_mock.assert_info(
        "Charge created: ch_test_123 for 5000 usd", "payments"
    )
