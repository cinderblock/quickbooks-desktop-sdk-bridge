"""Time tracking, other names and wage payroll items.

These entities exist for timesheet integrations: a client lists the people
time can belong to (employees, vendors, other names), the payroll items it
can be paid under, and adds, amends, finds and deletes TimeTracking records.
TimeTracking queries differ from other transactions: no RefNumber, no line
items, and a TimeTrackingEntityFilter that matches an exact name.
"""

from __future__ import annotations

from tests.conftest import FakeQBSession

TIME_RET = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<{rs} requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <TimeTrackingRet>
        <TxnID>1F-1726500000</TxnID>
        <EditSequence>1726500001</EditSequence>
        <TxnDate>2026-09-16</TxnDate>
        <EntityRef><ListID>80000001-1</ListID><FullName>Alice A</FullName></EntityRef>
        <CustomerRef><ListID>80000010-1</ListID><FullName>Acme:Phase 2</FullName></CustomerRef>
        <Duration>PT1H35M0S</Duration>
        <Notes>Framing [ref 0123456789ab]</Notes>
        <BillableStatus>Billable</BillableStatus>
    </TimeTrackingRet>
</{rs}>
</QBXMLMsgsRs>
</QBXML>"""

OTHER_NAMES = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<OtherNameQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <OtherNameRet><ListID>O-1</ListID><Name>Temp Helper</Name><IsActive>true</IsActive></OtherNameRet>
</OtherNameQueryRs>
</QBXMLMsgsRs>
</QBXML>"""

WAGES = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<PayrollItemWageQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <PayrollItemWageRet><ListID>W-1</ListID><Name>Hourly</Name><IsActive>true</IsActive></PayrollItemWageRet>
    <PayrollItemWageRet><ListID>W-2</ListID><Name>Overtime</Name><IsActive>false</IsActive></PayrollItemWageRet>
</PayrollItemWageQueryRs>
</QBXMLMsgsRs>
</QBXML>"""

EDIT_SEQUENCE_STALE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<TimeTrackingModRs requestID="1" statusCode="3200" statusSeverity="Error"
    statusMessage="The provided edit sequence &quot;1&quot; is out-of-date." />
</QBXMLMsgsRs>
</QBXML>"""

NO_MATCH = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<TimeTrackingQueryRs requestID="1" statusCode="1" statusSeverity="Info"
    statusMessage="A query request did not find a matching object in QuickBooks" />
</QBXMLMsgsRs>
</QBXML>"""


def child_tags(el) -> list[str]:
    return [c.tag for c in el]


class TestTimeTrackingQueries:
    async def test_list_by_date_and_person(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = TIME_RET.format(rs="TimeTrackingQueryRs")
        resp = await client.get(
            "/api/v1/time-tracking?from_date=2026-09-16&to_date=2026-09-16"
            "&entity_name=Alice A&max_returned=500"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["TxnID"] == "1F-1726500000"
        assert body["data"][0]["EntityRef"]["ListID"] == "80000001-1"

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "TimeTrackingQueryRq"
        # DTD order: MaxReturned, then the date range, then the entity filter.
        assert child_tags(rq) == ["MaxReturned", "TxnDateRangeFilter", "TimeTrackingEntityFilter"]
        assert rq.find("TimeTrackingEntityFilter/FullName").text == "Alice A"
        assert rq.find("EntityFilter") is None
        assert rq.find("TxnDateRangeFilter/FromTxnDate").text == "2026-09-16"

    async def test_name_is_not_applicable(self, client, fake_qb_session: FakeQBSession):
        """TimeTracking has no RefNumber, so RefNumberFilter would be invalid qbXML."""
        resp = await client.get("/api/v1/time-tracking?name=1001")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "PARAM_NOT_APPLICABLE"
        assert fake_qb_session.requests == []

    async def test_active_is_not_applicable(self, client, fake_qb_session: FakeQBSession):
        resp = await client.get("/api/v1/time-tracking?active=All")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "PARAM_NOT_APPLICABLE"

    async def test_no_iterator(self, client, fake_qb_session: FakeQBSession):
        resp = await client.get("/api/v1/time-tracking?iterator_id=Start")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "ITERATOR_NOT_SUPPORTED"

    async def test_get_by_id_has_no_line_items(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = TIME_RET.format(rs="TimeTrackingQueryRs")
        resp = await client.get("/api/v1/time-tracking/1F-1726500000")
        assert resp.status_code == 200
        assert resp.json()["data"]["EditSequence"] == "1726500001"

        rq = fake_qb_session.find_request_element()
        assert child_tags(rq) == ["TxnID"]
        assert rq.find("TxnID").text == "1F-1726500000"

    async def test_get_missing_is_404(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = NO_MATCH
        resp = await client.get("/api/v1/time-tracking/nope")
        assert resp.status_code == 404

    async def test_entity_filter_unchanged_for_other_transactions(
        self, client, fake_qb_session: FakeQBSession
    ):
        resp = await client.get("/api/v1/invoices?entity_name=Acme&max_returned=10")
        assert resp.status_code == 200
        rq = fake_qb_session.find_request_element()
        assert rq.find("EntityFilter/FullNameWithChildren").text == "Acme"
        assert rq.find("TimeTrackingEntityFilter") is None


class TestTimeTrackingWrites:
    async def test_add_keeps_the_field_order_given(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = TIME_RET.format(rs="TimeTrackingAddRs")
        body = {
            "TxnDate": "2026-09-16",
            "EntityRef": {"ListID": "80000001-1"},
            "CustomerRef": {"ListID": "80000010-1"},
            "ItemServiceRef": {"ListID": "80000020-1"},
            "Duration": "PT1H35M0S",
            "PayrollItemWageRef": {"ListID": "W-1"},
            "Notes": "Framing & <trim> [ref 0123456789ab]",
            "BillableStatus": "Billable",
        }
        resp = await client.post("/api/v1/time-tracking", json=body)
        assert resp.status_code == 201
        assert resp.json()["data"]["TxnID"] == "1F-1726500000"

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "TimeTrackingAddRq"
        add = rq.find("TimeTrackingAdd")
        assert child_tags(add) == list(body)
        assert add.find("Notes").text == "Framing & <trim> [ref 0123456789ab]"

    async def test_mod_puts_id_and_version_first(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = TIME_RET.format(rs="TimeTrackingModRs")
        resp = await client.put(
            "/api/v1/time-tracking/1F-1726500000",
            json={
                "EditSequence": "1726500000",
                "TxnDate": "2026-09-16",
                "EntityRef": {"ListID": "80000001-1"},
                "Duration": "PT2H0M0S",
                "BillableStatus": "NotBillable",
            },
        )
        assert resp.status_code == 200
        mod = fake_qb_session.find_request_element().find("TimeTrackingMod")
        assert child_tags(mod) == [
            "TxnID",
            "EditSequence",
            "TxnDate",
            "EntityRef",
            "Duration",
            "BillableStatus",
        ]

    async def test_stale_edit_sequence_reports_the_status_code(
        self, client, fake_qb_session: FakeQBSession
    ):
        fake_qb_session.response_xml = EDIT_SEQUENCE_STALE
        resp = await client.put(
            "/api/v1/time-tracking/1F-1726500000",
            json={"EditSequence": "1", "Duration": "PT1H0M0S"},
        )
        assert resp.status_code == 502
        error = resp.json()["error"]
        assert error["qb_status_code"] == 3200
        assert "out-of-date" in error["message"]

    async def test_delete_is_a_timetracking_txn_delete(
        self, client, fake_qb_session: FakeQBSession
    ):
        fake_qb_session.response_xml = """<?xml version="1.0" ?>
