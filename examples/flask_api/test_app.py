"""Test create_charge using tripwire HTTP mocking and log assertions."""

from dirty_equals import IsInstance

import tripwire

from .app import create_charge


def test_create_charge_posts_to_stripe_and_logs():
    tripwire.http.mock_response(
        "POST",
        "https://api.stripe.com/v1/charges",
        json={"id": "ch_test_123", "amount": 5000, "currency": "usd"},
        status=200,
    )

    with tripwire:
        result = create_charge(amount=5000, currency="usd")

    assert result == {"id": "ch_test_123", "amount": 5000, "currency": "usd"}

    tripwire.http.assert_request(
        method="POST",
        url="https://api.stripe.com/v1/charges",
        headers=IsInstance(dict),
        body=IsInstance(str),
    ).assert_response(
        status=200,
        headers=IsInstance(dict),
        body='{"id": "ch_test_123", "amount": 5000, "currency": "usd"}',
    )
    tripwire.log_mock.assert_info(
        'HTTP Request: POST https://api.stripe.com/v1/charges "HTTP/1.1 200 OK"',
        "httpx",
    )
    tripwire.log_mock.assert_info(
        "Charge created: ch_test_123 for 5000 usd", "payments"
    )
