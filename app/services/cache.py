import hashlib
import json

from redis.asyncio import Redis

from app.config import settings


def _cache_key(tenant_id: str, query: str, page: int, size: int) -> str:
    raw = f"{tenant_id}:{query}:{page}:{size}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"search:{tenant_id}:{digest}"


def _invalidation_pattern(tenant_id: str) -> str:
    return f"search:{tenant_id}:*"


async def get_cached_search(
    redis: Redis,
    tenant_id: str,
    query: str,
    page: int,
    size: int,
) -> dict | None:
    key = _cache_key(tenant_id, query, page, size)
    raw = await redis.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def set_cached_search(
    redis: Redis,
    tenant_id: str,
    query: str,
    page: int,
    size: int,
    data: dict,
) -> None:
    key = _cache_key(tenant_id, query, page, size)
    await redis.setex(key, settings.cache_ttl_seconds, json.dumps(data))


async def invalidate_tenant_cache(redis: Redis, tenant_id: str) -> None:
    """Invalidate all cached search results for a tenant on write/delete."""
    pattern = _invalidation_pattern(tenant_id)
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=pattern, count=100)
        if keys:
            await redis.delete(*keys)
        if cursor == 0:
            break
