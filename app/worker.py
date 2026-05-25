"""Index worker: consumes document events from RabbitMQ.

Handles two event types:
  extract    — download file from S3, extract text, chunk, index to ES, update DB
  delete_s3  — delete S3 files after a document is soft-deleted via API
"""

import asyncio
import json
import logging

import aio_pika
import asyncpg
from elasticsearch import AsyncElasticsearch
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.services import storage as storage_svc
from app.services.db import update_extraction_status
from app.services.extractors import chunk_pages, route_extraction
from app.services.search import ensure_index, index_document_chunks

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("index-worker")


async def handle_extract(
    es: AsyncElasticsearch,
    pool: asyncpg.Pool,
    event: dict,
) -> None:
    """Full extraction pipeline for a file document.

    Steps:
      1. Set DB status → extracting
      2. Download file bytes from S3
      3. Route to extractor → pages
      4. Chunk pages → chunks
      5. Bulk index chunks to ES
      6. Upload extracted.txt to S3 for future cache
      7. Update DB: status=indexed, page_count, word_count
      On any exception → set status=failed with error message.
    """
    doc_id = event["doc_id"]
    tenant_id = event["tenant_id"]
    s3_key = event["s3_key"]
    file_name = event["file_name"]
    mime_type = event["mime_type"]

    log.info("Starting extraction for doc %s (tenant=%s, file=%s)", doc_id, tenant_id, file_name)

    try:
        # 1. Mark as extracting
        await update_extraction_status(pool, doc_id, "extracting")

        # 2. Download file from S3
        file_bytes = await storage_svc.download_file(s3_key)
        log.info("Downloaded %d bytes for doc %s", len(file_bytes), doc_id)

        # 3. Extract text (CPU-heavy — runs in executor via extractors)
        pages = await route_extraction(file_bytes, mime_type)
        log.info("Extracted %d pages from doc %s", len(pages), doc_id)

        # 4. Chunk pages
        chunks = chunk_pages(
            pages,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )
        log.info("Created %d chunks for doc %s", len(chunks), doc_id)

        # 5. Bulk index chunks to Elasticsearch
        await index_document_chunks(
            es=es,
            doc_id=doc_id,
            tenant_id=tenant_id,
            file_name=file_name,
            s3_key=s3_key,
            chunks=chunks,
        )
        log.info("Indexed %d chunks for doc %s", len(chunks), doc_id)

        # 6. Cache extracted text in S3 as extracted.txt
        all_text = "\n\n".join(
            f"[Page {p['page_number']}]\n{p['text']}" for p in pages if p["text"].strip()
        )
        extracted_key = s3_key.rsplit("/", 1)[0] + "/extracted.txt"
        try:
            await storage_svc.upload_text(all_text, extracted_key)
        except Exception as e:
            log.warning("Failed to cache extracted.txt for doc %s: %s", doc_id, e)

        # 7. Compute stats and mark as indexed
        page_count = len(pages)
        word_count = sum(len(p["text"].split()) for p in pages)
        await update_extraction_status(
            pool, doc_id, "indexed",
            page_count=page_count,
            word_count=word_count,
        )
        log.info(
            "doc %s indexed: %d pages, %d words, %d chunks",
            doc_id, page_count, word_count, len(chunks),
        )

    except Exception as e:
        error_msg = str(e)
        log.error("Extraction failed for doc %s: %s", doc_id, error_msg, exc_info=True)
        try:
            await update_extraction_status(pool, doc_id, "failed", extraction_error=error_msg)
        except Exception as db_err:
            log.error("Failed to update DB status to failed for doc %s: %s", doc_id, db_err)
        # Do NOT re-raise — file is still in S3, downloadable, just not searchable.


async def handle_delete_s3(event: dict) -> None:
    """Delete original file and extracted.txt from S3."""
    doc_id = event.get("doc_id", "unknown")
    s3_key = event.get("s3_key")
    extracted_key = event.get("extracted_key")

    if s3_key:
        try:
            await storage_svc.delete_file(s3_key)
            log.info("Deleted S3 object %s for doc %s", s3_key, doc_id)
        except Exception as e:
            log.warning("Failed to delete S3 file %s: %s", s3_key, e)

    if extracted_key:
        try:
            await storage_svc.delete_file(extracted_key)
            log.info("Deleted extracted.txt %s for doc %s", extracted_key, doc_id)
        except Exception as e:
            log.warning("Failed to delete extracted.txt %s: %s", extracted_key, e)


async def handle_event(
    es: AsyncElasticsearch,
    pool: asyncpg.Pool,
    event: dict,
) -> None:
    action = event.get("action")

    if action == "extract":
        await handle_extract(es, pool, event)
    elif action == "delete_s3":
        await handle_delete_s3(event)
    else:
        log.warning("Unknown event action: %s — event: %s", action, event)


@retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30))
async def connect_rabbitmq() -> aio_pika.abc.AbstractRobustConnection:
    log.info("Connecting to RabbitMQ at %s ...", settings.rabbitmq_url)
    return await aio_pika.connect_robust(settings.rabbitmq_url)


async def main() -> None:
    import asyncpg as _asyncpg

    # Elasticsearch
    es = AsyncElasticsearch(settings.elasticsearch_url, retry_on_timeout=True)
    await ensure_index(es)

    # CockroachDB pool
    pool = await _asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        command_timeout=60,
    )

    # RabbitMQ
    connection = await connect_rabbitmq()
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=4)  # limit concurrent extractions

    queue = await channel.declare_queue(settings.rabbitmq_index_queue, durable=True)
    log.info("Index worker ready — consuming from '%s'", settings.rabbitmq_index_queue)

    async with queue.iterator() as q:
        async for message in q:
            async with message.process(requeue=False):
                try:
                    event = json.loads(message.body)
                    await handle_event(es, pool, event)
                except Exception as e:
                    log.error("Failed to process event: %s — %s", message.body, e)

    await connection.close()
    await pool.close()
    await es.close()


if __name__ == "__main__":
    asyncio.run(main())
