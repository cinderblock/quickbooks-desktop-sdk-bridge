"""Master router — includes all sub-routers."""

from __future__ import annotations

from fastapi import APIRouter

from qb_bridge.qb.entities import ENTITIES

from .routes import company, dialogs, discovery, reports, status
from .routes.crud import make_crud_router


def build_api_router() -> APIRouter:
    """Construct the complete API router tree."""
    root = APIRouter()

    # Discovery (mounted at /)
    root.include_router(discovery.router)

    # Status & company
    root.include_router(status.router)
    root.include_router(company.router)

    # QuickBooks dialog watcher
    root.include_router(dialogs.router)

    # Reports
    root.include_router(reports.router)

    # Auto-generate CRUD routers for every registered entity.
    #
    # Longest route first: Starlette matches routes in registration order, so
    # "items" (with its /items/{entity_id} get-by-id route) registered before
    # "items/service" would swallow /items/service as an item whose id is
    # "service" — a 404 with no query, and UNKNOWN_QUERY_PARAM with one. Every
    # sub-typed entity (items/*, payroll-items/*) is reachable only if it is
    # registered before the entity whose path is its prefix.
    for entity_def in sorted(ENTITIES.values(), key=lambda e: -len(e.rest_path)):
        crud_router = make_crud_router(entity_def)
        root.include_router(crud_router)

    return root
