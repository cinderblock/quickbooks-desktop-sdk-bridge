"""Web GUI routes — configuration dashboard served with Jinja2 + htmx."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiosqlite
import bcrypt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer

from qb_bridge import __version__
from qb_bridge.api.deps import get_db, get_dialog_watcher, get_qb_session
from qb_bridge.auth.api_keys import create_key, list_keys, revoke_key
from qb_bridge.config import get_settings
from qb_bridge.database import get_setting, set_setting
from qb_bridge.qb.dialogs import DialogError, DialogWatcher, find_dialogs
from qb_bridge.qb.process import is_qb_running
from qb_bridge.qb.session import QBSessionManager

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["version"] = __version__

gui_router = APIRouter(prefix="/gui", tags=["GUI"])

_SESSION_SECRET = "qb-bridge-gui-session-secret-placeholder"
SESSION_COOKIE = "qbb_session"
SESSION_MAX_AGE = 86400  # 24 hours


def set_session_secret(secret: str) -> None:
    """Called at app startup with the secret loaded from (or saved to) the DB."""
    global _SESSION_SECRET
    _SESSION_SECRET = secret


def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_SESSION_SECRET)


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
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, max_age=SESSION_MAX_AGE, samesite="strict"
    )
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
# QuickBooks dialogs
# ---------------------------------------------------------------------------


async def _dialogs_page(
    request: Request,
    watcher: DialogWatcher,
    *,
    message: str = "",
    error: str = "",
) -> HTMLResponse:
    """Render the dialog page from a fresh scan of QuickBooks' windows.

    The scan is a blocking Win32 enumeration, so it runs in a thread rather
    than stalling the event loop while QuickBooks answers.
    """
    dialogs: list[dict] = []
    try:
        for dialog in await asyncio.to_thread(find_dialogs):
            entry = dialog.as_dict()
            matched = watcher.match(dialog)
            entry["rule"] = matched[0].name if matched else None
            entry["auto_dismiss_button"] = matched[1].label if matched else None
            dialogs.append(entry)
    except DialogError as exc:
        error = error or f"Could not scan for QuickBooks dialogs: {exc}"

    return templates.TemplateResponse(
        "dialogs.html",
        {
            "request": request,
            "dialogs": dialogs,
            "events": [e.as_dict() for e in watcher.events],
            "watcher": watcher.status(),
            "rules_file": get_settings().dialog_rules_file,
            "message": message,
            "error": error,
        },
    )


@gui_router.get("/dialogs", response_class=HTMLResponse)
async def dialogs_page(
    request: Request,
    watcher: DialogWatcher = Depends(get_dialog_watcher),
):
    if not _check_session(request):
        return RedirectResponse("/gui/login", status_code=303)
    return await _dialogs_page(request, watcher)


@gui_router.post("/dialogs/dismiss", response_class=HTMLResponse)
async def dismiss_dialog(
    request: Request,
    hwnd: int = Form(...),
    button: str = Form(...),
    watcher: DialogWatcher = Depends(get_dialog_watcher),
):
    if not _check_session(request):
        return RedirectResponse("/gui/login", status_code=303)

    try:
        found = await asyncio.to_thread(find_dialogs)
        dialog = next((d for d in found if d.hwnd == hwnd), None)
    except DialogError as exc:
        return await _dialogs_page(request, watcher, error=str(exc))

    if dialog is None:
        return await _dialogs_page(
            request,
            watcher,
            error="That dialog is no longer open — it may have been dismissed already.",
        )

    target = dialog.button(button)
    if target is None:
        return await _dialogs_page(
            request,
            watcher,
            error=f"{dialog.title!r} has no button labelled {button!r}.",
        )

    event = await asyncio.to_thread(watcher.dismiss, dialog, target)
    if event.action == "dismissed":
        return await _dialogs_page(
            request, watcher, message=f"Clicked {button!r} on {dialog.title!r}."
        )
    return await _dialogs_page(
        request,
        watcher,
        error=f"Clicked {button!r} on {dialog.title!r} but it did not close: {event.detail}",
    )


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
