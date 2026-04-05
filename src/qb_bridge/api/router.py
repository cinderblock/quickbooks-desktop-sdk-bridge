"""Master router — includes all sub-routers."""

from __future__ import annotations

from fastapi import APIRouter

from qb_bridge.qb.entities import ENTITIES

from .routes import company, discovery, reports, status
from .routes.crud import make_crud_router


def build_api_router() -> APIRouter:
    """Construct the complete API router tree."""
    root = APIRouter()

    # Discovery (mounted at /)
    root.include_router(discovery.router)

    # Status & company
    root.include_router(status.router)
    root.include_router(company.router)

    # Reports
    root.include_router(reports.router)

    # Auto-generate CRUD routers for every registered entity
    for entity_def in ENTITIES.values():
        crud_router = make_crud_router(entity_def)
        root.include_router(crud_router)

    return root
