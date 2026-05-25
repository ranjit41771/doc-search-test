"""Index worker: consumes document events from RabbitMQ, writes to Elasticsearch."""

import asyncio
import json
import logging

import aio_pika
from elasticsearch import AsyncElasticsearch, NotFoundError
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.search import ensure_index

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("index-worker")


async def handle_event(es: AsyncElasticsearch, event: dict) -> None:
    action = event.get("action")
    doc_id = event.get("id")

    if action == "index":
        body = {
            "tenant_id": event["tenant_id"],
            "title": event["title"],
            "content": event["content"],
            "metadata": event.get("metadata", {}),
            "created_at": event["created_at"],
            "updated_at": event["updated_at"],
            "deleted": False,
        }
        await es.index(index=settings.es_index, id=doc_id, body=body)
        log.info("Indexed document %s (tenant=%s)", doc_id, event["tenant_id"])

    elif action == "delete":
        try:
            await es.delete(index=settings.es_index, id=doc_id)
            log.info("Deleted document %s from ES", doc_id)
        except NotFoundError:
            # Document was never indexed or already removed — safe to ignore.
            log.debug("Delete no-op: document %s not found in ES index", doc_id)
        except Exception as e:
            # Any other failure (ES down, timeout, etc.) means the document
            # remains searchable after the tenant deleted it. Re-raise so the
            # message is requeued and retried rather than silently lost.
            log.error("Failed to delete document %s from ES: %s", doc_id, e)
            raise


@retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30))
async def connect_rabbitmq() -> aio_pika.abc.AbstractRobustConnection:
    log.info("Connecting to RabbitMQ at %s ...", settings.rabbitmq_url)
    return await aio_pika.connect_robust(settings.rabbitmq_url)


async def main() -> None:
    es = AsyncElasticsearch(settings.elasticsearch_url, retry_on_timeout=True)
    await ensure_index(es)

    connection = await connect_rabbitmq()
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)

    queue = await channel.declare_queue(settings.rabbitmq_index_queue, durable=True)
    log.info("Index worker ready — consuming from '%s'", settings.rabbitmq_index_queue)

    async with queue.iterator() as q:
        async for message in q:
            async with message.process(requeue=False):
                try:
                    event = json.loads(message.body)
                    await handle_event(es, event)
                except Exception as e:
                    log.error("Failed to process event: %s — %s", message.body, e)

    await connection.close()
    await es.close()


if __name__ == "__main__":
    asyncio.run(main())
