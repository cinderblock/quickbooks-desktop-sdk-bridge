"""Sub-typed entity routes must not be swallowed by their parent's get-by-id route.

``/api/v1/items/service`` was answered by ``/api/v1/items/{entity_id}`` (id
"service"): a 404 with no query string, and UNKNOWN_QUERY_PARAM with one. The
router now registers longer routes first.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from qb_bridge.api import deps

SERVICE_RS = """<?xml version="1.0"?>
<QBXML><QBXMLMsgsRs>
<ItemServiceQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
<ItemServiceRet><ListID>80000012-1624403077</ListID><Name>Labor</Name><FullName>Labor</FullName><IsActive>true</IsActive></ItemServiceRet>
</ItemServiceQueryRs>
</QBXMLMsgsRs></QBXML>"""


class TestSubTypedRoutes:
    async def test_items_service_lists_service_items(self, app, fake_qb_session):
        app.dependency_overrides[deps.require_api_key] = lambda: {
            "id": 1,
            "name": "t",
            "permissions": {"*": ["read"]},
        }
        fake_qb_session.response_xml = SERVICE_RS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
            r = await c.get("/api/v1/items/service", params={"max_returned": 5, "active": "All"})
        assert r.status_code == 200, r.text
        assert r.json()["data"][0]["Name"] == "Labor"
        # It reached the ItemService entity, not the generic Item one.
        assert "<ItemServiceQueryRq" in fake_qb_session.last_request_xml

    async def test_payroll_wage_is_reachable_too(self, app, fake_qb_session):
        app.dependency_overrides[deps.require_api_key] = lambda: {
            "id": 1,
            "name": "t",
            "permissions": {"*": ["read"]},
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
            r = await c.get("/api/v1/payroll-items/wage", params={"max_returned": 5})
        assert r.status_code == 200, r.text
        assert "<PayrollItemWageQueryRq" in fake_qb_session.last_request_xml
