import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from app.metrics import DB_ERRORS, INFLIGHT, LATENCY, REQUESTS

log = logging.getLogger("lab.requests")
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestMiddleware:
    """Keep deadline cancellation in the endpoint's ASGI task, including DB work."""

    def __init__(self, app, timeout):
        self.app = app
        self.timeout = timeout

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = Headers(scope=scope).get("X-Request-ID", "")
        if not SAFE_ID.fullmatch(request_id):
            request_id = uuid.uuid4().hex
        started = False
        status = 500
        start = time.perf_counter()

        async def observed_send(message):
            nonlocal started, status
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
                status = message["status"]
                started = True
            await send(message)

        INFLIGHT.inc()
        try:
            async with asyncio.timeout(self.timeout):
                await self.app(scope, receive, observed_send)
        except TimeoutError:
            DB_ERRORS.labels("deadline").inc()
            if started:
                raise
            await JSONResponse(
                {"detail": "Request deadline exceeded"},
                status_code=503,
                headers={"Retry-After": "1"},
            )(scope, receive, observed_send)
        except Exception as exc:
            log.error(
                json.dumps(
                    {
                        "event": "request_error",
                        "request_id": request_id,
                        "error_type": type(exc).__name__,
                    }
                )
            )
            if started:
                raise
            await JSONResponse({"detail": "Internal server error"}, status_code=500)(
                scope, receive, observed_send
            )
        finally:
            INFLIGHT.dec()
            route = scope.get("route")
            route_name = route.path if route else "unmatched"
            duration = time.perf_counter() - start
            if route_name not in ("/metrics", "/health/live", "/health/ready"):
                REQUESTS.labels(scope["method"], route_name, str(status)).inc()
                LATENCY.labels(scope["method"], route_name).observe(duration)
            log.info(
                json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "event": "request",
                        "request_id": request_id,
                        "method": scope["method"],
                        "route": route_name,
                        "status": status,
                        "duration_ms": round(duration * 1000, 3),
                    }
                )
            )
