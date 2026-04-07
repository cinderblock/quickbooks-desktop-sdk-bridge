"""Company information endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from qb_bridge.api.deps import get_qb_session, require_api_key, require_permission
from qb_bridge.qb import xml_builder, xml_parser
from qb_bridge.qb.session import QBSessionManager

router = APIRouter(prefix="/api/v1", tags=["Company"])


@router.get(
    "/company",
    summary="Get company information",
    description="Returns the company name, address, and other metadata from QuickBooks.",
)
async def get_company(
    session: QBSessionManager = Depends(get_qb_session),
    _key: dict = Depends(require_api_key),
    _perm=Depends(require_permission("Company", "get")),
):
    request_xml = xml_builder.build_request("CompanyQueryRq")
    response_xml = await session.execute(request_xml)
    data = xml_parser.parse_single_entity(response_xml, "Company")
    return {"ok": True, "data": data}


@router.get(
    "/company/preferences",
    summary="Get company preferences",
    description="Returns QuickBooks preference settings for the current company.",
)
async def get_preferences(
    session: QBSessionManager = Depends(get_qb_session),
    _key: dict = Depends(require_api_key),
    _perm=Depends(require_permission("Company", "get")),
):
    request_xml = xml_builder.build_request("PreferencesQueryRq")
    response_xml = await session.execute(request_xml)
    data = xml_parser.parse_single_entity(response_xml, "Preferences")
    return {"ok": True, "data": data}
