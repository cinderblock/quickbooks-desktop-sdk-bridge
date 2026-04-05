"""Convert ReportData to PDF using ReportLab."""

from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from qb_bridge.qb.xml_parser import ReportData, ReportRow


def report_to_pdf(report: ReportData) -> bytes:
    """Render a QB report as a PDF byte string."""
    buf = io.BytesIO()
    page_size = landscape(letter) if len(report.columns) > 5 else letter

    doc = SimpleDocTemplate(
        buf,
        pagesize=page_size,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    styles = getSampleStyleSheet()
    elements: list = []

    # Title
    if report.title:
        elements.append(Paragraph(report.title, styles["Title"]))
    if report.subtitle:
        elements.append(Paragraph(report.subtitle, styles["Normal"]))
    if report.basis:
        elements.append(Paragraph(f"Basis: {report.basis}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    # Build table data
    col_ids = sorted(report.columns, key=lambda c: c.col_id)
    header = [c.title for c in col_ids]
    table_data = [header]
    row_styles: list[tuple[int, str]] = []  # (row_index, row_type)

    _collect_rows(report.rows, col_ids, table_data, row_styles, depth=0)

    if len(table_data) <= 1:
        elements.append(Paragraph("No data", styles["Normal"]))
    else:
        # Calculate column widths
        avail_width = page_size[0] - 1.0 * inch
        col_count = len(col_ids)
        col_width = avail_width / max(col_count, 1)

        table = Table(table_data, colWidths=[col_width] * col_count)

        # Styling
        style_commands = [
            # Header row
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            # Body
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            # Grid
            ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.grey),
            # Right-align numeric columns (all except first)
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ]

        # Alternating row colors
        for i in range(1, len(table_data)):
            if i % 2 == 0:
                style_commands.append(
                    ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8f9fa"))
                )

        # Bold for subtotal / total rows
        for row_idx, row_type in row_styles:
            actual_idx = row_idx + 1  # +1 for header
            if row_type in ("SubtotalRow", "TotalRow"):
                style_commands.append(
                    ("FONTNAME", (0, actual_idx), (-1, actual_idx), "Helvetica-Bold")
                )
            if row_type == "TotalRow":
                style_commands.append(
                    ("LINEABOVE", (0, actual_idx), (-1, actual_idx), 1, colors.black)
                )

        table.setStyle(TableStyle(style_commands))
        elements.append(table)

    doc.build(elements)
    return buf.getvalue()


def _collect_rows(
    rows: list[ReportRow],
    col_ids: list,
    table_data: list,
    row_styles: list,
    depth: int,
) -> None:
    """Flatten hierarchical report rows into a table."""
    for row in rows:
        values = []
        for i, col in enumerate(col_ids):
            val = row.values.get(col.col_id, "")
            if i == 0 and depth > 0:
                val = ("  " * depth) + str(val)
            values.append(str(val))
        row_styles.append((len(table_data) - 1, row.row_type))
        table_data.append(values)

        if row.children:
            _collect_rows(row.children, col_ids, table_data, row_styles, depth + 1)
