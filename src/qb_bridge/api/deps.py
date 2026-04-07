"""FastAPI dependency injection — QB session, auth, database, permissions."""

from __future__ import annotations

import aiosqlite
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from qb_bridge.auth.api_keys import validate_key
from qb_bridge.auth.permissions import check_permission
from qb_bridge.qb.session import QBSessionManager

_api_key_header = APIKeyHeader(
    name="X-API-Key",
    description="Paste your API key here. Generate one with: "
    '`python -m qb_bridge.cli create-key "name"`',
    auto_error=False,
)


def get_db(request: Request) -> aiosqlite.Connection:
    """Get the database connection from app state."""
    return request.app.state.db


def get_qb_session(request: Request) -> QBSessionManager:
    """Get the QBSessionManager from app state."""
    return request.app.state.qb_session


async def require_api_key(
    request: Request,
    x_api_key: str | None = Security(_api_key_header),
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    """Validate the X-API-Key header. Returns the key record (with parsed permissions)."""
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "error": {
                    "code": "MISSING_API_KEY",
                    "message": 'X-API-Key header is required. '
                    'Generate one with: python -m qb_bridge.cli create-key "name"',
                },
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

    # Stash for audit logging and permission checks in route handlers
    request.state.api_key_id = key_record["id"]
    request.state.api_key_name = key_record["name"]
    request.state.permissions = key_record["permissions"]

    return key_record


def require_permission(entity: str, operation: str):
    """Factory that returns a dependency checking a specific permission.

    Usage in route::

        @router.get("/customers")
        async def list_customers(
            key: dict = Depends(require_api_key),
            _perm = Depends(require_permission("Customer", "list")),
        ):
            ...
    """

    async def _check(request: Request, key: dict = Depends(require_api_key)) -> None:
        perms = key.get("permissions", {})
        if not check_permission(perms, entity, operation):
            raise HTTPException(
                status_code=403,
                detail={
                    "ok": False,
                    "error": {
                        "code": "FORBIDDEN",
                        "message": f"This API key does not have '{operation}' permission on '{entity}'",
                    },
                },
            )

    return _check
