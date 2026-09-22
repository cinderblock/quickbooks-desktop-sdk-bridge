"""Warm the QuickBooks connection up before you need it.

Opening a QuickBooks session is the expensive part of a request: if QuickBooks
isn't running it has to start first, which takes 15-25 seconds. After that
every request is fast until the session is released on idle. This endpoint
pays that cost on purpose, at a moment of the caller's choosing, so the request
that actually matters doesn't.

It doubles as a keep-alive: each call resets the idle timer, so calling it
inside ``idle_timeout`` keeps the session open.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from qb_bridge.api.deps import get_qb_session, require_api_key, require_permission
from qb_bridge.api.strict import StrictQueryParamsRoute
from qb_bridge.qb import xml_builder, xml_parser
from qb_bridge.qb.process import is_qb_running
from qb_bridge.qb.session import QBSessionManager

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/connection", tags=["Connection"], route_class=StrictQueryParamsRoute
)


async def warm_up(session: QBSessionManager) -> dict:
    """Open the session (starting QuickBooks if needed) and report what it took.

    Uses HostQueryRq — the cheapest real round-trip there is — so a success
    means the whole path works, not just that a process is running.
    """
    started_at = time.monotonic()
    qb_was_running = is_qb_running()
    was_connected = session.state == "connected"

    response_xml = await session.execute(xml_builder.build_request("HostQueryRq"), idempotent=True)
    host = xml_parser.parse_single_entity(response_xml, "Host") or {}

    return {
        "state": session.state,
        "already_warm": was_connected,
        "started_quickbooks": not qb_was_running,
        "elapsed_seconds": round(time.monotonic() - started_at, 2),
        # Call again within this many seconds to keep the session open.
        "stays_warm_for_seconds": session.idle_timeout,
        "quickbooks": {
            "product": host.get("ProductName"),
            "version": host.get("MajorVersion"),
            "country": host.get("Country"),
            "file_mode": host.get("QBFileMode"),
        },
    }


@router.post(
    "/warm",
    summary="Warm up the QuickBooks connection",
    description=(
        "Opens the QuickBooks session now — starting QuickBooks Desktop if it isn't "
        "running — so the next request doesn't pay for it. Returns once the connection "
        "is live, which can take 15-25 seconds from cold; pass `wait=false` to kick it "
        "off in the background and get an immediate 202 instead.\n\n"
        "Each call also resets the idle timer, so calling it periodically (within "
        "`stays_warm_for_seconds`) keeps QuickBooks open and every request fast."
    ),
)
async def warm_connection(
    request: Request,
    wait: bool = Query(
        True,
        description="Wait for the connection to be live (default). false returns at once.",
    ),
    session: QBSessionManager = Depends(get_qb_session),
    _key: dict = Depends(require_api_key),
    # Warming touches no company data — a read-only key is meant to be able to
    # warm the connection it is about to read through.
    _perm=Depends(require_permission("Connection", "get")),
):
    if wait:
        return {"ok": True, "data": await warm_up(session)}

    existing: asyncio.Task | None = getattr(request.app.state, "warm_task", None)
    if existing is not None and not existing.done():
        return JSONResponse(
            status_code=202,
            content={
                "ok": True,
                "data": {"state": "warming", "already_warming": True},
            },
        )

    async def _warm_in_background() -> None:
        try:
            result = await warm_up(session)
            log.info(
                "Background warm-up finished in %.1fs (started QuickBooks: %s)",
                result["elapsed_seconds"],
                result["started_quickbooks"],
            )
        except Exception as exc:
            # Nobody is waiting on this response, so the log is the only place
            # this can be seen — don't let it vanish.
            log.error("Background warm-up failed: %s", exc)

    request.app.state.warm_task = asyncio.create_task(_warm_in_background(), name="qb-warm-up")
    return JSONResponse(
        status_code=202,
        content={"ok": True, "data": {"state": "warming", "already_warming": False}},
    )
