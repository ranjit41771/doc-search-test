import asyncpg
from elasticsearch import AsyncElasticsearch
from redis.asyncio import ConnectionPool, Redis

from app.config import settings

_es_client: AsyncElasticsearch | None = None
_redis_pool: ConnectionPool | None = None
_db_pool: asyncpg.Pool | None = None


def get_es() -> AsyncElasticsearch:
    global _es_client
    if _es_client is None:
        _es_client = AsyncElasticsearch(
            settings.elasticsearch_url,
            retry_on_timeout=True,
            max_retries=3,
            # Each worker process keeps up to 32 persistent connections to ES.
            # Without this, every request opens+closes a connection (expensive).
            connections_per_node=32,
        )
    return _es_client


def get_redis() -> Redis:
    global _redis_pool
    if _redis_pool is None:
        # Shared connection pool: 4 workers × 50 concurrent requests = 200 peak.
        # Pool of 50 amortises this well without over-connecting to Redis.
        _redis_pool = ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=50,
        )
    return Redis(connection_pool=_redis_pool)


async def get_db() -> asyncpg.Pool:
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=4,    # keep 4 connections warm — no cold-start on first requests
            max_size=20,   # allow burst up to 20 (4 workers × 5 concurrent DB calls)
            command_timeout=30,
        )
    return _db_pool


async def close_clients() -> None:
    global _es_client, _redis_pool, _db_pool
    if _es_client:
        await _es_client.close()
    if _redis_pool:
        await _redis_pool.aclose()
    if _db_pool:
        await _db_pool.close()
