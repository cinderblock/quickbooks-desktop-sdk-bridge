"""Report endpoints — P&L, Balance Sheet, Aging, etc."""

from __future__ import annotations

import io
from enum import StrEnum
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from qb_bridge.api.deps import get_qb_session, require_api_key, require_permission
from qb_bridge.qb.session import QBSessionManager
from qb_bridge.reports.csv_export import report_to_csv
from qb_bridge.reports.engine import run_report
from qb_bridge.reports.pdf import report_to_pdf

router = APIRouter(prefix="/api/v1/reports", tags=["Reports"])


# ---------------------------------------------------------------------------
# Report definitions — maps URL slug to qbXML request type + report name
# ---------------------------------------------------------------------------


class ReportCategory(StrEnum):
    general_summary = "GeneralSummaryReportQueryRq"
    general_detail = "GeneralDetailReportQueryRq"
    aging = "AgingReportQueryRq"
    budget_summary = "BudgetSummaryReportQueryRq"


REPORT_DEFS: dict[str, tuple[ReportCategory, str, str]] = {
    # slug: (category, qbxml_report_type, display_name)
    "profit-and-loss": (ReportCategory.general_summary, "ProfitAndLossStandard", "Profit & Loss"),
    "profit-and-loss-detail": (
        ReportCategory.general_detail,
        "ProfitAndLossDetail",
        "Profit & Loss Detail",
    ),
    "balance-sheet": (ReportCategory.general_summary, "BalanceSheetStandard", "Balance Sheet"),
    "balance-sheet-detail": (
        ReportCategory.general_detail,
        "BalanceSheetDetail",
        "Balance Sheet Detail",
    ),
    "trial-balance": (ReportCategory.general_summary, "TrialBalance", "Trial Balance"),
    "general-ledger": (ReportCategory.general_detail, "GeneralLedger", "General Ledger"),
    "ar-aging-summary": (ReportCategory.aging, "ARAgingSummary", "A/R Aging Summary"),
    "ar-aging-detail": (ReportCategory.aging, "ARAgingDetail", "A/R Aging Detail"),
    "ap-aging-summary": (ReportCategory.aging, "APAgingSummary", "A/P Aging Summary"),
    "ap-aging-detail": (ReportCategory.aging, "APAgingDetail", "A/P Aging Detail"),
    "sales-by-customer": (
        ReportCategory.general_summary,
        "SalesByCustomerSummary",
        "Sales by Customer",
    ),
    "sales-by-item": (ReportCategory.general_summary, "SalesByItemSummary", "Sales by Item"),
    "customer-balance": (
        ReportCategory.general_summary,
        "CustomerBalanceSummary",
        "Customer Balance",
    ),
    "vendor-balance": (ReportCategory.general_summary, "VendorBalanceSummary", "Vendor Balance"),
    "inventory-valuation": (
        ReportCategory.general_summary,
        "InventoryValuationSummary",
        "Inventory Valuation",
    ),
    "open-invoices": (ReportCategory.general_detail, "OpenInvoices", "Open Invoices"),
    "unpaid-bills": (ReportCategory.general_detail, "UnpaidBillsDetail", "Unpaid Bills"),
    "income-by-customer": (
        ReportCategory.general_summary,
        "IncomeByCustomerSummary",
        "Income by Customer",
    ),
    "expense-by-vendor": (
        ReportCategory.general_summary,
        "ExpenseByVendorSummary",
        "Expense by Vendor",
    ),
}


@router.get(
    "",
    summary="List available reports",
    description="Returns all report types this API can generate.",
)
async def list_reports(
    key: dict = Depends(require_api_key),
    _perm=Depends(require_permission("Report", "list")),
):
    reports = []
    for slug, (category, _qb_type, display_name) in REPORT_DEFS.items():
        reports.append(
            {
                "slug": slug,
                "name": display_name,
                "path": f"/api/v1/reports/{slug}",
                "category": category.name,
                "formats": ["json", "csv", "pdf"],
            }
        )
    return {"ok": True, "data": reports, "meta": {"count": len(reports)}}


@router.get(
    "/{report_slug}",
    summary="Generate a report",
    description=(
        "Run a QuickBooks report. Use `format` query param to choose output: "
        "`json` (default), `csv`, or `pdf`."
    ),
)
async def get_report(
    report_slug: str,
    session: QBSessionManager = Depends(get_qb_session),
    _key: dict = Depends(require_api_key),
    _perm=Depends(require_permission("Report", "get")),
    from_date: str | None = Query(None, description="Start date YYYY-MM-DD"),
    to_date: str | None = Query(None, description="End date YYYY-MM-DD"),
    date_macro: str | None = Query(
        None,
        description="Date preset: ThisMonth, LastMonth, ThisQuarter, ThisYear, LastYear, etc.",
    ),
    basis: Literal["Accrual", "Cash"] | None = Query(None, description="Accounting basis"),
    summarize_by: str | None = Query(
        None, description="Summarize columns by: Month, Quarter, Year, TotalOnly"
    ),
    format: Literal["json", "csv", "pdf"] = Query("json", description="Output format"),
):
    if report_slug not in REPORT_DEFS:
        return {
            "ok": False,
            "error": {
                "code": "UNKNOWN_REPORT",
                "message": f"Unknown report: {report_slug}. GET /api/v1/reports for available reports.",
            },
        }

    category, qb_report_type, display_name = REPORT_DEFS[report_slug]

    # Build qbXML body — element name and order must match the DTD.
    # The report-type element name is derived from the request type:
    #   "GeneralSummaryReportQueryRq" → "GeneralSummaryReportType"
    report_type_element = category.value.replace("QueryRq", "Type")
    body: dict = {report_type_element: qb_report_type}

    if date_macro:
        body["ReportDateMacro"] = date_macro
    elif from_date:
        period: dict = {"FromReportDate": from_date}
        if to_date:
            period["ToReportDate"] = to_date
        body["ReportPeriod"] = period

    # SummarizeColumnsBy must precede ReportBasis in the DTD
    if summarize_by:
        body["SummarizeColumnsBy"] = summarize_by

    if basis:
        body["ReportBasis"] = basis

    report_data = await run_report(session, request_type=category.value, body=body)

    if format == "csv":
        csv_content = report_to_csv(report_data)
        return StreamingResponse(
            io.StringIO(csv_content),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{report_slug}.csv"'},
        )

    if format == "pdf":
        pdf_bytes = report_to_pdf(report_data)
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{report_slug}.pdf"'},
        )

    # JSON format — convert ReportData to dict
    return {
        "ok": True,
        "data": {
            "title": report_data.title,
            "subtitle": report_data.subtitle,
            "basis": report_data.basis,
            "columns": [
                {"id": c.col_id, "type": c.col_type, "title": c.title} for c in report_data.columns
            ],
            "rows": _rows_to_dicts(report_data.rows),
        },
    }


def _rows_to_dicts(rows: list) -> list[dict]:
    """Recursively serialize ReportRow objects."""
    result = []
    for row in rows:
        d: dict = {
            "type": row.row_type,
            "label": row.label,
            "values": row.values,
        }
        if row.children:
            d["children"] = _rows_to_dicts(row.children)
        result.append(d)
    return result
