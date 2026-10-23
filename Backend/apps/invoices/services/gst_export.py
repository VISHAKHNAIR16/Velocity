"""
GSTR export handlers (Phase 4.2).

Generates CSV and Excel (.xlsx) files formatted to match GST Portal filing
requirements. Both formats are produced from the same in-memory row data so the
two exports always agree.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .gst_reports import gstr1_summary, gstr3b_summary

ZERO = Decimal("0.00")


def _money(value) -> str:
    try:
        return str(Decimal(value or ZERO))
    except Exception:
        return "0.00"


def _sheet_rows_from_summary(summary: dict, kind: str) -> list[list]:
    """Flatten a GSTR summary dict into a list of rows (first row = header)."""
    if kind == "gstr1":
        rows = [
            ["Section", "Invoice Number", "Invoice Date", "Party Name", "GSTIN",
             "Place of Supply", "Supply Type", "Taxable Value", "CGST", "SGST",
             "IGST", "Total"],
        ]
        for section_key, label in (
            ("b2b", "B2B"),
            ("b2c_large", "B2C Large"),
            ("b2c_small", "B2C Small"),
        ):
            for row in summary.get(section_key, []):
                rows.append([
                    label,
                    row["invoice_number"],
                    row["invoice_date"],
                    row["party_name"],
                    row["gstin"],
                    row["place_of_supply"],
                    row["supply_type"],
                    _money(row["taxable_value"]),
                    _money(row["cgst"]),
                    _money(row["sgst"]),
                    _money(row["igst"]),
                    _money(row["total"]),
                ])
        # HSN summary block
        rows.append([])
        rows.append(["HSN/SAC Code", "Total Quantity", "Taxable Value", "CGST", "SGST", "IGST", "Total Tax"])
        for row in summary.get("hsn_summary", []):
            rows.append([
                row["hsn_sac_code"],
                _money(row["total_quantity"]),
                _money(row["taxable_value"]),
                _money(row["cgst"]),
                _money(row["sgst"]),
                _money(row["igst"]),
                _money(row["total_tax"]),
            ])
    else:  # gstr3b
        rows = [
            ["Section", "Taxable Value", "CGST", "SGST", "IGST", "Tax Total"],
        ]
        out = summary.get("outward_supplies", {})
        itc = summary.get("input_tax_credit", {})
        net = summary.get("net_tax_liability", {})
        rows.append(["Outward Supplies", _money(out.get("taxable_value")), _money(out.get("cgst")),
                     _money(out.get("sgst")), _money(out.get("igst")), _money(out.get("tax_total"))])
        rows.append(["Input Tax Credit", _money(itc.get("taxable_value")), _money(itc.get("cgst")),
                     _money(itc.get("sgst")), _money(itc.get("igst")), _money(itc.get("tax_total"))])
        rows.append(["Net Tax Liability", "", _money(net.get("cgst")), _money(net.get("sgst")),
                     _money(net.get("igst")), _money(net.get("tax_total"))])
    return rows


def export_gstr1_csv(business, period: str) -> bytes:
    """Return GSTR-1 summary as CSV bytes (UTF-8)."""
    summary = gstr1_summary(business, period)
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in _sheet_rows_from_summary(summary, "gstr1"):
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def export_gstr3b_csv(business, period: str) -> bytes:
    """Return GSTR-3B summary as CSV bytes (UTF-8)."""
    summary = gstr3b_summary(business, period)
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in _sheet_rows_from_summary(summary, "gstr3b"):
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def export_gstr1_excel(business, period: str) -> bytes:
    """Return GSTR-1 summary as an .xlsx workbook (bytes)."""
    summary = gstr1_summary(business, period)
    wb = Workbook()
    ws = wb.active
    ws.title = f"GSTR-1 {period}"

    rows = _sheet_rows_from_summary(summary, "gstr1")
    for r_idx, row in enumerate(rows, start=1):
        for c_idx, value in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=value)
            if r_idx == 1:
                cell.font = Font(bold=True)
    # Autosize columns crudely
    for c_idx in range(1, 13):
        col_letter = get_column_letter(c_idx)
        ws.column_dimensions[col_letter].width = 22

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_gstr3b_excel(business, period: str) -> bytes:
    """Return GSTR-3B summary as an .xlsx workbook (bytes)."""
    summary = gstr3b_summary(business, period)
    wb = Workbook()
    ws = wb.active
    ws.title = f"GSTR-3B {period}"

    rows = _sheet_rows_from_summary(summary, "gstr3b")
    for r_idx, row in enumerate(rows, start=1):
        for c_idx, value in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=value)
            if r_idx == 1:
                cell.font = Font(bold=True)
    for c_idx in range(1, 7):
        col_letter = get_column_letter(c_idx)
        ws.column_dimensions[col_letter].width = 22

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
