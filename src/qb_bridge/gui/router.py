"""Web GUI routes — configuration dashboard served with Jinja2 + htmx."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite
import bcrypt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer

from qb_bridge.api.deps import get_db, get_qb_session
from qb_bridge.auth.api_keys import create_key, list_keys, revoke_key
from qb_bridge.database import get_setting, set_setting
from qb_bridge.qb.process import is_qb_running
from qb_bridge.qb.session import QBSessionManager

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

gui_router = APIRouter(prefix="/gui", tags=["GUI"])

SESSION_SECRET = "qb-bridge-gui-session-secret"  # Overridden at runtime from DB
SESSION_COOKIE = "qbb_session"
SESSION_MAX_AGE = 86400  # 24 hours


def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(SESSION_SECRET)


def _check_session(request: Request) -> bool:
    """Validate the session cookie. Returns True if logged in."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    try:
        _get_serializer().loads(token, max_age=SESSION_MAX_AGE)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@gui_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@gui_router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    password: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    stored_hash = await get_setting(db, "gui_password_hash")

    if not stored_hash:
        # First login — set the password
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        await set_setting(db, "gui_password_hash", hashed)
        stored_hash = hashed

    if not bcrypt.checkpw(password.encode(), stored_hash.encode()):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid password"}
        )

    token = _get_serializer().dumps({"user": "admin"})
    response = RedirectResponse("/gui/", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, max_age=SESSION_MAX_AGE)
    return response


@gui_router.get("/logout")
async def logout():
    response = RedirectResponse("/gui/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@gui_router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    session: QBSessionManager = Depends(get_qb_session),
):
    if not _check_session(request):
        return RedirectResponse("/gui/login", status_code=303)

    # Recent audit log entries
    async with db.execute("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 20") as cursor:
        recent_logs = [dict(row) for row in await cursor.fetchall()]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "qb_running": is_qb_running(),
            "qb_state": session.state,
            "qb_idle": round(session.idle_seconds, 1),
            "recent_logs": recent_logs,
        },
    )


# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------


@gui_router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_page(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    if not _check_session(request):
        return RedirectResponse("/gui/login", status_code=303)

    keys = await list_keys(db)
    return templates.TemplateResponse(
        "api_keys.html",
        {
            "request": request,
            "keys": keys,
            "new_key": None,
        },
    )


@gui_router.post("/api-keys/create", response_class=HTMLResponse)
async def create_api_key(
    request: Request,
    name: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    if not _check_session(request):
        return RedirectResponse("/gui/login", status_code=303)

    key_id, plaintext_key = await create_key(db, name)
    keys = await list_keys(db)
    return templates.TemplateResponse(
        "api_keys.html",
        {
            "request": request,
            "keys": keys,
            "new_key": plaintext_key,
        },
    )


@gui_router.post("/api-keys/{key_id}/revoke")
async def revoke_api_key(
    key_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    if not _check_session(request):
        return RedirectResponse("/gui/login", status_code=303)

    await revoke_key(db, key_id)
    return RedirectResponse("/gui/api-keys", status_code=303)


# ---------------------------------------------------------------------------
# Connection Settings
# ---------------------------------------------------------------------------


@gui_router.get("/connection", response_class=HTMLResponse)
async def connection_page(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    session: QBSessionManager = Depends(get_qb_session),
):
    if not _check_session(request):
        return RedirectResponse("/gui/login", status_code=303)

    return templates.TemplateResponse(
        "connection.html",
        {
            "request": request,
            "qb_running": is_qb_running(),
            "qb_state": session.state,
            "company_file": await get_setting(db, "company_file_path") or "",
            "idle_timeout": await get_setting(db, "idle_timeout_seconds") or "600",
            "auto_launch": await get_setting(db, "auto_launch_qb") or "true",
            "auto_close": await get_setting(db, "auto_close_qb") or "false",
        },
    )


@gui_router.post("/connection", response_class=HTMLResponse)
async def update_connection(
    request: Request,
    company_file: str = Form(""),
    idle_timeout: str = Form("600"),
    auto_launch: str = Form("false"),
    auto_close: str = Form("false"),
    db: aiosqlite.Connection = Depends(get_db),
):
    if not _check_session(request):
        return RedirectResponse("/gui/login", status_code=303)

    await set_setting(db, "company_file_path", company_file)
    await set_setting(db, "idle_timeout_seconds", idle_timeout)
    await set_setting(db, "auto_launch_qb", auto_launch)
    await set_setting(db, "auto_close_qb", auto_close)

    return RedirectResponse("/gui/connection", status_code=303)


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


@gui_router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    if not _check_session(request):
        return RedirectResponse("/gui/login", status_code=303)

    async with db.execute("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 100") as cursor:
        logs = [dict(row) for row in await cursor.fetchall()]

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "logs": logs,
        },
    )
