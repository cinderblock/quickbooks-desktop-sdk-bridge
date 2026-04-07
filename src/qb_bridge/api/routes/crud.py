"""Generic CRUD route factory for QuickBooks entities.

Generates a full set of list / get / create / update / delete routes
for any registered entity, reducing per-entity boilerplate to zero.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from qb_bridge.api.deps import get_qb_session, require_api_key, require_permission
from qb_bridge.qb import xml_builder, xml_parser
from qb_bridge.qb.entities import EntityDef
from qb_bridge.qb.session import QBSessionManager


def make_crud_router(entity: EntityDef) -> APIRouter:
    """Create a FastAPI router with CRUD endpoints for *entity*.

    Each inner function captures ``entity`` via closure — no default
    parameter tricks that upset Pydantic's model introspection.
    """
    router = APIRouter(
        prefix=f"/api/v1/{entity.rest_path}",
        tags=[entity.name],
    )

    # Capture entity fields in local vars for the closure
    ent_name = entity.name
    ent_id_field = entity.id_field
    ent_is_txn = entity.is_transaction
    safe_path = entity.rest_path.replace("-", "_").replace("/", "_")

    # ----- LIST -----
    if entity.supports_query:

        @router.get(
            "",
            summary=f"List {ent_name}s",
            description=entity.description,
            name=f"list_{safe_path}",
        )
        async def list_entities(
            session: QBSessionManager = Depends(get_qb_session),
            key: dict = Depends(require_api_key),
            _perm=Depends(require_permission(ent_name, "list")),
            name: str | None = Query(None, description="Filter by name (contains)"),
            active: str = Query("ActiveOnly", description="ActiveOnly | InactiveOnly | All"),
            modified_after: str | None = Query(
                None, description="ISO datetime, e.g. 2024-01-01T00:00:00"
            ),
            max_returned: int = Query(100, ge=1, le=5000, description="Max results to return"),
            iterator_id: str | None = Query(None, description="Continue a previous iterator"),
        ):
            filters: dict = {}

            if name:
                if ent_is_txn:
                    filters["RefNumber"] = name
                else:
                    filters["NameFilter"] = {"MatchCriterion": "Contains", "Name": name}

            if active != "ActiveOnly":
                filters["ActiveStatus"] = active

            if modified_after:
                filters["FromModifiedDate"] = modified_after

            iterator = None
            if iterator_id:
                iterator = "Continue"
            else:
                if max_returned > 0:
                    filters["MaxReturned"] = str(max_returned)

            request_xml = xml_builder.query(
                ent_name,
                filters=filters,
                iterator="Start" if not iterator_id and max_returned >= 100 else iterator,
                iterator_id=iterator_id,
            )
            response_xml = await session.execute(request_xml)
            resp = xml_parser.parse_response(response_xml)

            items = []
            if resp.data is not None:
                items = resp.data if isinstance(resp.data, list) else [resp.data]

            meta: dict = {"count": len(items)}
            if resp.iterator_id:
                meta["iterator_id"] = resp.iterator_id
            if resp.iterator_remaining is not None:
                meta["remaining"] = resp.iterator_remaining

            return {"ok": True, "data": items, "meta": meta}

        # ----- GET BY ID -----
        @router.get(
            "/{entity_id}",
            summary=f"Get {ent_name} by ID",
            name=f"get_{safe_path}",
        )
        async def get_entity(
            entity_id: str,
            session: QBSessionManager = Depends(get_qb_session),
            key: dict = Depends(require_api_key),
            _perm=Depends(require_permission(ent_name, "get")),
        ):
            filters = {ent_id_field: entity_id}
            request_xml = xml_builder.query(ent_name, filters=filters)
            response_xml = await session.execute(request_xml)
            item = xml_parser.parse_single_entity(response_xml, ent_name)
            if item is None:
                raise HTTPException(
                    404,
                    detail={
                        "ok": False,
                        "error": {"code": "NOT_FOUND", "message": f"{ent_name} not found"},
                    },
                )
            return {"ok": True, "data": item}

    # ----- CREATE -----
    if entity.supports_add:

        @router.post(
            "",
            status_code=201,
            summary=f"Create {ent_name}",
            name=f"create_{safe_path}",
        )
        async def create_entity(
            body: dict = Body(...),
            session: QBSessionManager = Depends(get_qb_session),
            key: dict = Depends(require_api_key),
            _perm=Depends(require_permission(ent_name, "create")),
        ):
            request_xml = xml_builder.add(ent_name, body)
            response_xml = await session.execute(request_xml)
            created = xml_parser.parse_single_entity(response_xml, ent_name)
            return {"ok": True, "data": created}

    # ----- UPDATE -----
    if entity.supports_mod:

        @router.put(
            "/{entity_id}",
            summary=f"Update {ent_name}",
            name=f"update_{safe_path}",
        )
        async def update_entity(
            entity_id: str,
            body: dict = Body(...),
            session: QBSessionManager = Depends(get_qb_session),
            key: dict = Depends(require_api_key),
            _perm=Depends(require_permission(ent_name, "update")),
        ):
            edit_sequence = body.pop("EditSequence", None) or body.pop("edit_sequence", None)
            if not edit_sequence:
                raise HTTPException(
                    400,
                    detail={
                        "ok": False,
                        "error": {
                            "code": "MISSING_EDIT_SEQUENCE",
                            "message": "EditSequence is required for updates. "
                            "Get it from the entity's current data via GET.",
                        },
                    },
                )

            mod_data = {ent_id_field: entity_id, "EditSequence": edit_sequence}
            mod_data.update(body)

            request_xml = xml_builder.mod(ent_name, mod_data)
            response_xml = await session.execute(request_xml)
            updated = xml_parser.parse_single_entity(response_xml, ent_name)
            return {"ok": True, "data": updated}

    # ----- DELETE -----
    if entity.supports_delete:

        @router.delete(
            "/{entity_id}",
            summary=f"Delete {ent_name}",
            name=f"delete_{safe_path}",
        )
        async def delete_entity(
            entity_id: str,
            session: QBSessionManager = Depends(get_qb_session),
            key: dict = Depends(require_api_key),
            _perm=Depends(require_permission(ent_name, "delete")),
        ):
            if ent_is_txn:
                request_xml = xml_builder.delete(ent_name, is_transaction=True, txn_id=entity_id)
            else:
                request_xml = xml_builder.delete(ent_name, is_transaction=False, list_id=entity_id)
            response_xml = await session.execute(request_xml)
            xml_parser.check_status(response_xml)
            return {"ok": True, "data": None}

    return router
