import time
import uuid

from redis.asyncio import Redis

from app.config import settings


async def check_rate_limit(redis: Redis, tenant_id: str) -> tuple[bool, int]:
    """True sliding window rate limit per tenant using a Redis sorted set.

    Each request is stored as a member scored by its Unix timestamp.
    On every call we:
      1. Remove all members older than (now - window_seconds)  → ZREMRANGEBYSCORE
      2. Add the current request with score = now              → ZADD
      3. Count remaining members                               → ZCARD
      4. Reset TTL so the key expires when no longer needed    → EXPIRE

    All four commands run in a single pipeline (atomic batch) — no race condition.

    Unlike a fixed window counter, this approach never allows a burst at the
    window boundary because the window always looks back exactly window_seconds
    from the current moment, not from a fixed clock tick.

    Returns:
        (True, 0)                  — request allowed
        (False, retry_after_secs)  — request blocked, client should wait N seconds
    """
    now    = time.time()
    cutoff = now - settings.rate_limit_window_seconds
    key    = f"rate:{tenant_id}"

    async with redis.pipeline(transaction=True) as pipe:
        # Remove requests outside the sliding window
        pipe.zremrangebyscore(key, 0, cutoff)
        # Add this request (unique member, score = current timestamp)
        pipe.zadd(key, {str(uuid.uuid4()): now})
        # Count how many requests are in the window
        pipe.zcard(key)
        # Auto-expire the key after the window passes (cleanup)
        pipe.expire(key, settings.rate_limit_window_seconds + 1)
        _, _, count, _ = await pipe.execute()

    if count > settings.rate_limit_requests:
        # Earliest request in the window tells us when a slot frees up
        earliest = await redis.zrange(key, 0, 0, withscores=True)
        if earliest:
            retry_after = int(settings.rate_limit_window_seconds - (now - earliest[0][1])) + 1
        else:
            retry_after = settings.rate_limit_window_seconds
        return False, retry_after

    return True, 0
