"""Test Celery task dispatch using tripwire celery_mock."""

import logging

import pytest

import tripwire

from .app import enqueue_order_pipeline


@pytest.fixture(autouse=True)
def _silence_celery():
    """Suppress celery DEBUG logs that would generate LoggingPlugin interactions."""
    for name in ("celery", "kombu"):
        logging.getLogger(name).setLevel(logging.WARNING)


def test_enqueue_order_pipeline():
    tripwire.celery_mock.mock_delay("example.validate_order", returns=None)
    tripwire.celery_mock.mock_apply_async("example.charge_payment", returns=None)
    tripwire.celery_mock.mock_delay("example.send_confirmation", returns=None)

    with tripwire:
        enqueue_order_pipeline("order-42", "buyer@example.com")

    tripwire.celery_mock.assert_delay(
        task_name="example.validate_order",
        args=("order-42",),
        kwargs={},
        options={},
    )
    tripwire.celery_mock.assert_apply_async(
        task_name="example.charge_payment",
        args=("order-42",),
        kwargs={"currency": "USD"},
        options={"countdown": 5},
    )
    tripwire.celery_mock.assert_delay(
        task_name="example.send_confirmation",
        args=("buyer@example.com", "order-42"),
        kwargs={},
        options={},
    )
