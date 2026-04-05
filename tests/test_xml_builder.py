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


class TestAddBuilder:
    def test_add(self):
        xml = xml_builder.add("Customer", {"Name": "Test Co"})
        assert "CustomerAddRq" in xml
        assert "CustomerAdd" in xml
        root = _parse(xml)
        assert root.find(".//CustomerAdd/Name").text == "Test Co"


class TestModBuilder:
    def test_mod(self):
        xml = xml_builder.mod("Customer", {
            "ListID": "ABC-123",
            "EditSequence": "999",
            "Name": "Updated Co",
        })
        assert "CustomerModRq" in xml
        root = _parse(xml)
        mod_elem = root.find(".//CustomerMod")
        assert mod_elem.find("ListID").text == "ABC-123"
        assert mod_elem.find("EditSequence").text == "999"
        assert mod_elem.find("Name").text == "Updated Co"


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
