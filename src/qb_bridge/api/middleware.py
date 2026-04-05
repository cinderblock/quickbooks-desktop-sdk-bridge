"""FastAPI middleware — IP filtering, request logging, error handling."""

from __future__ import annotations

import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from qb_bridge.auth.ip_filter import is_private_ip

log = logging.getLogger(__name__)

# Paths that bypass API key auth (documentation + GUI)
PUBLIC_PATH_PREFIXES = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/gui",
    "/static",
    "/favicon.ico",
)


class IPFilterMiddleware(BaseHTTPMiddleware):
    """Reject requests from non-private IP addresses."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_ip = request.client.host if request.client else "unknown"

        if not is_private_ip(client_ip):
            log.warning("Blocked request from non-private IP: %s", client_ip)
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False,
                    "error": {
                        "code": "FORBIDDEN_IP",
                        "message": f"Access denied: {client_ip} is not a private IP",
                    },
                },
            )

        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with timing info."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.monotonic()
        client_ip = request.client.host if request.client else "?"

        response = await call_next(request)

        duration_ms = (time.monotonic() - start) * 1000
        log.info(
            "%s %s %s -> %d (%.1fms)",
            client_ip,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )

        # Store timing for audit log (picked up by deps if needed)
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"

        return response
