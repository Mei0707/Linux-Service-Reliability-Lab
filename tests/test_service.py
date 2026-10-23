import asyncio
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError

from app.cache import LinkCache
from app.config import Settings
from app.main import create_app


class StubRepository:
    async def open(self):
        pass

    async def close(self):
        pass

    async def ping(self):
        pass

    async def get(self, code):
        return None


class StubCache:
    async def close(self):
        pass

    async def healthy(self):
        return False

    async def get(self, code):
        return None


def test_redis_is_optional_for_readiness(tmp_path):
    app = create_app(
        replace(Settings(), drain_file=str(tmp_path / "drain")), StubRepository(), StubCache()
    )
    with TestClient(app) as client:
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["cache"] == "degraded"


def test_drain_fails_readiness_but_preserves_liveness(tmp_path):
    flag = tmp_path / "drain"
    flag.touch()
    with TestClient(
        create_app(replace(Settings(), drain_file=str(flag)), StubRepository(), StubCache())
    ) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_database_failure_does_not_fail_liveness():
    class FailingRepository(StubRepository):
        async def ping(self):
            raise OperationalError("unavailable")

    with TestClient(create_app(repository=FailingRepository(), cache=StubCache())) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_deadline_cancels_dependency_work():
    cancelled = []

    class SlowRepository(StubRepository):
        async def get(self, code):
            try:
                await asyncio.sleep(2)
            finally:
                cancelled.append(True)

    app = create_app(replace(Settings(), request_timeout=0.03), SlowRepository(), StubCache())
    with TestClient(app) as client:
        start = time.monotonic()
        response = client.get("/links/abcdefgh")
        assert response.status_code == 503
        assert time.monotonic() - start < 0.5
        assert cancelled == [True]
        assert response.headers["X-Request-ID"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "not-a-url"])
def test_non_http_urls_are_rejected(url):
    with TestClient(create_app(repository=StubRepository(), cache=StubCache())) as client:
        assert client.post("/links", json={"url": url}).status_code == 422


def test_request_id_is_bounded():
    with TestClient(create_app(repository=StubRepository(), cache=StubCache())) as client:
        assert (
            client.get("/health/live", headers={"X-Request-ID": "test-123"}).headers["X-Request-ID"]
            == "test-123"
        )
        assert (
            len(
                client.get("/health/live", headers={"X-Request-ID": "x" * 1000}).headers[
                    "X-Request-ID"
                ]
            )
            == 32
        )


async def test_cache_failure_uses_cooldown_and_recovers():
    class FailingRedis:
        calls = 0

        async def get(self, key):
            self.calls += 1
            if self.calls == 1:
                raise RedisConnectionError("down")
            return "https://example.com/"

    cache = LinkCache(Settings())
    await cache.redis.aclose()
    cache.redis = FailingRedis()
    assert await cache.get("abcdefgh") is None
    assert await cache.get("abcdefgh") is None
    assert cache.redis.calls == 1
    cache.retry_at = 0
    assert await cache.get("abcdefgh") == "https://example.com/"


async def test_cache_disabled_never_touches_redis():
    cache = LinkCache(replace(Settings(), cache_enabled=False))
    assert await cache.get("abcdefgh") is None
    assert await cache.healthy() is None
    await cache.close()
