"""Tests for qbXML request builder."""

from lxml import etree

from qb_bridge.qb import xml_builder


def _parse(xml_str: str) -> etree._Element:
    """Parse XML string, stripping the processing instructions."""
    # Remove the PI lines to parse cleanly
    clean = xml_str
    for line in xml_str.splitlines():
        if line.strip().startswith("<?"):
            clean = clean.replace(line + "\n", "")
    return etree.fromstring(clean.encode())


class TestBuildRequest:
    def test_simple_query(self):
        xml = xml_builder.build_request("AccountQueryRq")
        assert "AccountQueryRq" in xml
        assert '<?qbxml version="13.0"?>' in xml
        root = _parse(xml)
        assert root.tag == "QBXML"
        msgs = root.find("QBXMLMsgsRq")
        assert msgs is not None
        assert msgs.get("onError") == "stopOnError"

    def test_request_with_body(self):
        xml = xml_builder.build_request(
            "CustomerQueryRq",
            body={"MaxReturned": "5", "ActiveStatus": "ActiveOnly"},
        )
        root = _parse(xml)
        rq = root.find(".//CustomerQueryRq")
        assert rq is not None
        assert rq.find("MaxReturned").text == "5"
        assert rq.find("ActiveStatus").text == "ActiveOnly"

    def test_nested_body(self):
        xml = xml_builder.build_request(
            "CustomerAddRq",
            body={
                "CustomerAdd": {
                    "Name": "Acme Corp",
                    "BillAddress": {
                        "Addr1": "123 Main St",
                        "City": "Springfield",
                    },
                }
            },
        )
        root = _parse(xml)
        add = root.find(".//CustomerAdd")
        assert add is not None
        assert add.find("Name").text == "Acme Corp"
        assert add.find("BillAddress/City").text == "Springfield"

    def test_list_body(self):
        """Repeated elements (like multiple line items)."""
        xml = xml_builder.build_request(
            "InvoiceAddRq",
            body={
                "InvoiceAdd": {
                    "CustomerRef": {"FullName": "Test"},
                    "InvoiceLineAdd": [
                        {"ItemRef": {"FullName": "Widget"}, "Quantity": "2"},
                        {"ItemRef": {"FullName": "Gadget"}, "Quantity": "1"},
                    ],
                }
            },
        )
        root = _parse(xml)
        lines = root.findall(".//InvoiceLineAdd")
        assert len(lines) == 2
        assert lines[0].find("ItemRef/FullName").text == "Widget"
        assert lines[1].find("ItemRef/FullName").text == "Gadget"


class TestQueryBuilder:
    def test_basic_query(self):
        xml = xml_builder.query("Customer")
        assert "CustomerQueryRq" in xml

    def test_query_with_filters(self):
        xml = xml_builder.query(
            "Customer",
            filters={"ActiveStatus": "All"},
            max_returned=50,
        )
        root = _parse(xml)
        rq = root.find(".//CustomerQueryRq")
        assert rq.find("ActiveStatus").text == "All"
        assert rq.find("MaxReturned").text == "50"

    # --- Bug 1: max_returned >= 100 should NOT force an iterator -----------
    def test_max_returned_100_no_iterator(self):
        """max_returned=100 must produce a plain query (no iterator attr)."""
        xml = xml_builder.query("Account", max_returned=100)
        root = _parse(xml)
        rq = root.find(".//AccountQueryRq")
        assert rq.find("MaxReturned").text == "100"
        assert rq.get("iterator") is None
        assert rq.get("iteratorID") is None

    def test_max_returned_5000_no_iterator(self):
        """Even very large page sizes must not trigger an iterator."""
        xml = xml_builder.query("Customer", max_returned=5000)
        root = _parse(xml)
        rq = root.find(".//CustomerQueryRq")
        assert rq.find("MaxReturned").text == "5000"
        assert rq.get("iterator") is None

    # --- Bug 2: element ordering must match qbXML DTD ----------------------
    def test_max_returned_before_filters(self):
        """MaxReturned must precede filter elements in the XML output."""
        xml = xml_builder.query(
            "Account",
            filters={
                "NameFilter": {"MatchCriterion": "Contains", "Name": "Rent"},
                "ActiveStatus": "All",
                "FromModifiedDate": "2021-01-01T00:00:00",
            },
            max_returned=10,
        )
        root = _parse(xml)
        rq = root.find(".//AccountQueryRq")
        tags = [child.tag for child in rq]
        assert tags.index("MaxReturned") < tags.index("ActiveStatus")
        assert tags.index("MaxReturned") < tags.index("FromModifiedDate")
        assert tags.index("MaxReturned") < tags.index("NameFilter")
        assert tags.index("ActiveStatus") < tags.index("FromModifiedDate")
        assert tags.index("FromModifiedDate") < tags.index("NameFilter")

    def test_name_filter_structure(self):
        """NameFilter must contain MatchCriterion + Name children."""
        xml = xml_builder.query(
            "Account",
            filters={"NameFilter": {"MatchCriterion": "Contains", "Name": "Rent"}},
            max_returned=10,
        )
        root = _parse(xml)
        nf = root.find(".//NameFilter")
        assert nf is not None
        assert nf.find("MatchCriterion").text == "Contains"
        assert nf.find("Name").text == "Rent"

    # --- Bug 3: iterator attributes ----------------------------------------
    def test_iterator_start(self):
        """iterator='Start' should set the XML attribute, not a child."""
        xml = xml_builder.query("Account", max_returned=10, iterator="Start")
        root = _parse(xml)
        rq = root.find(".//AccountQueryRq")
        assert rq.get("iterator") == "Start"
        assert rq.get("iteratorID") is None
        assert rq.find("MaxReturned").text == "10"

    def test_iterator_continue(self):
        """iterator='Continue' needs both the iterator and iteratorID attrs."""
        xml = xml_builder.query(
            "Account",
            max_returned=10,
            iterator="Continue",
            iterator_id="{abc-123}",
        )
        root = _parse(xml)
        rq = root.find(".//AccountQueryRq")
        assert rq.get("iterator") == "Continue"
        assert rq.get("iteratorID") == "{abc-123}"

    # --- Bug 5: IncludeLineItems -------------------------------------------
    def test_include_line_items(self):
        """Transaction get-by-ID must include IncludeLineItems element."""
        xml = xml_builder.query(
            "Check",
            filters={"TxnID": "85-1613406293"},
            include_line_items=True,
        )
        root = _parse(xml)
        rq = root.find(".//CheckQueryRq")
        assert rq.find("IncludeLineItems").text == "true"
        # IncludeLineItems must come after TxnID in DTD order
        tags = [child.tag for child in rq]
        assert tags.index("TxnID") < tags.index("IncludeLineItems")

    def test_no_include_line_items_by_default(self):
        """List entities should NOT include IncludeLineItems."""
        xml = xml_builder.query("Account", max_returned=50)
        assert "IncludeLineItems" not in xml


