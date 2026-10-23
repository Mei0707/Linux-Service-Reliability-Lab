import hashlib
import json
import random
import uuid
from bisect import bisect_left
from pathlib import Path

from locust import FastHttpUser, between, task

PROFILE_BYTES = Path(__file__).with_name("profile.json").read_bytes()
PROFILE = json.loads(PROFILE_BYTES)
PROFILE_HASH = hashlib.sha256(PROFILE_BYTES).hexdigest()
WEIGHTS = PROFILE["weights"]
CUMULATIVE = []
for rank in range(1, PROFILE["key_count"] + 1):
    CUMULATIVE.append((CUMULATIVE[-1] if CUMULATIVE else 0) + rank ** -PROFILE["zipf_exponent"])


def seed_code(index):
    return f"seed{index:08d}"


class LinkUser(FastHttpUser):
    connection_timeout = 3
    network_timeout = 10
    wait_time = between(PROFILE["think_time_min_seconds"], PROFILE["think_time_max_seconds"])
    sequence = 0

    def on_start(self):
        type(self).sequence += 1
        self.rng = random.Random(PROFILE["seed"] + type(self).sequence)
        self.run_id = uuid.uuid4().hex
        self.writes = 0

    def hot_code(self):
        rank = bisect_left(CUMULATIVE, self.rng.random() * CUMULATIVE[-1]) + 1
        return seed_code(rank)

    @task(WEIGHTS["read"])
    def resolve(self):
        code = self.hot_code()
        with self.client.get(
            f"/links/{code}", name="/links/[code]", catch_response=True
        ) as response:
            if response.status_code == 200:
                try:
                    if response.json().get("url") != f"https://example.com/seed/{int(code[4:])}":
                        response.failure("Incorrect resolved URL")
                except (ValueError, AttributeError):
                    response.failure("Invalid JSON response")

    @task(WEIGHTS["write"])
    def create(self):
        self.writes += 1
        key = f"load-{self.run_id}-{self.writes}"
        with self.client.post(
            "/links",
            name="/links",
            json={"url": "https://example.com/load"},
            headers={"Idempotency-Key": key},
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure(f"Expected a newly created link; got {response.status_code}")

    @task(WEIGHTS["stats"])
    def stats(self):
        self.client.get(f"/links/{self.hot_code()}/stats", name="/links/[code]/stats")
