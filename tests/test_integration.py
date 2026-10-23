import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import redis

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("LAB_INTEGRATION") != "1", reason="Set LAB_INTEGRATION=1 with the lab running"
    ),
]
BASE = os.getenv("LAB_URL", "http://127.0.0.1:8088")


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE, timeout=15) as client:
        yield client


def create(client):
    response = client.post("/links", json={"url": "https://example.com/test"})
    assert response.status_code == 201, response.text
    return response.json()["code"]


def test_health_and_metrics_boundary(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/metrics").status_code == 404


def test_create_resolve_and_exact_stats(client):
    code = create(client)
    assert client.get(f"/links/{code}/stats").json()["hits"] == 0
    for _ in range(3):
        response = client.get(f"/links/{code}")
        assert response.status_code == 200
        assert response.json()["url"] == "https://example.com/test"
    assert client.get(f"/links/{code}/stats").json()["hits"] == 3


def test_idempotency_replay_and_conflict(client):
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    one = client.post("/links", headers=headers, json={"url": "https://example.com/a"})
    two = client.post("/links", headers=headers, json={"url": "https://example.com/a"})
    assert one.status_code == 201
    assert two.status_code == 200
    assert one.json() == two.json()
    assert two.headers["Idempotency-Replayed"] == "true"
    assert (
        client.post("/links", headers=headers, json={"url": "https://example.com/b"}).status_code
        == 409
    )


def test_concurrent_retries_create_one_link(client):
    headers = {"Idempotency-Key": uuid.uuid4().hex}

    def write(_):
        return client.post("/links", headers=headers, json={"url": "https://example.com/race"})

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(write, range(32)))
    assert sum(response.status_code == 201 for response in results) == 1
    assert all(response.status_code in [200, 201] for response in results)
    assert len({response.json()["code"] for response in results}) == 1


def test_concurrent_hits_are_durable_and_exact(client):
    code = create(client)
    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(lambda _: client.get(f"/links/{code}"), range(100)))
    assert all(response.status_code == 200 for response in results)
    assert client.get(f"/links/{code}/stats").json()["hits"] == 100


def test_missing_links_and_invalid_input(client):
    assert client.get("/links/nonexistent123").status_code == 404
    assert client.get("/links/nonexistent123/stats").status_code == 404
    assert client.post("/links", json={"url": "ftp://example.com"}).status_code == 422
    assert (
        client.post(
            "/links", headers={"Idempotency-Key": "x" * 129}, json={"url": "https://example.com"}
        ).status_code
        == 400
    )


def test_cache_population_and_ttl(client):
    if os.getenv("CACHE_ENABLED", "true") != "true":
        pytest.skip("Cache is disabled for this experiment")
    code = create(client)
    cache = redis.Redis(host="127.0.0.1", port=int(os.getenv("REDIS_PORT", "56379")))
    try:
        assert cache.get(f"link:{code}") is None
        assert client.get(f"/links/{code}").status_code == 200
        assert cache.get(f"link:{code}") == b"https://example.com/test"
        assert 0 < cache.ttl(f"link:{code}") <= int(os.getenv("CACHE_TTL", "60"))
        cache.delete(f"link:{code}")
        assert client.get(f"/links/{code}").status_code == 200
        assert client.get(f"/links/{code}/stats").json()["hits"] == 2
    finally:
        cache.close()
