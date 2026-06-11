"""Pytest configuration and shared fixtures.

The ``app`` and ``client`` fixtures create a fully-wired FastAPI application
with a **fake** QuickBooks session.  Instead of launching a COM subprocess,
``FakeQBSession.execute()`` records every qbXML request it receives and
returns a canned XML response.  This lets us integration-test the full
route-handler → XML-builder pipeline without a company file.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from lxml import etree

from qb_bridge.config import Settings
from qb_bridge.main import create_app

# ---------------------------------------------------------------------------
# Fake QB session — captures XML, returns canned responses
# ---------------------------------------------------------------------------


class FakeQBSession:
    """Drop-in replacement for ``QBSessionManager``.

    Attributes:
        last_request_xml: The raw qbXML string from the most recent ``execute()`` call.
        requests: List of *all* qbXML strings sent during the test.
        response_xml: The canned XML string to return from ``execute()``.
                      Override per-test via ``fake_qb_session.response_xml = ...``
    """

    def __init__(self) -> None:
        self.last_request_xml: str = ""
        self.requests: list[str] = []
        self.response_xml: str = _EMPTY_OK_RESPONSE

    @property
    def state(self) -> str:
        return "connected"

    @property
    def idle_seconds(self) -> float:
        return 0.0

    async def start(self) -> None:  # noqa: D102
        pass

    async def stop(self) -> None:  # noqa: D102
        pass

    async def execute(self, qbxml: str) -> str:
        """Record the request and return the canned response."""
        self.last_request_xml = qbxml
        self.requests.append(qbxml)
        return self.response_xml

    # -- Helpers for assertions in tests --

    def parse_last_request(self) -> etree._Element:
        """Parse ``last_request_xml`` and return the root ``<QBXML>`` element."""
        xml = self.last_request_xml
        # Strip processing instructions so lxml can parse
        lines = [ln for ln in xml.splitlines() if not ln.strip().startswith("<?")]
        return etree.fromstring("\n".join(lines).encode())

    def find_request_element(self) -> etree._Element:
        """Return the first ``*Rq`` element inside ``<QBXMLMsgsRq>``."""
        root = self.parse_last_request()
        msgs = root.find("QBXMLMsgsRq")
        assert msgs is not None, "No QBXMLMsgsRq in captured XML"
        return msgs[0]


# Canned qbXML responses -------------------------------------------------------

_EMPTY_OK_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<QueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
</QueryRs>
</QBXMLMsgsRs>
</QBXML>"""

ACCOUNT_LIST_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<AccountQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <AccountRet>
        <ListID>80000001-1234567890</ListID>
        <Name>Checking</Name>
        <AccountType>Bank</AccountType>
        <Balance>15000.00</Balance>
    </AccountRet>
    <AccountRet>
        <ListID>80000002-1234567890</ListID>
        <Name>Savings</Name>
        <AccountType>Bank</AccountType>
        <Balance>25000.00</Balance>
    </AccountRet>
</AccountQueryRs>
</QBXMLMsgsRs>
</QBXML>"""

ACCOUNT_LIST_ITERATOR_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<AccountQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK"
    iteratorID="{iter-abc-123}" iteratorRemainingCount="42">
    <AccountRet>
        <ListID>80000001-1234567890</ListID>
        <Name>Checking</Name>
    </AccountRet>
</AccountQueryRs>
</QBXMLMsgsRs>
</QBXML>"""

CUSTOMER_LIST_ITERATOR_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<CustomerQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK"
    iteratorID="{iter-abc-123}" iteratorRemainingCount="42">
    <CustomerRet>
        <ListID>80000001-1611594109</ListID>
        <Name>Acme Corp</Name>
    </CustomerRet>
</CustomerQueryRs>
</QBXMLMsgsRs>
</QBXML>"""

CHECK_DETAIL_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<CheckQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <CheckRet>
        <TxnID>85-1613406293</TxnID>
        <TxnDate>2024-06-01</TxnDate>
        <Amount>-1500.00</Amount>
        <PayeeEntityRef>
            <ListID>V-001</ListID>
            <FullName>Office Supplies Inc</FullName>
        </PayeeEntityRef>
        <AccountRef>
            <ListID>A-001</ListID>
            <FullName>Checking</FullName>
        </AccountRef>
        <ExpenseLineRet>
            <TxnLineID>LN-001</TxnLineID>
            <AccountRef>
                <ListID>A-010</ListID>
                <FullName>Office Supplies</FullName>
            </AccountRef>
            <Amount>-1000.00</Amount>
        </ExpenseLineRet>
        <ExpenseLineRet>
            <TxnLineID>LN-002</TxnLineID>
            <AccountRef>
                <ListID>A-011</ListID>
                <FullName>Postage</FullName>
            </AccountRef>
            <Amount>-500.00</Amount>
        </ExpenseLineRet>
    </CheckRet>
</CheckQueryRs>
</QBXMLMsgsRs>
</QBXML>"""

REPORT_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<GeneralSummaryReportQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <ReportRet>
        <ReportTitle>Profit &amp; Loss</ReportTitle>
        <ReportSubtitle>January 2024</ReportSubtitle>
        <ReportBasis>Accrual</ReportBasis>
        <NumRows>1</NumRows>
        <NumColumns>2</NumColumns>
        <NumColTitleRows>1</NumColTitleRows>
        <ColDesc colID="1" dataType="String" colType="Account">
            <ColTitle titleRow="1" value="Account" />
        </ColDesc>
        <ColDesc colID="2" dataType="Amount" colType="Amount">
            <ColTitle titleRow="1" value="Amount" />
        </ColDesc>
        <ReportData>
            <DataRow>
                <ColData colID="1" value="Income" />
                <ColData colID="2" value="50000.00" />
            </DataRow>
        </ReportData>
    </ReportRet>
</GeneralSummaryReportQueryRs>
</QBXMLMsgsRs>
</QBXML>"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_qb_session() -> FakeQBSession:
    """A fresh ``FakeQBSession`` for each test."""
    return FakeQBSession()


@pytest.fixture()
async def app(fake_qb_session, tmp_path):
    """Create the FastAPI app with all QB/auth dependencies stubbed out.

    The IP-filter middleware is bypassed because httpx's ASGI transport uses
    ``127.0.0.1`` as the client address (which passes the private-IP check).
    """
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "test.db",
        log_dir=tmp_path / "logs",
        auto_launch_qb=False,
        company_file="",
    )
    application = create_app(settings)

    # Inject the fake QB session and a permissive API-key validator
    from qb_bridge.api import deps

    application.dependency_overrides[deps.get_qb_session] = lambda: fake_qb_session
    application.dependency_overrides[deps.require_api_key] = lambda: {
        "id": 1,
        "name": "test-key",
        "permissions": {"*": ["list", "get", "create", "update", "delete"]},
    }

    return application


@pytest.fixture()
async def client(app) -> AsyncClient:
    """httpx async client wired to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
