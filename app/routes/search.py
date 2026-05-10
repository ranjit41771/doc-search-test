from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import get_es, get_redis
from app.middleware.tenant import get_tenant_id
from app.models import SearchResponse, SearchResult
from app.services import cache as cache_svc
from app.services import search as search_svc
from app.services.rate_limiter import check_rate_limit

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=100, description="Results per page"),
    tenant_id: str = Depends(get_tenant_id),
):
    redis = get_redis()

    allowed, retry_after = await check_rate_limit(redis, tenant_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    # L2 cache check
    cached = await cache_svc.get_cached_search(redis, tenant_id, q, page, size)
    if cached:
        cached["cached"] = True
        return SearchResponse(**cached)

    es = get_es()
    results, total, took_ms = await search_svc.search_documents(es, tenant_id, q, page, size)

    response_data = {
        "query": q,
        "tenant_id": tenant_id,
        "total": total,
        "took_ms": took_ms,
        "results": [r.model_dump() for r in results],
        "cached": False,
    }
    await cache_svc.set_cached_search(redis, tenant_id, q, page, size, response_data)

    return SearchResponse(**response_data)
