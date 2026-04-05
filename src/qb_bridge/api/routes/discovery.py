"""API root and discovery endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from qb_bridge.qb.entities import ENTITIES

router = APIRouter(tags=["Discovery"])


@router.get("/", summary="API root", description="Links to documentation and available resources.")
async def api_root(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "ok": True,
        "data": {
            "name": "QuickBooks Bridge API",
            "version": "0.1.0",
            "documentation": {
                "swagger_ui": f"{base}/docs",
                "redoc": f"{base}/redoc",
                "openapi_spec": f"{base}/openapi.json",
            },
            "endpoints": {
                "status": f"{base}/api/v1/status",
                "entities": f"{base}/api/v1/entities",
                "company": f"{base}/api/v1/company",
            },
        },
    }


@router.get(
    "/api/v1/entities",
    summary="List available entities",
    description="Shows every QuickBooks entity the API can interact with and which operations are supported.",
)
async def list_entities():
    return {
        "ok": True,
        "data": [entity.to_dict() for entity in ENTITIES.values()],
        "meta": {"count": len(ENTITIES)},
    }
