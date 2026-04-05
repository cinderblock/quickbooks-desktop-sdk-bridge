"""Parse qbXML response XML into Python dicts."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from lxml import etree

from .exceptions import QBRequestError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class QBResponse:
    """Parsed result of a qbXML response."""

    status_code: int
    status_severity: str  # "Info", "Warn", "Error"
    status_message: str
    ret_count: int = 0
    data: list[dict] | dict | None = None
    iterator_id: str | None = None
    iterator_remaining: int | None = None
    raw_xml: str = ""


@dataclass
class ReportColumn:
    col_id: int
    col_type: str  # "Amount", "Account", etc.
    title: str


@dataclass
class ReportRow:
    row_type: str  # "DataRow", "TextRow", "SubtotalRow", "TotalRow"
    label: str | None = None
    values: dict[int, str] = field(default_factory=dict)
    children: list[ReportRow] = field(default_factory=list)


@dataclass
class ReportData:
    title: str = ""
    subtitle: str = ""
    basis: str | None = None
    columns: list[ReportColumn] = field(default_factory=list)
    rows: list[ReportRow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _elem_to_dict(elem: etree._Element) -> dict | str:
    """Recursively convert an XML element to a dict.

    Leaf elements become ``{"Tag": "text"}``.
    Elements with children become nested dicts.
    Repeated sibling tags become lists.
    """
    children = list(elem)
    if not children:
        return elem.text or ""

    result: dict = {}
    for child in children:
        tag = etree.QName(child).localname if child.tag else child.tag
        value = _elem_to_dict(child)

        if tag in result:
            existing = result[tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[tag] = [existing, value]
        else:
            result[tag] = value

    return result


def check_status(xml_string: str) -> tuple[int, str, str]:
    """Extract ``(statusCode, statusSeverity, statusMessage)`` from the
    first ``*Rs`` element in a qbXML response.

    Raises ``QBRequestError`` if severity is ``"Error"``.
    """
    root = etree.fromstring(
        xml_string.encode("utf-8") if isinstance(xml_string, str) else xml_string
    )
    msgs = root.find("QBXMLMsgsRs")
    if msgs is None:
        raise QBRequestError("Invalid qbXML response: no QBXMLMsgsRs element")

    rs = msgs[0]  # first response element
    code = int(rs.get("statusCode", "-1"))
    severity = rs.get("statusSeverity", "Error")
    message = rs.get("statusMessage", "Unknown error")

    if severity == "Error":
        raise QBRequestError(message, qb_status_code=code, severity=severity)

    return code, severity, message


def parse_response(xml_string: str) -> QBResponse:
    """Parse a complete qbXML response into a ``QBResponse``."""
    root = etree.fromstring(
        xml_string.encode("utf-8") if isinstance(xml_string, str) else xml_string
    )
    msgs = root.find("QBXMLMsgsRs")
    if msgs is None:
        raise QBRequestError("Invalid qbXML response: no QBXMLMsgsRs element")

    rs = msgs[0]
    code = int(rs.get("statusCode", "-1"))
    severity = rs.get("statusSeverity", "Error")
    message = rs.get("statusMessage", "Unknown error")

    if severity == "Error":
        raise QBRequestError(message, qb_status_code=code, severity=severity)

    iterator_id = rs.get("iteratorID")
    remaining_str = rs.get("iteratorRemainingCount")
    remaining = int(remaining_str) if remaining_str else None

    # Collect all *Ret children
    ret_elements = [child for child in rs if child.tag.endswith("Ret")]
    data: list[dict] | dict | None = None
    if len(ret_elements) == 1:
        d = _elem_to_dict(ret_elements[0])
        data = d if isinstance(d, dict) else {"value": d}
    elif len(ret_elements) > 1:
        data = []
        for el in ret_elements:
            d = _elem_to_dict(el)
            data.append(d if isinstance(d, dict) else {"value": d})
    # else: no data (e.g. delete responses)

    return QBResponse(
        status_code=code,
        status_severity=severity,
        status_message=message,
        ret_count=len(ret_elements),
        data=data,
        iterator_id=iterator_id,
        iterator_remaining=remaining,
        raw_xml=xml_string if isinstance(xml_string, str) else xml_string.decode(),
    )


def parse_entity_list(xml_string: str, entity_type: str) -> list[dict]:
    """Extract all ``<EntityRet>`` elements as a list of dicts."""
    resp = parse_response(xml_string)
    if resp.data is None:
        return []
    if isinstance(resp.data, list):
        return resp.data
    return [resp.data]


def parse_single_entity(xml_string: str, entity_type: str) -> dict | None:
    """Extract a single entity from an Add/Mod/Query response."""
    resp = parse_response(xml_string)
    if resp.data is None:
        return None
    if isinstance(resp.data, list):
        return resp.data[0] if resp.data else None
    return resp.data


# ---------------------------------------------------------------------------
# Report parsing
# ---------------------------------------------------------------------------


def parse_report(xml_string: str) -> ReportData:
    """Parse a QB report response into structured ``ReportData``."""
    root = etree.fromstring(
        xml_string.encode("utf-8") if isinstance(xml_string, str) else xml_string
    )
    msgs = root.find("QBXMLMsgsRs")
    if msgs is None:
        raise QBRequestError("Invalid report response: no QBXMLMsgsRs")

    rs = msgs[0]
    code = int(rs.get("statusCode", "-1"))
    severity = rs.get("statusSeverity", "Error")
    message = rs.get("statusMessage", "")

    if severity == "Error":
        raise QBRequestError(message, qb_status_code=code, severity=severity)

    report = ReportData()

    # Report metadata
    report_ret = rs.find("ReportRet")
    ret = report_ret if report_ret is not None else rs
    report.title = _text(ret, "ReportTitle")
    report.subtitle = _text(ret, "ReportSubtitle")
    report.basis = _text(ret, "ReportBasis")

    # Columns
    for col_desc in ret.findall(".//ColDesc"):
        report.columns.append(
            ReportColumn(
                col_id=int(col_desc.get("colID", "0")),
                col_type=col_desc.get("dataType", ""),
                title=_text(col_desc, "ColTitle") or col_desc.get("colType", ""),
            )
        )

    # Rows
    report_data_elem = ret.find("ReportData")
    if report_data_elem is not None:
        report.rows = _parse_report_rows(report_data_elem)

    return report


def _parse_report_rows(parent: etree._Element) -> list[ReportRow]:
    """Recursively parse DataRow, TextRow, SubtotalRow, TotalRow."""
    rows: list[ReportRow] = []

    for child in parent:
        tag = child.tag

        if tag in ("DataRow", "TextRow", "SubtotalRow", "TotalRow"):
            row = ReportRow(row_type=tag)

            # Label from RowData element or first ColData
            row_data = child.find("RowData")
            if row_data is not None:
                row.label = row_data.get("value", "")
                row.values = {int(row_data.get("colID", "0")): row_data.get("value", "")}

            # Column values
            for col_data in child.findall("ColData"):
                col_id = int(col_data.get("colID", "0"))
                row.values[col_id] = col_data.get("value", "")

            # Label from first column if not set
            if row.label is None and row.values:
                first_col = min(row.values.keys())
                row.label = row.values.get(first_col, "")

            # Nested rows (for grouped reports)
            row.children = _parse_report_rows(child)

            rows.append(row)

    return rows


def _text(parent: etree._Element, tag: str) -> str:
    """Get text of a child element, or empty string."""
    elem = parent.find(tag)
    return elem.text.strip() if elem is not None and elem.text else ""
