"""Search route — full-text search over indexed file chunks."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_db, get_es, get_redis
from app.middleware.tenant import get_tenant_id
from app.models import FileSearchResponse, FileSearchResultItem
from app.services import cache as cache_svc
from app.services import db as db_svc
from app.services import search as search_svc
from app.services import storage as storage_svc
from app.services.rate_limiter import check_rate_limit

log = logging.getLogger(__name__)

router = APIRouter(tags=["search"])


@router.get("/search", response_model=FileSearchResponse)
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=100, description="Results per page"),
    tenant_id: str = Depends(get_tenant_id),
):
    """Full-text search over extracted document text.

    Returns results grouped by document (one result per doc, best chunk).
    Each result includes a highlighted snippet, page hint, relevance score,
    and a presigned S3 download URL (1-hour TTL).

    Results are cached in Redis for 60 seconds per (tenant, query, page, size).
    """
    redis = get_redis()

    allowed, retry_after = await check_rate_limit(redis, tenant_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    # L2 Redis cache check
    cached = await cache_svc.get_cached_search(redis, tenant_id, q, page, size)
    if cached:
        cached["cached"] = True
        return FileSearchResponse(**cached)

    es = get_es()
    raw_results, total, took_ms = await search_svc.search_documents(es, tenant_id, q, page, size)

    # Enrich each result with presigned URL and DB extraction status
    pool = await get_db()
    enriched: list[FileSearchResultItem] = []

    for r in raw_results:
        # Generate presigned URL for download
        download_url = ""
        if r.get("s3_key"):
            try:
                download_url = await storage_svc.generate_presigned_url(r["s3_key"])
            except Exception as e:
                log.warning("Presigned URL failed for doc %s: %s", r["doc_id"], e)

        # Fetch extraction status from DB for this tenant's doc
        extraction_status = "indexed"
        try:
            doc = await db_svc.get_file_document(pool, tenant_id, r["doc_id"])
            if doc:
                extraction_status = doc.get("extraction_status", "indexed")
        except Exception:
            pass

        enriched.append(FileSearchResultItem(
            doc_id=r["doc_id"],
            file_name=r["file_name"],
            snippet=r["snippet"],
            page_hint=r["page_number"],
            score=r["score"],
            download_url=download_url,
            extraction_status=extraction_status,
        ))

    response_data = {
        "results": [item.model_dump() for item in enriched],
        "total": total,
        "query_time_ms": took_ms,
        "query": q,
        "tenant_id": tenant_id,
        "cached": False,
    }

    await cache_svc.set_cached_search(redis, tenant_id, q, page, size, response_data)

    return FileSearchResponse(**response_data)
