"""FastAPI middleware — IP filtering, request logging, error handling."""

from __future__ import annotations

import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from qb_bridge.auth.ip_filter import is_private_ip
from qb_bridge.database import log_request as _db_log_request

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
    """Log every request with timing info and write API requests to the audit log."""

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

        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"

        # Write audit log for /api/v1/* requests only
        if request.url.path.startswith("/api/v1/"):
            try:
                db = request.app.state.db
                # api_key_id is set by require_api_key dep; absent on 401/unauthenticated paths
                api_key_id = getattr(request.state, "api_key_id", None)
                error_detail = None
                if response.status_code >= 400:
                    error_detail = str(response.status_code)
                await _db_log_request(
                    db,
                    api_key_id=api_key_id,
                    client_ip=client_ip,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=round(duration_ms, 2),
                    error=error_detail,
                )
            except Exception:
                log.exception("Failed to write audit log entry")

        return response
