"""Tests for qbXML response parser."""

import pytest

from qb_bridge.qb.exceptions import QBRequestError
from qb_bridge.qb.xml_parser import (
    check_status,
    parse_entity_list,
    parse_report,
    parse_response,
    parse_single_entity,
)

SAMPLE_ACCOUNT_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<AccountQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <AccountRet>
        <ListID>80000001-1234567890</ListID>
        <TimeCreated>2024-01-01T00:00:00-08:00</TimeCreated>
        <TimeModified>2024-06-15T10:30:00-08:00</TimeModified>
        <EditSequence>1234567890</EditSequence>
        <Name>Checking</Name>
        <FullName>Checking</FullName>
        <AccountType>Bank</AccountType>
        <Balance>15000.00</Balance>
    </AccountRet>
    <AccountRet>
        <ListID>80000002-1234567890</ListID>
        <Name>Accounts Receivable</Name>
        <FullName>Accounts Receivable</FullName>
        <AccountType>AccountsReceivable</AccountType>
        <Balance>5000.00</Balance>
    </AccountRet>
</AccountQueryRs>
</QBXMLMsgsRs>
</QBXML>"""


SAMPLE_ERROR_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<CustomerQueryRs requestID="1" statusCode="500" statusSeverity="Error" statusMessage="Could not find the specified record.">
</CustomerQueryRs>
</QBXMLMsgsRs>
</QBXML>"""


SAMPLE_ADD_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<CustomerAddRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <CustomerRet>
        <ListID>NEW-001</ListID>
        <EditSequence>9999</EditSequence>
        <Name>New Customer</Name>
    </CustomerRet>
</CustomerAddRs>
</QBXMLMsgsRs>
</QBXML>"""


SAMPLE_REPORT_RESPONSE = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<GeneralSummaryReportQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
    <ReportRet>
        <ReportTitle>Profit &amp; Loss</ReportTitle>
        <ReportSubtitle>January 2024</ReportSubtitle>
        <ReportBasis>Accrual</ReportBasis>
        <NumRows>2</NumRows>
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
            <DataRow>
                <ColData colID="1" value="Expenses" />
                <ColData colID="2" value="30000.00" />
            </DataRow>
            <TotalRow>
                <ColData colID="1" value="Net Income" />
                <ColData colID="2" value="20000.00" />
            </TotalRow>
        </ReportData>
    </ReportRet>
</GeneralSummaryReportQueryRs>
</QBXMLMsgsRs>
</QBXML>"""


class TestCheckStatus:
    def test_success(self):
        code, severity, message = check_status(SAMPLE_ACCOUNT_RESPONSE)
        assert code == 0
        assert severity == "Info"
        assert message == "Status OK"

    def test_error_raises(self):
        with pytest.raises(QBRequestError) as exc_info:
            check_status(SAMPLE_ERROR_RESPONSE)
        assert exc_info.value.qb_status_code == 500
        assert "not find" in str(exc_info.value)

    def test_error_on_second_message_is_not_swallowed(self):
        """A batched response where the FIRST message is OK but a later one is
        an Error must still raise — not silently report success."""
        batched = """<?xml version="1.0" ?>
<QBXML>
<QBXMLMsgsRs>
<CustomerQueryRs requestID="1" statusCode="0" statusSeverity="Info" statusMessage="Status OK">
</CustomerQueryRs>
<InvoiceQueryRs requestID="2" statusCode="3100" statusSeverity="Error" statusMessage="Boom on the second request.">
</InvoiceQueryRs>
</QBXMLMsgsRs>
</QBXML>"""
        with pytest.raises(QBRequestError) as exc_info:
            check_status(batched)
        assert exc_info.value.qb_status_code == 3100
        assert "Boom" in str(exc_info.value)
        # parse_response must reject it too
        with pytest.raises(QBRequestError):
            parse_response(batched)


class TestParseResponse:
    def test_parse_list(self):
        resp = parse_response(SAMPLE_ACCOUNT_RESPONSE)
        assert resp.status_code == 0
        assert resp.ret_count == 2
        assert isinstance(resp.data, list)
        assert resp.data[0]["Name"] == "Checking"
        assert resp.data[1]["AccountType"] == "AccountsReceivable"

    def test_parse_single(self):
        resp = parse_response(SAMPLE_ADD_RESPONSE)
        assert resp.ret_count == 1
        assert isinstance(resp.data, dict)
        assert resp.data["ListID"] == "NEW-001"


class TestParseEntityList:
    def test_list(self):
        items = parse_entity_list(SAMPLE_ACCOUNT_RESPONSE, "Account")
        assert len(items) == 2
        assert items[0]["FullName"] == "Checking"


class TestParseSingleEntity:
    def test_single(self):
        item = parse_single_entity(SAMPLE_ADD_RESPONSE, "Customer")
        assert item is not None
        assert item["Name"] == "New Customer"

    def test_from_list(self):
        item = parse_single_entity(SAMPLE_ACCOUNT_RESPONSE, "Account")
        assert item is not None
        assert item["Name"] == "Checking"


class TestParseReport:
    def test_parse_columns(self):
        report = parse_report(SAMPLE_REPORT_RESPONSE)
        assert report.title == "Profit & Loss"
        assert report.subtitle == "January 2024"
        assert report.basis == "Accrual"
        assert len(report.columns) == 2
        assert report.columns[0].title == "Account"
        assert report.columns[1].title == "Amount"

    def test_parse_rows(self):
        report = parse_report(SAMPLE_REPORT_RESPONSE)
        assert len(report.rows) == 3  # 2 data + 1 total
        assert report.rows[0].row_type == "DataRow"
        assert report.rows[0].values[1] == "Income"
        assert report.rows[0].values[2] == "50000.00"
        assert report.rows[2].row_type == "TotalRow"
        assert report.rows[2].values[2] == "20000.00"
