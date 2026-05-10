from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_db, get_redis
from app.middleware.tenant import get_tenant_id
from app.models import DocumentCreate, DocumentResponse
from app.services import db as db_svc
from app.services.cache import invalidate_tenant_cache
from app.services.queue import publish_index_event
from app.services.rate_limiter import check_rate_limit

router = APIRouter(prefix="/documents", tags=["documents"])


async def enforce_rate_limit(tenant_id: str = Depends(get_tenant_id)) -> str:
    redis = get_redis()
    allowed, retry_after = await check_rate_limit(redis, tenant_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return tenant_id


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    doc: DocumentCreate,
    tenant_id: str = Depends(enforce_rate_limit),
):
    """Index a new document.

    Write path:
      1. Write to CockroachDB (source of truth, ACID)
      2. Publish index event to RabbitMQ (async → ES worker)
      3. Invalidate Redis cache for this tenant
    """
    pool = await get_db()
    result = await db_svc.create_document(pool, tenant_id, doc)

    # Async: publish to queue for ES indexing (~1s eventual consistency)
    await publish_index_event({
        "action": "index",
        "id": result.id,
        "tenant_id": tenant_id,
        "title": result.title,
        "content": result.content,
        "metadata": result.metadata,
        "created_at": result.created_at.isoformat(),
        "updated_at": result.updated_at.isoformat(),
    })

    redis = get_redis()
    await invalidate_tenant_cache(redis, tenant_id)
    return result


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: str,
    tenant_id: str = Depends(enforce_rate_limit),
):
    """Retrieve a document by ID.

    Reads from CockroachDB (strong consistency — always returns the latest
    written state, not the eventually-consistent ES index).
    """
    pool = await get_db()
    doc = await db_svc.get_document(pool, tenant_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: str,
    tenant_id: str = Depends(enforce_rate_limit),
):
    """Soft-delete a document.

    Sets deleted=TRUE in CockroachDB, then publishes a delete event to
    RabbitMQ so the ES worker removes it from the search index.
    """
    pool = await get_db()
    deleted = await db_svc.soft_delete_document(pool, tenant_id, doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")

    await publish_index_event({
        "action": "delete",
        "id": doc_id,
        "tenant_id": tenant_id,
    })

    redis = get_redis()
    await invalidate_tenant_cache(redis, tenant_id)
