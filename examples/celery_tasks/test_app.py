"""Test Celery task dispatch using bigfoot celery_mock."""

import logging

import pytest

import bigfoot

from .app import enqueue_order_pipeline


@pytest.fixture(autouse=True)
def _silence_celery():
    """Suppress celery DEBUG logs that would generate LoggingPlugin interactions."""
    for name in ("celery", "kombu"):
        logging.getLogger(name).setLevel(logging.WARNING)


def test_enqueue_order_pipeline():
    bigfoot.celery_mock.mock_delay("example.validate_order", returns=None)
    bigfoot.celery_mock.mock_apply_async("example.charge_payment", returns=None)
    bigfoot.celery_mock.mock_delay("example.send_confirmation", returns=None)

    with bigfoot:
        enqueue_order_pipeline("order-42", "buyer@example.com")

    bigfoot.celery_mock.assert_delay(
        task_name="example.validate_order",
        args=("order-42",),
        kwargs={},
        options={},
    )
    bigfoot.celery_mock.assert_apply_async(
        task_name="example.charge_payment",
        args=("order-42",),
        kwargs={"currency": "USD"},
        options={"countdown": 5},
    )
    bigfoot.celery_mock.assert_delay(
        task_name="example.send_confirmation",
        args=("buyer@example.com", "order-42"),
        kwargs={},
        options={},
    )
