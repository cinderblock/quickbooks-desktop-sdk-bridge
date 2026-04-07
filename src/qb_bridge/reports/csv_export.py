"""Convert ReportData to CSV."""

from __future__ import annotations

import csv
import io

from qb_bridge.qb.xml_parser import ReportData, ReportRow
from qb_bridge.reports.formatters import format_currency, is_numeric


def report_to_csv(report: ReportData) -> str:
    """Render a QB report as a CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header rows
    if report.title:
        writer.writerow([report.title])
    if report.subtitle:
        writer.writerow([report.subtitle])
    if report.basis:
        writer.writerow([f"Basis: {report.basis}"])
    writer.writerow([])  # blank line

    # Column headers
    col_ids = sorted(report.columns, key=lambda c: c.col_id)
    header = [c.title for c in col_ids]
    writer.writerow(header)

    # Data rows
    _write_rows(writer, report.rows, col_ids, depth=0)

    return output.getvalue()


def _write_rows(writer: csv.writer, rows: list[ReportRow], col_ids: list, depth: int) -> None:
    """Recursively write report rows with indentation for hierarchy."""
    for row in rows:
        values = []
        for i, col in enumerate(col_ids):
            val = str(row.values.get(col.col_id, ""))
            if i == 0 and depth > 0:
                # Indent the label column for nested rows
                val = ("  " * depth) + val
            elif i > 0 and is_numeric(val):
                # Format numeric data columns as currency
                val = format_currency(val)
            values.append(val)
        writer.writerow(values)

        if row.children:
            _write_rows(writer, row.children, col_ids, depth + 1)