<QBXML><QBXMLMsgsRs>
<TxnDelRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <TxnDelType>TimeTracking</TxnDelType><TxnID>1F-1726500000</TxnID>
</TxnDelRs>
</QBXMLMsgsRs></QBXML>"""
        resp = await client.delete("/api/v1/time-tracking/1F-1726500000")
        assert resp.status_code == 200
        rq = fake_qb_session.find_request_element()
        assert rq.tag == "TxnDelRq"
        assert rq.find("TxnDelType").text == "TimeTracking"
        assert rq.find("TxnID").text == "1F-1726500000"


class TestPeopleAndPayrollLists:
    async def test_other_names(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = OTHER_NAMES
        resp = await client.get("/api/v1/other-names?active=All&max_returned=5000")
        assert resp.status_code == 200
        assert resp.json()["data"] == [{"ListID": "O-1", "Name": "Temp Helper", "IsActive": "true"}]
        rq = fake_qb_session.find_request_element()
        assert rq.tag == "OtherNameQueryRq"
        assert child_tags(rq) == ["MaxReturned", "ActiveStatus"]

    async def test_wage_items(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = WAGES
        resp = await client.get("/api/v1/payroll-items/wage?active=All&max_returned=5000")
        assert resp.status_code == 200
        assert [w["Name"] for w in resp.json()["data"]] == ["Hourly", "Overtime"]
        assert fake_qb_session.find_request_element().tag == "PayrollItemWageQueryRq"

    async def test_wage_items_cannot_be_modified(self, client):
        resp = await client.put("/api/v1/payroll-items/wage/W-1", json={"EditSequence": "1"})
        assert resp.status_code == 405

    async def test_listed_in_discovery(self, client):
        resp = await client.get("/api/v1/entities")
        assert resp.status_code == 200
        body = resp.json()
        entities = body["data"] if isinstance(body, dict) and "data" in body else body
        by_name = {e["name"]: e for e in entities}
        assert by_name["TimeTracking"]["path"] == "/api/v1/time-tracking"
        assert by_name["TimeTracking"]["operations"] == {
            "list": True,
            "get": True,
            "create": True,
            "update": True,
            "delete": True,
        }
        assert by_name["PayrollItemWage"]["operations"]["update"] is False
        assert "OtherName" in by_name


class TestScopedKey:
    """A timesheet integration's key: read everything, write only time."""

    async def test_time_writes_allowed_other_writes_refused(self, app, fake_qb_session):
        from httpx import ASGITransport, AsyncClient

        from qb_bridge.api import deps

        app.dependency_overrides[deps.require_api_key] = lambda: {
            "id": 2,
            "name": "time-tracker",
            "permissions": {
                "*": ["read"],
                "TimeTracking": ["read", "write"],
                "Customer": ["read", "insert"],
            },
        }
        fake_qb_session.response_xml = TIME_RET.format(rs="TimeTrackingAddRs")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
            ok = await c.post("/api/v1/time-tracking", json={"TxnDate": "2026-09-16"})
            assert ok.status_code == 201
            refused = await c.post("/api/v1/invoices", json={"TxnDate": "2026-09-16"})
            assert refused.status_code == 403
            refused = await c.delete("/api/v1/customers/C-1")
            assert refused.status_code == 403
