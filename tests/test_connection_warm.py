"""Warming the QuickBooks connection before it is needed."""

from __future__ import annotations

import asyncio

from httpx import ASGITransport, AsyncClient

from qb_bridge.api import deps

HOST_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<HostQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <HostRet>
        <ProductName>QuickBooks Desktop Pro 2021</ProductName>
        <MajorVersion>31</MajorVersion>
        <Country>US</Country>
        <QBFileMode>SingleUser</QBFileMode>
    </HostRet>
</HostQueryRs>
</QBXMLMsgsRs>
</QBXML>"""


class TestWarmUp:
    async def test_warm_opens_the_session_and_reports_quickbooks(self, client, fake_qb_session):
        fake_qb_session.response_xml = HOST_RESPONSE

        r = await client.post("/api/v1/connection/warm")

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["state"] == "connected"
        assert data["quickbooks"]["product"] == "QuickBooks Desktop Pro 2021"
        assert data["quickbooks"]["version"] == "31"
        assert isinstance(data["elapsed_seconds"], float)

    async def test_warm_uses_the_cheapest_real_round_trip(self, client, fake_qb_session):
        """A process check would lie; only a real request proves the path works."""
        fake_qb_session.response_xml = HOST_RESPONSE

        await client.post("/api/v1/connection/warm")

        assert "<HostQueryRq" in fake_qb_session.last_request_xml
        assert fake_qb_session.last_idempotent is True

    async def test_warm_reports_how_long_it_stays_warm(self, client, fake_qb_session):
        """Callers need to know how often to touch it."""
        fake_qb_session.response_xml = HOST_RESPONSE
        fake_qb_session.idle_timeout = 600

        r = await client.post("/api/v1/connection/warm")

        assert r.json()["data"]["stays_warm_for_seconds"] == 600

    async def test_wait_false_returns_at_once(self, app, fake_qb_session):
        """The background mode must not block on a cold QuickBooks."""
        fake_qb_session.response_xml = HOST_RESPONSE
        release = asyncio.Event()

        async def slow_execute(qbxml, timeout=None, *, idempotent=False):
            await release.wait()
            return HOST_RESPONSE

        fake_qb_session.execute = slow_execute

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            r = await asyncio.wait_for(
                c.post("/api/v1/connection/warm", params={"wait": "false"}), timeout=2
            )

            assert r.status_code == 202
            assert r.json()["data"]["state"] == "warming"
            assert r.json()["data"]["already_warming"] is False

            # A second call while the first is still running doesn't pile on.
            second = await c.post("/api/v1/connection/warm", params={"wait": "false"})
            assert second.json()["data"]["already_warming"] is True

        release.set()
        await asyncio.sleep(0)

    async def test_a_read_only_key_may_warm_the_connection(self, app, fake_qb_session):
        """Warming touches no company data; read-only clients need it most."""
        fake_qb_session.response_xml = HOST_RESPONSE
        app.dependency_overrides[deps.require_api_key] = lambda: {
            "id": 1,
            "name": "readonly",
            "permissions": {"*": ["read"]},
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            r = await c.post("/api/v1/connection/warm")

        assert r.status_code == 200, r.text

    async def test_unknown_query_param_is_rejected(self, client, fake_qb_session):
        fake_qb_session.response_xml = HOST_RESPONSE
        r = await client.post("/api/v1/connection/warm", params={"waitt": "false"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "UNKNOWN_QUERY_PARAM"
