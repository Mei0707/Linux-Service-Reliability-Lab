import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST
from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout, TooManyRequests
from pydantic import BaseModel, Field, HttpUrl

from app.cache import LinkCache
from app.config import Settings
from app.metrics import DB_ERRORS, EXPECTED, exposition
from app.middleware import RequestMiddleware
from app.repository import IdempotencyConflict, Repository

log = logging.getLogger("lab.requests")
logging.basicConfig(level=logging.INFO, format="%(message)s")
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SAFE_KEY = re.compile(r"^[\x21-\x7e]{1,128}$")


class LinkInput(BaseModel):
    url: HttpUrl = Field(max_length=2048)


def create_app(settings=None, repository=None, cache=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.repository = repository or Repository(settings)
        app.state.cache = cache or LinkCache(settings)
        app.state.stopping = False
        await app.state.repository.open()
        EXPECTED.set(int(os.getenv("EXPECTED_REPLICAS", "3")))
        try:
            yield
        finally:
            app.state.stopping = True
            await app.state.cache.close()
            await app.state.repository.close()

    app = FastAPI(title="Linux Service Reliability Lab", version="0.1.0", lifespan=lifespan)

    app.add_middleware(RequestMiddleware, timeout=settings.request_timeout)

    async def database_unavailable(request, exc):
        DB_ERRORS.labels(type(exc).__name__).inc()
        return JSONResponse(
            {"detail": "Database unavailable"}, status_code=503, headers={"Retry-After": "1"}
        )

    for error_type in (DatabaseError, PoolTimeout, TooManyRequests):
        app.add_exception_handler(error_type, database_unavailable)

    @app.get("/health/live")
    async def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready(request: Request):
        if app.state.stopping or Path(settings.drain_file).exists():
            return JSONResponse({"status": "draining"}, status_code=503)
        try:
            async with asyncio.timeout(1):
                await request.app.state.repository.ping()
        except (DatabaseError, PoolTimeout, TooManyRequests, TimeoutError, OSError):
            return JSONResponse({"status": "not_ready", "database": "unavailable"}, status_code=503)
        redis_ok = await request.app.state.cache.healthy()
        return {
            "status": "ready",
            "database": "ok",
            "cache": "disabled"
            if redis_ok is None and not settings.cache_enabled
            else "ok"
            if redis_ok
            else "degraded",
        }

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(exposition(), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @app.post("/links", status_code=201)
    async def create_link(
        body: LinkInput,
        request: Request,
        response: Response,
        idempotency_key: str | None = Header(default=None),
    ):
        if idempotency_key is not None and not SAFE_KEY.fullmatch(idempotency_key):
            raise HTTPException(400, "Idempotency-Key must be 1–128 visible ASCII characters")
        try:
            row, created = await request.app.state.repository.create(str(body.url), idempotency_key)
        except IdempotencyConflict:
            raise HTTPException(
                409, "Idempotency-Key was already used for a different URL"
            ) from None
        response.status_code = 201 if created else 200
        response.headers["Idempotency-Replayed"] = "false" if created else "true"
        if created:
            await request.app.state.cache.invalidate(row["code"])
        return row

    @app.get("/links/{code}")
    async def resolve(code: str, request: Request):
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,32}", code):
            raise HTTPException(404, "Link not found")
        cache = request.app.state.cache
        repo = request.app.state.repository
        url = await cache.get(code)
        if url is None:
            url = await repo.get(code)
            if url is None:
                raise HTTPException(404, "Link not found")
            await cache.set(code, url)
        await repo.record_hit(code)
        return {"code": code, "url": url}

    @app.get("/links/{code}/stats")
    async def stats(code: str, request: Request):
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,32}", code):
            raise HTTPException(404, "Link not found")
        row = await request.app.state.repository.stats(code)
        if row is None:
            raise HTTPException(404, "Link not found")
        return row

    return app


app = create_app()
