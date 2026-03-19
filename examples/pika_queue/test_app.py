"""Test RabbitMQ publishing using bigfoot pika_mock."""

import bigfoot

from .app import publish_event


def test_publish_event():
    (bigfoot.pika_mock
        .new_session()
        .expect("connect",  returns=None)
        .expect("channel",  returns=None)
        .expect("publish",  returns=None)
        .expect("close",    returns=None))

    with bigfoot:
        publish_event("mq.internal", "events", "order.created", b'{"order_id": 42}')

    bigfoot.pika_mock.assert_connect(host="mq.internal", port=5672, virtual_host="/")
    bigfoot.pika_mock.assert_channel()
    bigfoot.pika_mock.assert_publish(
        exchange="events",
        routing_key="order.created",
        body=b'{"order_id": 42}',
        properties=None,
    )
    bigfoot.pika_mock.assert_close()
