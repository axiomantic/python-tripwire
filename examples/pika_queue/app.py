"""Publish events to RabbitMQ using pika."""

import pika


def publish_event(host, exchange, routing_key, body):
    """Publish a message to a RabbitMQ exchange."""
    params = pika.ConnectionParameters(host=host)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.basic_publish(exchange=exchange, routing_key=routing_key, body=body)
    connection.close()
