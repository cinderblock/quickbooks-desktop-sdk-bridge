"""FastAPI dependency injection — QB session, auth, database."""

from __future__ import annotations

import aiosqlite
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from qb_bridge.auth.api_keys import validate_key
from qb_bridge.qb.session import QBSessionManager

# Declares the security scheme in OpenAPI — Swagger UI shows an "Authorize"
# button where you paste your key once, and it's sent on every request.
_api_key_header = APIKeyHeader(
    name="X-API-Key",
    description="Paste your API key here. Generate one with: "
    '`python -m qb_bridge.cli create-key "name"`',
)


def get_db(request: Request) -> aiosqlite.Connection:
    """Get the database connection from app state."""
    return request.app.state.db


def get_qb_session(request: Request) -> QBSessionManager:
    """Get the QBSessionManager from app state."""
    return request.app.state.qb_session


async def require_api_key(
    request: Request,
    x_api_key: str = Security(_api_key_header),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Validate the X-API-Key header. Returns the key record.

    Raises 401 if missing/invalid.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "error": {"code": "MISSING_API_KEY", "message": "X-API-Key header required"},
            },
        )

    key_record = await validate_key(db, x_api_key)
    if key_record is None:
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "error": {"code": "INVALID_API_KEY", "message": "Invalid or revoked API key"},
            },
        )

    # Stash for audit logging
    request.state.api_key_id = key_record["id"]
    request.state.api_key_name = key_record["name"]

    return key_record
