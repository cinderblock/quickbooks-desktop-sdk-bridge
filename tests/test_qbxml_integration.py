"""Integration tests for the full HTTP → qbXML pipeline.

These tests hit real FastAPI endpoints through ``httpx.AsyncClient``, but
use a ``FakeQBSession`` (see conftest.py) that captures the qbXML request
string instead of calling QuickBooks.  This lets us verify that each
combination of query parameters produces valid, correctly-ordered XML —
without a company file or QuickBooks Desktop.

Each test class corresponds to one of the original bug reports.
"""

from __future__ import annotations

from tests.conftest import (
    ACCOUNT_LIST_RESPONSE,
    CHECK_DETAIL_RESPONSE,
    CUSTOMER_LIST_ITERATOR_RESPONSE,
    REPORT_RESPONSE,
    FakeQBSession,
)

# ---------------------------------------------------------------------------
# Bug 1 — MaxReturned >= 100 must not inject iterator="Start"
# ---------------------------------------------------------------------------


class TestMaxReturnedPagination:
    """max_returned from 1–5000 should produce a plain query with
    ``<MaxReturned>N</MaxReturned>`` and **no** iterator attribute.
    """

    async def test_max_returned_99(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/accounts?max_returned=99")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "AccountQueryRq"
        assert rq.find("MaxReturned").text == "99"
        assert rq.get("iterator") is None

    async def test_max_returned_100(self, client, fake_qb_session: FakeQBSession):
        """The exact boundary that was broken before the fix."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/accounts?max_returned=100")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.find("MaxReturned").text == "100"
        assert rq.get("iterator") is None, "max_returned=100 must NOT force an iterator"

    async def test_max_returned_500(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/accounts?max_returned=500")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.find("MaxReturned").text == "500"
        assert rq.get("iterator") is None

    async def test_default_max_returned_is_100(self, client, fake_qb_session: FakeQBSession):
        """No max_returned param → defaults to 100 (no iterator)."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/accounts")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.find("MaxReturned").text == "100"
        assert rq.get("iterator") is None


# ---------------------------------------------------------------------------
# Bug 2 — Filter query parameters must produce correct, ordered XML
# ---------------------------------------------------------------------------