class TestAddBuilder:
    def test_add(self):
        xml = xml_builder.add("Customer", {"Name": "Test Co"})
        assert "CustomerAddRq" in xml
        assert "CustomerAdd" in xml
        root = _parse(xml)
        assert root.find(".//CustomerAdd/Name").text == "Test Co"


class TestModBuilder:
    def test_mod(self):
        xml = xml_builder.mod(
            "Customer",
            {
                "ListID": "ABC-123",
                "EditSequence": "999",
                "Name": "Updated Co",
            },
        )
        assert "CustomerModRq" in xml
        root = _parse(xml)
        mod_elem = root.find(".//CustomerMod")
        assert mod_elem.find("ListID").text == "ABC-123"
        assert mod_elem.find("EditSequence").text == "999"
        assert mod_elem.find("Name").text == "Updated Co"


class TestReportBuilder:
    """Bug 4 — report XML must use category-specific element names."""

    def test_general_summary_report_type_element(self):
        """GeneralSummaryReportQueryRq must use <GeneralSummaryReportType>."""
        xml = xml_builder.report(
            "GeneralSummaryReportQueryRq",
            "ProfitAndLossStandard",
        )
        root = _parse(xml)
        rq = root.find(".//GeneralSummaryReportQueryRq")
        assert rq is not None
        assert rq.find("GeneralSummaryReportType").text == "ProfitAndLossStandard"
        # Must NOT have a generic <ReportType> element
        assert rq.find("ReportType") is None

    def test_general_detail_report_type_element(self):
        xml = xml_builder.report(
            "GeneralDetailReportQueryRq",
            "GeneralLedger",
        )
        root = _parse(xml)
        rq = root.find(".//GeneralDetailReportQueryRq")
        assert rq.find("GeneralDetailReportType").text == "GeneralLedger"

    def test_aging_report_type_element(self):
        xml = xml_builder.report("AgingReportQueryRq", "ARAgingSummary")
        root = _parse(xml)
        rq = root.find(".//AgingReportQueryRq")
        assert rq.find("AgingReportType").text == "ARAgingSummary"

    def test_report_basis_after_summarize(self):
        """ReportBasis must come after SummarizeColumnsBy in the DTD."""
        xml = xml_builder.report(
            "GeneralSummaryReportQueryRq",
            "ProfitAndLossStandard",
            basis="Accrual",
            summarize_by="Month",
        )
        root = _parse(xml)
        rq = root.find(".//GeneralSummaryReportQueryRq")
        tags = [child.tag for child in rq]
        assert tags.index("SummarizeColumnsBy") < tags.index("ReportBasis")

    def test_report_with_date_macro(self):
        xml = xml_builder.report(
            "GeneralSummaryReportQueryRq",
            "BalanceSheetStandard",
            date_macro="ThisFiscalYear",
        )
        root = _parse(xml)
        rq = root.find(".//GeneralSummaryReportQueryRq")
        assert rq.find("ReportDateMacro").text == "ThisFiscalYear"

    def test_report_with_date_range(self):
        xml = xml_builder.report(
            "GeneralDetailReportQueryRq",
            "GeneralLedger",
            from_date="2024-01-01",
            to_date="2024-12-31",
        )
        root = _parse(xml)
        period = root.find(".//ReportPeriod")
        assert period is not None
        assert period.find("FromReportDate").text == "2024-01-01"
        assert period.find("ToReportDate").text == "2024-12-31"


class TestDeleteBuilder:
    def test_list_delete(self):
        xml = xml_builder.delete("Customer", is_transaction=False, list_id="ABC-123")
        assert "ListDelRq" in xml
        root = _parse(xml)
        assert root.find(".//ListDelType").text == "Customer"
        assert root.find(".//ListID").text == "ABC-123"

    def test_txn_delete(self):
        xml = xml_builder.delete("Invoice", is_transaction=True, txn_id="TXN-456")
        assert "TxnDelRq" in xml
        root = _parse(xml)
        assert root.find(".//TxnDelType").text == "Invoice"
        assert root.find(".//TxnID").text == "TXN-456"
