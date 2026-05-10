import time

from fastapi import APIRouter

from app.dependencies import get_db, get_es, get_redis
from app.models import HealthDependency, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health():
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
