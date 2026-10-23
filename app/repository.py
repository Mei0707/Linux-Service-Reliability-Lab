import hashlib
import secrets
import time
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.metrics import DB_DURATION, POOL, WAITING


class IdempotencyConflict(Exception):
    pass


class Repository:
    def __init__(self, settings):
        self.pool = AsyncConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.pool_size,
            timeout=settings.pool_timeout,
            max_waiting=2048,
            open=False,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "prepare_threshold": None,
                "connect_timeout": 3,
            },
            check=AsyncConnectionPool.check_connection,
        )

    async def open(self):
        await self.pool.open(wait=True, timeout=15)

    async def close(self):
        await self.pool.close()

    @asynccontextmanager
    async def connection(self, operation):
        start = time.perf_counter()
        try:
            async with self.pool.connection() as connection:
                yield connection
        finally:
            DB_DURATION.labels(operation).observe(time.perf_counter() - start)
            stats = self.pool.get_stats()
            POOL.set(stats.get("pool_size", 0))
            WAITING.set(stats.get("requests_waiting", 0))

    async def ping(self):
        async with self.connection("health") as connection:
            await connection.execute("SELECT 1")

    async def create(self, url, idempotency_key):
        fingerprint = hashlib.sha256(url.encode()).hexdigest()
        async with self.connection("create") as connection:
            async with connection.transaction():
                for _ in range(5):
                    code = secrets.token_urlsafe(8)
                    cursor = await connection.execute(
                        """INSERT INTO links (code, url, idempotency_key, request_hash)
                           VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
                           RETURNING code, url, created_at""",
                        (code, url, idempotency_key, fingerprint),
                    )
                    row = await cursor.fetchone()
                    if row:
                        return row, True
                    if idempotency_key:
                        cursor = await connection.execute(
                            "SELECT code, url, created_at, request_hash FROM links WHERE idempotency_key = %s",
                            (idempotency_key,),
                        )
                        previous = await cursor.fetchone()
                        if previous:
                            if previous.pop("request_hash") != fingerprint:
                                raise IdempotencyConflict()
                            return previous, False
                raise RuntimeError("Could not allocate a unique short code")

    async def get(self, code):
        async with self.connection("resolve") as connection:
            cursor = await connection.execute("SELECT url FROM links WHERE code = %s", (code,))
            row = await cursor.fetchone()
            return row["url"] if row else None

    async def record_hit(self, code):
        # Shards reduce hot-row lock contention; the committed sum is exact.
        async with self.connection("record_hit") as connection:
            await connection.execute(
                """INSERT INTO link_hits (code, bucket, hits) VALUES (%s, %s, 1)
                   ON CONFLICT (code, bucket) DO UPDATE SET hits = link_hits.hits + 1""",
                (code, secrets.randbelow(64)),
            )

    async def stats(self, code):
        async with self.connection("stats") as connection:
            cursor = await connection.execute(
                """SELECT l.code, l.url, l.created_at, COALESCE(SUM(h.hits), 0)::bigint AS hits
                   FROM links l LEFT JOIN link_hits h ON l.code = h.code
                   WHERE l.code = %s GROUP BY l.code""",
                (code,),
            )
            return await cursor.fetchone()