class TestFilterQueryParameters:
    """Every filter (name, active, modified_after) must produce valid
    qbXML with elements in the DTD-mandated order.
    """

    async def test_name_filter(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/accounts?name=Rent&max_returned=10")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        nf = rq.find("NameFilter")
        assert nf is not None, "NameFilter element missing"
        assert nf.find("MatchCriterion").text == "Contains"
        assert nf.find("Name").text == "Rent"

    async def test_active_status_filter(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/accounts?active=All&max_returned=10")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.find("ActiveStatus").text == "All"

    async def test_modified_after_filter(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get(
            "/api/v1/accounts?modified_after=2021-01-01T00:00:00&max_returned=10"
        )
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.find("FromModifiedDate").text == "2021-01-01T00:00:00"

    async def test_all_filters_together(self, client, fake_qb_session: FakeQBSession):
        """Combine all filters — the DTD order must be:
        MaxReturned → ActiveStatus → FromModifiedDate → NameFilter
        """
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get(
            "/api/v1/accounts?name=Rent&active=All&modified_after=2021-01-01T00:00:00&max_returned=10"
        )
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        tags = [child.tag for child in rq]

        assert "MaxReturned" in tags
        assert "ActiveStatus" in tags
        assert "FromModifiedDate" in tags
        assert "NameFilter" in tags

        # Verify DTD ordering
        assert tags.index("MaxReturned") < tags.index("ActiveStatus"), (
            "MaxReturned must precede ActiveStatus"
        )
        assert tags.index("ActiveStatus") < tags.index("FromModifiedDate"), (
            "ActiveStatus must precede FromModifiedDate"
        )
        assert tags.index("FromModifiedDate") < tags.index("NameFilter"), (
            "FromModifiedDate must precede NameFilter"
        )

    async def test_active_only_not_emitted(self, client, fake_qb_session: FakeQBSession):
        """ActiveOnly is the QB default — it should NOT be in the XML."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/accounts?active=ActiveOnly&max_returned=10")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.find("ActiveStatus") is None, (
            "ActiveStatus=ActiveOnly should be omitted (it's the default)"
        )

    async def test_transaction_name_uses_refnumber_filter(self, client, fake_qb_session: FakeQBSession):
        """For transaction entities, 'name' maps to RefNumberFilter (which can
        coexist with MaxReturned), not a bare RefNumber (which cannot)."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE  # Good enough shape
        resp = await client.get("/api/v1/invoices?name=1001&max_returned=10")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "InvoiceQueryRq"
        assert rq.find("RefNumber") is None, (
            "bare RefNumber conflicts with MaxReturned in the DTD"
        )
        nf = rq.find("RefNumberFilter")
        assert nf is not None
        assert nf.find("MatchCriterion").text == "Contains"
        assert nf.find("RefNumber").text == "1001"
        assert rq.find("NameFilter") is None
        # Both filter and page size present and correctly ordered
        tags = [child.tag for child in rq]
        assert tags.index("MaxReturned") < tags.index("RefNumberFilter")


# ---------------------------------------------------------------------------
# Bug 6 — Transaction filters must produce transaction-specific qbXML
# (regression for the "list endpoints can't reach recent transactions" report)
# ---------------------------------------------------------------------------


class TestTransactionFilters:
    """Transaction queries (Check, Bill, JournalEntry, ...) have a different
    qbXML DTD from list queries. Filters must use the transaction variants or
    QuickBooks rejects the whole request with a parse error (HTTP 502).
    """

    async def test_active_rejected_for_transactions(
        self, client, fake_qb_session: FakeQBSession
    ):
        """ActiveStatus does not exist on transaction queries. Rather than
        silently dropping the filter, the request must be rejected."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/checks?active=All&max_returned=10")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "PARAM_NOT_APPLICABLE"

    async def test_active_default_ok_for_transactions(
        self, client, fake_qb_session: FakeQBSession
    ):
        """Not sending 'active' at all must still work for transactions."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/checks?max_returned=10")
        assert resp.status_code == 200
        assert fake_qb_session.find_request_element().find("ActiveStatus") is None

    async def test_modified_after_uses_range_filter_for_transactions(
        self, client, fake_qb_session: FakeQBSession
    ):
        """modified_after must be wrapped in ModifiedDateRangeFilter for txns,
        not emitted as a bare FromModifiedDate."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get(
            "/api/v1/checks?modified_after=2026-01-01T00:00:00&max_returned=10"
        )
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.find("FromModifiedDate") is None, "bare FromModifiedDate is invalid for txns"
        mrf = rq.find("ModifiedDateRangeFilter")
        assert mrf is not None
        assert mrf.find("FromModifiedDate").text == "2026-01-01T00:00:00"

    async def test_txn_date_range_filter(self, client, fake_qb_session: FakeQBSession):
        """from_date/to_date must produce a TxnDateRangeFilter so recent
        transactions are reachable."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get(
            "/api/v1/checks?from_date=2026-01-01&to_date=2026-12-31&max_returned=10"
        )
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        tdr = rq.find("TxnDateRangeFilter")
        assert tdr is not None
        assert tdr.find("FromTxnDate").text == "2026-01-01"
        assert tdr.find("ToTxnDate").text == "2026-12-31"
        # DTD order: MaxReturned must precede the range filter
        tags = [child.tag for child in rq]
        assert tags.index("MaxReturned") < tags.index("TxnDateRangeFilter")

    async def test_txn_date_from_only(self, client, fake_qb_session: FakeQBSession):
        """Only from_date supplied — ToTxnDate must be absent."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/checks?from_date=2026-01-01&max_returned=10")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        tdr = rq.find("TxnDateRangeFilter")
        assert tdr.find("FromTxnDate").text == "2026-01-01"
        assert tdr.find("ToTxnDate") is None

    async def test_date_params_rejected_for_list_entities(
        self, client, fake_qb_session: FakeQBSession
    ):
        """List entities have no TxnDate — from_date/to_date must be rejected,
        not silently ignored."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/accounts?from_date=2026-01-01&max_returned=10")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "PARAM_NOT_APPLICABLE"

    async def test_entity_name_filter_for_transactions(
        self, client, fake_qb_session: FakeQBSession
    ):
        """entity_name must produce an EntityFilter using FullNameWithChildren,
        so a customer name also matches all of that customer's jobs. (A nested
        NameFilter is rejected by QuickBooks for transaction queries.)"""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get(
            "/api/v1/checks?entity_name=Acme Plumbing Services (AP)&max_returned=10"
        )
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        ef = rq.find("EntityFilter")
        assert ef is not None
        assert ef.find("FullNameWithChildren").text == "Acme Plumbing Services (AP)"
        assert ef.find("NameFilter") is None
        # DTD order: MaxReturned precedes EntityFilter
        tags = [child.tag for child in rq]
        assert tags.index("MaxReturned") < tags.index("EntityFilter")

    async def test_entity_name_rejected_for_list_entities(
        self, client, fake_qb_session: FakeQBSession
    ):
        """List entities have no EntityFilter — entity_name must be rejected."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/customers?entity_name=Acme&max_returned=10")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "PARAM_NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# Bug 3 — Iterator pagination (Start / Continue / iteratorID)
# ---------------------------------------------------------------------------


class TestIteratorPagination:
    """The QB SDK iterator protocol requires:
    - ``iterator="Start"`` as an XML *attribute* on the ``*QueryRq`` element
    - ``iterator="Continue" iteratorID="..."`` on subsequent requests
    - ``iteratorID`` and ``iteratorRemainingCount`` extracted from the response

    Only certain entity types support iterators in the qbXML DTD.
    Customer supports it; Account does not.
    """

    async def test_iterator_start(self, client, fake_qb_session: FakeQBSession):
        """iterator_id=Start must produce iterator='Start' attribute."""
        fake_qb_session.response_xml = CUSTOMER_LIST_ITERATOR_RESPONSE
        resp = await client.get("/api/v1/customers?max_returned=10&iterator_id=Start")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "CustomerQueryRq"
        assert rq.get("iterator") == "Start", "Should be iterator='Start' attribute"
        assert rq.get("iteratorID") is None, "Start request must not have iteratorID"
        assert rq.find("MaxReturned").text == "10"

    async def test_iterator_start_case_insensitive(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = CUSTOMER_LIST_ITERATOR_RESPONSE
        resp = await client.get("/api/v1/customers?max_returned=10&iterator_id=start")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.get("iterator") == "Start"

    async def test_iterator_continue(self, client, fake_qb_session: FakeQBSession):
        """A real iteratorID must produce iterator='Continue' + iteratorID attr."""
        fake_qb_session.response_xml = CUSTOMER_LIST_ITERATOR_RESPONSE
        resp = await client.get(
            "/api/v1/customers?max_returned=10&iterator_id={iter-abc-123}"
        )
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.get("iterator") == "Continue"
        assert rq.get("iteratorID") == "{iter-abc-123}"

    async def test_iterator_response_includes_meta(self, client, fake_qb_session: FakeQBSession):
        """Response meta must include iterator_id and remaining count."""
        fake_qb_session.response_xml = CUSTOMER_LIST_ITERATOR_RESPONSE
        resp = await client.get("/api/v1/customers?max_returned=10&iterator_id=Start")
        data = resp.json()

        assert data["meta"]["iterator_id"] == "{iter-abc-123}"
        assert data["meta"]["remaining"] == 42

    async def test_no_iterator_without_param(self, client, fake_qb_session: FakeQBSession):
        """Regular queries (no iterator_id param) must NOT have iterator attrs."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/accounts?max_returned=200")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.get("iterator") is None
        assert rq.get("iteratorID") is None

    async def test_iterator_rejected_for_unsupported_entity(
        self, client, fake_qb_session: FakeQBSession
    ):
        """Account does not support iterators — should return 400, not an XML error."""
        resp = await client.get("/api/v1/accounts?max_returned=10&iterator_id=Start")
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["error"]["code"] == "ITERATOR_NOT_SUPPORTED"
        assert "Account" in data["detail"]["error"]["message"]

    async def test_iterator_on_transaction_entity(self, client, fake_qb_session: FakeQBSession):
        """Transaction entities like Invoice support iterators."""
        fake_qb_session.response_xml = CUSTOMER_LIST_ITERATOR_RESPONSE
        resp = await client.get("/api/v1/invoices?max_returned=10&iterator_id=Start")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "InvoiceQueryRq"
        assert rq.get("iterator") == "Start"


