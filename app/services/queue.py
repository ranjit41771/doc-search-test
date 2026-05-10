"""RabbitMQ publisher for async document indexing via aio-pika."""

import json
import logging

import aio_pika

from app.config import settings

log = logging.getLogger(__name__)

_connection: aio_pika.abc.AbstractRobustConnection | None = None
_channel: aio_pika.abc.AbstractChannel | None = None


async def _get_channel() -> aio_pika.abc.AbstractChannel:
    global _connection, _channel
    if _connection is None or _connection.is_closed:
        _connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    if _channel is None or _channel.is_closed:
        _channel = await _connection.channel()
        await _channel.declare_queue(settings.rabbitmq_index_queue, durable=True)
    return _channel


async def publish_index_event(event: dict) -> None:
    try:
        channel = await _get_channel()
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(event).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=settings.rabbitmq_index_queue,
        )
    except Exception as e:
        # Non-fatal: CockroachDB write already succeeded.
        log.warning("Failed to publish index event (ES sync will lag): %s", e)


async def close_queue() -> None:
    global _connection
    if _connection and not _connection.is_closed:
        await _connection.close()
