import time

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.metrics import CACHE, CACHE_ERRORS


class LinkCache:
    def __init__(self, settings):
        self.enabled = settings.cache_enabled
        self.ttl = settings.cache_ttl
        self.retry_at = 0.0
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=0.05,
            socket_connect_timeout=0.05,
            max_connections=128,
            retry_on_timeout=False,
            retry=Retry(NoBackoff(), 0),
        )

    async def close(self):
        await self.redis.aclose()

    async def _call(self, operation, *args, **kwargs):
        if not self.enabled or time.monotonic() < self.retry_at:
            return None
        try:
            return await getattr(self.redis, operation)(*args, **kwargs)
        except (RedisError, OSError):
            CACHE_ERRORS.labels(operation).inc()
            self.retry_at = time.monotonic() + 1.0
            return None

    async def get(self, code):
        if not self.enabled:
            CACHE.labels("disabled").inc()
            return None
        value = await self._call("get", f"link:{code}")
        CACHE.labels("hit" if value is not None else "miss").inc()
        return value

    async def set(self, code, url):
        await self._call("set", f"link:{code}", url, ex=self.ttl)

    async def invalidate(self, code):
        await self._call("delete", f"link:{code}")

    async def healthy(self):
        return bool(await self._call("ping")) if self.enabled else None
