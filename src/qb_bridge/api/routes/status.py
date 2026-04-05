"""Health / status endpoint."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from qb_bridge.api.deps import get_qb_session, require_api_key
from qb_bridge.qb.process import is_qb_running
from qb_bridge.qb.session import QBSessionManager

router = APIRouter(prefix="/api/v1", tags=["Status"])

_start_time = time.monotonic()


@router.get(
    "/status",
    summary="Service health check",
    description="Returns QB connection status, uptime, and whether QuickBooks Desktop is running.",
)
async def get_status(
    request: Request,
    session: QBSessionManager = Depends(get_qb_session),
    key: dict = Depends(require_api_key),
):
    uptime = time.monotonic() - _start_time
    return {
        "ok": True,
        "data": {
            "service": "QuickBooks Bridge API",
            "version": "0.1.0",
            "uptime_seconds": round(uptime, 1),
            "qb_desktop_running": is_qb_running(),
            "qb_connection_state": session.state,
            "qb_idle_seconds": round(session.idle_seconds, 1),
        },
    }