# ---------------------------------------------------------------------------
# Unknown query parameters must be rejected, never silently ignored
# ---------------------------------------------------------------------------


class TestUnknownQueryParams:
    """A query parameter the endpoint doesn't declare must produce a 400, so a
    client typo or unsupported option fails loudly instead of doing nothing."""

    async def test_unknown_param_on_list(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/checks?sort=date&max_returned=10")
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "UNKNOWN_QUERY_PARAM"
        assert "sort" in body["error"]["message"]

    async def test_reported_noop_params_now_rejected(
        self, client, fake_qb_session: FakeQBSession
    ):
        """The exact params the bug report said were silently ignored."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        for bad in ("sort=x", "order=desc", "txn_date_from=2026-01-01", "from_date2=x"):
            resp = await client.get(f"/api/v1/checks?{bad}&max_returned=10")
            assert resp.status_code == 400, f"{bad} should be rejected"
            assert resp.json()["error"]["code"] == "UNKNOWN_QUERY_PARAM"

    async def test_unknown_param_on_report(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = REPORT_RESPONSE
        resp = await client.get("/api/v1/reports/profit-and-loss?bogus=1")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "UNKNOWN_QUERY_PARAM"

    async def test_known_params_still_accepted(self, client, fake_qb_session: FakeQBSession):
        """Sanity: a fully-valid request is unaffected by the strict check."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get(
            "/api/v1/checks?from_date=2026-01-01&to_date=2026-12-31"
            "&entity_name=Acme&name=1001&max_returned=10"
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Bug 4 — Report endpoints must generate valid qbXML
# ---------------------------------------------------------------------------


class TestReportEndpoints:
    """Report queries need:
    - Category-specific type element (``<GeneralSummaryReportType>``, not ``<ReportType>``)
    - Correct DTD element ordering
    """

    async def test_profit_and_loss(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = REPORT_RESPONSE
        resp = await client.get("/api/v1/reports/profit-and-loss")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "GeneralSummaryReportQueryRq"
        assert rq.find("GeneralSummaryReportType").text == "ProfitAndLossStandard"
        assert rq.find("ReportType") is None, "Must NOT use generic <ReportType>"

    async def test_general_ledger_detail_report(self, client, fake_qb_session: FakeQBSession):
        # Use a detail-category response
        detail_response = REPORT_RESPONSE.replace(
            "GeneralSummaryReportQueryRs", "GeneralDetailReportQueryRs"
        )
        fake_qb_session.response_xml = detail_response
        resp = await client.get("/api/v1/reports/general-ledger")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "GeneralDetailReportQueryRq"
        assert rq.find("GeneralDetailReportType").text == "GeneralLedger"
        assert rq.find("ReportType") is None

    async def test_aging_report(self, client, fake_qb_session: FakeQBSession):
        aging_response = REPORT_RESPONSE.replace(
            "GeneralSummaryReportQueryRs", "AgingReportQueryRs"
        )
        fake_qb_session.response_xml = aging_response
        resp = await client.get("/api/v1/reports/ar-aging-summary")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "AgingReportQueryRq"
        assert rq.find("AgingReportType").text == "ARAgingSummary"

    async def test_report_with_date_macro(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = REPORT_RESPONSE
        resp = await client.get("/api/v1/reports/profit-and-loss?date_macro=ThisYear")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.find("ReportDateMacro").text == "ThisYear"

    async def test_report_with_date_range(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = REPORT_RESPONSE
        resp = await client.get(
            "/api/v1/reports/profit-and-loss?from_date=2024-01-01&to_date=2024-12-31"
        )
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        period = rq.find("ReportPeriod")
        assert period is not None
        assert period.find("FromReportDate").text == "2024-01-01"
        assert period.find("ToReportDate").text == "2024-12-31"

    async def test_report_entity_filter(self, client, fake_qb_session: FakeQBSession):
        """entity= must add a ReportEntityFilter (FullNameWithChildren) so
        detail reports can be scoped to a customer/job, including sub-jobs."""
        fake_qb_session.response_xml = REPORT_RESPONSE
        resp = await client.get(
            "/api/v1/reports/profit-and-loss-detail"
            "?entity=Acme Plumbing Services (AP)&from_date=2026-01-01&to_date=2026-12-31"
        )
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        ef = rq.find("ReportEntityFilter")
        assert ef is not None
        assert ef.find("FullNameWithChildren").text == "Acme Plumbing Services (AP)"
        # DTD order: ReportPeriod precedes ReportEntityFilter
        tags = [child.tag for child in rq]
        assert tags.index("ReportPeriod") < tags.index("ReportEntityFilter")

    async def test_report_no_entity_filter_by_default(
        self, client, fake_qb_session: FakeQBSession
    ):
        """No entity param → no ReportEntityFilter element."""
        fake_qb_session.response_xml = REPORT_RESPONSE
        resp = await client.get("/api/v1/reports/profit-and-loss-detail")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.find("ReportEntityFilter") is None

    async def test_report_basis_after_summarize_by(self, client, fake_qb_session: FakeQBSession):
        """SummarizeColumnsBy must precede ReportBasis in the DTD."""
        fake_qb_session.response_xml = REPORT_RESPONSE
        resp = await client.get(
            "/api/v1/reports/profit-and-loss?basis=Accrual&summarize_by=Month"
        )
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        tags = [child.tag for child in rq]
        assert "SummarizeColumnsBy" in tags
        assert "ReportBasis" in tags
        assert tags.index("SummarizeColumnsBy") < tags.index("ReportBasis")

    async def test_report_json_response_shape(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = REPORT_RESPONSE
        resp = await client.get("/api/v1/reports/profit-and-loss")
        data = resp.json()

        assert data["ok"] is True
        assert data["data"]["title"] == "Profit & Loss"
        assert len(data["data"]["columns"]) == 2
        assert len(data["data"]["rows"]) >= 1

    async def test_unknown_report_slug(self, client, fake_qb_session: FakeQBSession):
        resp = await client.get("/api/v1/reports/nonexistent-report")
        data = resp.json()
        assert data["ok"] is False
        assert data["error"]["code"] == "UNKNOWN_REPORT"


# ---------------------------------------------------------------------------
# Bug 5 — Transaction get-by-ID must include line items
# ---------------------------------------------------------------------------


class TestTransactionLineItems:
    """GET /checks/{id} (and other txn entities) should include
    ``<IncludeLineItems>true</IncludeLineItems>`` so QB returns
    ExpenseLineRet / ItemLineRet arrays.
    """

    async def test_check_get_includes_line_items(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = CHECK_DETAIL_RESPONSE
        resp = await client.get("/api/v1/checks/85-1613406293")
        assert resp.status_code == 200

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "CheckQueryRq"
        assert rq.find("TxnID").text == "85-1613406293"
        ili = rq.find("IncludeLineItems")
        assert ili is not None, "Transaction GET must include <IncludeLineItems>"
        assert ili.text == "true"

    async def test_check_response_has_expense_lines(self, client, fake_qb_session: FakeQBSession):
        """Verify that line items from the response are serialized in JSON."""
        fake_qb_session.response_xml = CHECK_DETAIL_RESPONSE
        resp = await client.get("/api/v1/checks/85-1613406293")
        data = resp.json()["data"]

        assert "ExpenseLineRet" in data
        lines = data["ExpenseLineRet"]
        assert isinstance(lines, list)
        assert len(lines) == 2
        assert lines[0]["AccountRef"]["FullName"] == "Office Supplies"
        assert lines[1]["Amount"] == "-500.00"

    async def test_invoice_get_includes_line_items(self, client, fake_qb_session: FakeQBSession):
        """All transaction entities (not just Check) should request line items."""
        # Use a generic OK response — we just need to verify the request XML
        await client.get("/api/v1/invoices/TXN-999")

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "InvoiceQueryRq"
        assert rq.find("IncludeLineItems").text == "true"

    async def test_account_get_does_not_include_line_items(
        self, client, fake_qb_session: FakeQBSession
    ):
        """List entities (Account, Customer, etc.) should NOT include IncludeLineItems."""
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        await client.get("/api/v1/accounts/80000001-1234567890")

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "AccountQueryRq"
        assert rq.find("IncludeLineItems") is None, (
            "List entity GET must NOT include <IncludeLineItems>"
        )

    async def test_include_line_items_dtd_order(self, client, fake_qb_session: FakeQBSession):
        """IncludeLineItems must come after TxnID in DTD order."""
        fake_qb_session.response_xml = CHECK_DETAIL_RESPONSE
        await client.get("/api/v1/checks/85-1613406293")

        rq = fake_qb_session.find_request_element()
        tags = [child.tag for child in rq]
        assert tags.index("TxnID") < tags.index("IncludeLineItems")


# ---------------------------------------------------------------------------
# Regression — basic list endpoint still works end-to-end
# ---------------------------------------------------------------------------


class TestBasicListEndpoint:
    """Sanity checks: the list endpoint works and returns correct shape."""

    async def test_list_returns_data(self, client, fake_qb_session: FakeQBSession):
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        resp = await client.get("/api/v1/accounts")
        data = resp.json()

        assert data["ok"] is True
        assert len(data["data"]) == 2
        assert data["data"][0]["Name"] == "Checking"
        assert data["meta"]["count"] == 2

    async def test_list_generates_correct_query_tag(
        self, client, fake_qb_session: FakeQBSession
    ):
        fake_qb_session.response_xml = ACCOUNT_LIST_RESPONSE
        await client.get("/api/v1/customers")

        rq = fake_qb_session.find_request_element()
        assert rq.tag == "CustomerQueryRq"
