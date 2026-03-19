"""Order processing pipeline using Celery task dispatch."""

from celery import Celery

app = Celery("example")


@app.task(name="example.validate_order")
def validate_order(order_id):
    """Validate an order exists and is ready for payment."""


@app.task(name="example.charge_payment")
def charge_payment(order_id, currency="USD"):
    """Charge payment for the given order."""


@app.task(name="example.send_confirmation")
def send_confirmation(email, order_id):
    """Send order confirmation email."""


def enqueue_order_pipeline(order_id, user_email):
    """Dispatch order validation, payment, and confirmation tasks."""
    validate_order.delay(order_id)
    charge_payment.apply_async(
        args=(order_id,), kwargs={"currency": "USD"}, countdown=5
    )
    send_confirmation.delay(user_email, order_id)
