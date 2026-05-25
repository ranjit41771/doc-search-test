import time

from fastapi import APIRouter, Depends

from app.dependencies import get_db, get_es, get_redis
from app.models import HealthDependency, HealthResponse
from app.services.auth import decode_access_token_dep

router = APIRouter(tags=["health"])


@router.get("/ping", include_in_schema=False)
async def ping():
    """Unauthenticated liveness probe for load balancers / orchestrators."""
    return {"status": "ok"}


@router.get("/health", response_model=HealthResponse)
async def health(_: dict = Depends(decode_access_token_dep)):
    """Detailed dependency health check. Requires a valid JWT.

    Exposes internal latency and service topology — restricted to authenticated
    tenants so this information is not visible to unauthenticated scanners.
    """
    deps = {}
    overall = "healthy"

    # Elasticsearch
    t0 = time.monotonic()
    try:
        await get_es().ping()
        deps["elasticsearch"] = HealthDependency(
            status="healthy",
            latency_ms=round((time.monotonic() - t0) * 1000, 2),
        )
    except Exception:
        deps["elasticsearch"] = HealthDependency(
            status="unhealthy",
            latency_ms=round((time.monotonic() - t0) * 1000, 2),
        )
        overall = "degraded"

    # Redis
    t0 = time.monotonic()
    try:
        await get_redis().ping()
        deps["redis"] = HealthDependency(
            status="healthy",
            latency_ms=round((time.monotonic() - t0) * 1000, 2),
        )
    except Exception:
        deps["redis"] = HealthDependency(
            status="unhealthy",
            latency_ms=round((time.monotonic() - t0) * 1000, 2),
        )
        overall = "degraded"

    # CockroachDB
    t0 = time.monotonic()
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        deps["cockroachdb"] = HealthDependency(
            status="healthy",
            latency_ms=round((time.monotonic() - t0) * 1000, 2),
        )
    except Exception:
        deps["cockroachdb"] = HealthDependency(
            status="unhealthy",
            latency_ms=round((time.monotonic() - t0) * 1000, 2),
        )
        overall = "degraded"

    return HealthResponse(status=overall, dependencies=deps)
