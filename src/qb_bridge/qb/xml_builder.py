"""Build qbXML request documents from Python dicts.

qbXML is order-sensitive (validated against a DTD), so we use
``lxml.etree`` to construct elements in insertion order and rely
on callers to pass ``OrderedDict`` or plain ``dict`` (which is
insertion-ordered in Python 3.7+).
"""

from __future__ import annotations

from lxml import etree

QBXML_VERSION = "13.0"

QBXML_PROLOG = f'<?xml version="1.0" encoding="utf-8"?>\n<?qbxml version="{QBXML_VERSION}"?>\n'


def _dict_to_xml(parent: etree._Element, data: dict | list | str) -> None:
    """Recursively convert a nested dict/list/str into XML child elements."""
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list):
                # Repeated elements (e.g. multiple InvoiceLineAdd)
                for item in value:
                    child = etree.SubElement(parent, key)
                    _dict_to_xml(child, item)
            elif isinstance(value, dict):
                child = etree.SubElement(parent, key)
                _dict_to_xml(child, value)
            elif value is not None:
                child = etree.SubElement(parent, key)
                child.text = str(value)
    elif isinstance(data, str):
        parent.text = data


def build_request(
    request_type: str,
    body: dict | None = None,
    request_id: str = "1",
    on_error: str = "stopOnError",
) -> str:
    """Build a complete qbXML request envelope.

    Args:
        request_type: e.g. ``"CustomerQueryRq"``, ``"InvoiceAddRq"``
        body: Nested dict of XML elements inside the request tag.
        request_id: Correlates request to response.
        on_error: ``"stopOnError"`` or ``"continueOnError"``.

    Returns:
        Complete qbXML string ready for ``ProcessRequest``.
    """
    root = etree.Element("QBXML")
    msgs = etree.SubElement(root, "QBXMLMsgsRq", onError=on_error)
    rq = etree.SubElement(msgs, request_type, requestID=request_id)

    if body:
        _dict_to_xml(rq, body)

    xml_bytes = etree.tostring(root, xml_declaration=False, encoding="unicode")
    return QBXML_PROLOG + xml_bytes


def query(
    entity: str,
    *,
    filters: dict | None = None,
    max_returned: int | None = None,
    include_ret_elements: list[str] | None = None,
    iterator: str | None = None,
    iterator_id: str | None = None,
) -> str:
    """Build a ``<EntityQueryRq>`` request.

    Args:
        entity: e.g. ``"Customer"``, ``"Invoice"``, ``"Account"``
        filters: Dict of filter elements (``NameFilter``, ``ActiveStatus``, etc.)
        max_returned: Limit results.
        include_ret_elements: List of field names to include in response.
        iterator: ``"Start"`` or ``"Continue"``
        iterator_id: Required when ``iterator="Continue"``
    """
    body: dict = {}

    if filters:
        body.update(filters)

    if max_returned is not None:
        body["MaxReturned"] = str(max_returned)

    if include_ret_elements:
        # IncludeRetElement appears multiple times — use a list
        body["IncludeRetElement"] = include_ret_elements

    rq_type = f"{entity}QueryRq"
    attrs = {}
    if iterator:
        attrs["iterator"] = iterator
    if iterator_id:
        attrs["iteratorID"] = iterator_id

    # For iterator attrs we need to build manually
    if attrs:
        root = etree.Element("QBXML")
        msgs = etree.SubElement(root, "QBXMLMsgsRq", onError="stopOnError")
        rq = etree.SubElement(msgs, rq_type, requestID="1", **attrs)
        if body:
            _dict_to_xml(rq, body)
        xml_bytes = etree.tostring(root, xml_declaration=False, encoding="unicode")
        return QBXML_PROLOG + xml_bytes

    return build_request(rq_type, body)


def add(entity: str, data: dict) -> str:
    """Build a ``<EntityAddRq>`` request.

    The ``data`` dict is wrapped in an ``<EntityAdd>`` element automatically.
    """
    return build_request(f"{entity}AddRq", {f"{entity}Add": data})


def mod(entity: str, edit_data: dict) -> str:
    """Build a ``<EntityModRq>`` request.

    ``edit_data`` must include the entity's ID field (``ListID`` or ``TxnID``)
    and ``EditSequence``.
    """
    return build_request(f"{entity}ModRq", {f"{entity}Mod": edit_data})


def delete(
    entity: str,
    *,
    is_transaction: bool = False,
    list_id: str | None = None,
    txn_id: str | None = None,
) -> str:
    """Build a ``ListDelRq`` or ``TxnDelRq``.

    Args:
        entity: e.g. ``"Customer"`` or ``"Invoice"``
        is_transaction: True for transaction entities (Invoice, Bill, etc.)
        list_id: Required for list entities.
        txn_id: Required for transaction entities.
    """
    if is_transaction:
        return build_request(
            "TxnDelRq",
            {"TxnDelType": entity, "TxnID": txn_id},
        )
    else:
        return build_request(
            "ListDelRq",
            {"ListDelType": entity, "ListID": list_id},
        )


def report(
    report_type: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    date_macro: str | None = None,
    basis: str | None = None,
    summarize_by: str | None = None,
) -> str:
    """Build a report query request.

    Args:
        report_type: e.g. ``"GeneralSummaryReportQueryRq"``,
                     ``"GeneralDetailReportQueryRq"``
        from_date: ISO date ``YYYY-MM-DD``
        to_date: ISO date ``YYYY-MM-DD``
        date_macro: e.g. ``"ThisMonth"``, ``"ThisFiscalYear"``
        basis: ``"Accrual"`` or ``"Cash"``
        summarize_by: e.g. ``"Month"``, ``"TotalOnly"``
    """
    body: dict = {}

    if date_macro:
        body["ReportDateMacro"] = date_macro
    else:
        if from_date:
            body["ReportPeriod"] = {"FromReportDate": from_date}
            if to_date:
                body["ReportPeriod"]["ToReportDate"] = to_date

    if basis:
        body["ReportBasis"] = basis

    if summarize_by:
        body["SummarizeColumnsBy"] = summarize_by

    return build_request(report_type, body)
