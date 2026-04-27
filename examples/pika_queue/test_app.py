"""Test RabbitMQ publishing using tripwire pika_mock."""

import tripwire

from .app import publish_event


def test_publish_event():
    (tripwire.pika_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("channel",  returns=None)
        .expect("publish",  returns=None)
        .expect("close",    returns=None))

    with tripwire:
        publish_event("mq.internal", "events", "order.created", b'{"order_id": 42}')

    tripwire.pika_mock.assert_connect(host="mq.internal", port=5672, virtual_host="/")
    tripwire.pika_mock.assert_channel()
    tripwire.pika_mock.assert_publish(
        exchange="events",
        routing_key="order.created",
        body=b'{"order_id": 42}',
        properties=None,
    )
    tripwire.pika_mock.assert_close()
