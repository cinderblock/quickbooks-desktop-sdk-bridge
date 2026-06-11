"""Generic CRUD route factory for QuickBooks entities.

Generates a full set of list / get / create / update / delete routes
for any registered entity, reducing per-entity boilerplate to zero.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from qb_bridge.api.deps import get_qb_session, require_api_key, require_permission
from qb_bridge.api.strict import StrictQueryParamsRoute
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
        route_class=StrictQueryParamsRoute,
    )

    # Capture entity fields in local vars for the closure
    ent_name = entity.name
    ent_id_field = entity.id_field
    ent_is_txn = entity.is_transaction
    ent_supports_iterator = entity.supports_iterator
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
            request: Request,
            session: QBSessionManager = Depends(get_qb_session),
            key: dict = Depends(require_api_key),
            _perm=Depends(require_permission(ent_name, "list")),
            name: str | None = Query(None, description="Filter by name (contains)"),
            entity_name: str | None = Query(
                None,
                description=(
                    "Filter transactions by customer/job/vendor name (contains match, "
                    "e.g. a job name). Transactions only."
                ),
            ),
            active: str = Query(
                "ActiveOnly",
                description="ActiveOnly | InactiveOnly | All (list entities only; ignored for transactions)",
            ),
            modified_after: str | None = Query(
                None, description="ISO datetime, e.g. 2024-01-01T00:00:00"
            ),
            from_date: str | None = Query(
                None, description="TxnDate >= this date, e.g. 2026-01-01 (transactions only)"
            ),
            to_date: str | None = Query(
                None, description="TxnDate <= this date, e.g. 2026-12-31 (transactions only)"
            ),
            max_returned: int = Query(100, ge=1, le=5000, description="Max results to return"),
            iterator_id: str | None = Query(
                None,
                description=(
                    "Pagination cursor. Pass 'Start' to begin an iterator, then pass the "
                    "meta.iterator_id from each response to fetch the next page. This is the "
                    "only way to reach records beyond max_returned for entities with more than "
                    "max_returned rows."
                ),
            ),
        ):
            # Reject parameters that don't apply to this entity type rather than
            # silently ignoring them (a silent no-op hides client mistakes).
            sent = request.query_params
            if ent_is_txn:
                if "active" in sent:
                    raise HTTPException(
                        400,
                        detail={
                            "ok": False,
                            "error": {
                                "code": "PARAM_NOT_APPLICABLE",
                                "message": (
                                    f"'active' does not apply to {ent_name}: transactions "
                                    f"have no active/inactive status. Remove it, or use "
                                    f"from_date/to_date/entity_name to filter transactions."
                                ),
                            },
                        },
                    )
            else:
                txn_only = [p for p in ("from_date", "to_date", "entity_name") if p in sent]
                if txn_only:
                    raise HTTPException(
                        400,
                        detail={
                            "ok": False,
                            "error": {
                                "code": "PARAM_NOT_APPLICABLE",
                                "message": (
                                    f"{', '.join(txn_only)} only appl{'ies' if len(txn_only) == 1 else 'y'} "
                                    f"to transaction entities, not {ent_name}. Use 'name'/'active'/"
                                    f"'modified_after' for list entities."
                                ),
                            },
                        },
                    )

            filters: dict = {}

            # Determine iterator mode
            iterator = None
            iter_id = None
            if iterator_id:
                if not ent_supports_iterator:
                    raise HTTPException(
                        400,
                        detail={
                            "ok": False,
                            "error": {
                                "code": "ITERATOR_NOT_SUPPORTED",
                                "message": (
                                    f"{ent_name} does not support iterator pagination. "
                                    f"Use max_returned to limit results instead."
                                ),
                            },
                        },
                    )
                if iterator_id.lower() == "start":
                    iterator = "Start"
                else:
                    iterator = "Continue"
                    iter_id = iterator_id

            # Only apply filters for new queries (not iterator Continue).
            # Transaction queries and list queries have different qbXML DTDs,
            # so the same logical filter maps to different elements.
            if iterator != "Continue":
                if name:
                    if ent_is_txn:
                        # RefNumberFilter lives in the same DTD branch as
                        # MaxReturned. A bare <RefNumber> is in a mutually
                        # exclusive branch and would invalidate the query.
                        filters["RefNumberFilter"] = {
                            "MatchCriterion": "Contains",
                            "RefNumber": name,
                        }
                    else:
                        filters["NameFilter"] = {"MatchCriterion": "Contains", "Name": name}

                # Transaction queries have no ActiveStatus element in the DTD.
                if active != "ActiveOnly" and not ent_is_txn:
                    filters["ActiveStatus"] = active

                if modified_after:
                    if ent_is_txn:
                        filters["ModifiedDateRangeFilter"] = {
                            "FromModifiedDate": modified_after
                        }
                    else:
                        filters["FromModifiedDate"] = modified_after

                # Filter transactions by the associated customer/job/vendor.
                # FullNameWithChildren matches the named entity *and* its
                # sub-entities, so a customer name also catches all its jobs.
                if entity_name and ent_is_txn:
                    filters["EntityFilter"] = {"FullNameWithChildren": entity_name}

                # TxnDate range filter — transactions only.
                if ent_is_txn and (from_date or to_date):
                    txn_range: dict = {}
                    if from_date:
                        txn_range["FromTxnDate"] = from_date
                    if to_date:
                        txn_range["ToTxnDate"] = to_date
                    filters["TxnDateRangeFilter"] = txn_range

            request_xml = xml_builder.query(
                ent_name,
                filters=filters,
                max_returned=max_returned,
                iterator=iterator,
                iterator_id=iter_id,
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
            request_xml = xml_builder.query(
                ent_name,
                filters=filters,
                include_line_items=ent_is_txn,
            )
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
