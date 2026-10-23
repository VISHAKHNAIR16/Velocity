"""
GSTR-1 & GSTR-3B summary engine (Phase 4.2).

Reads from issued sales invoices and their line snapshots, plus purchase bills
(for the input-tax summary). All money as Decimal, never float.

The B2C Large threshold is kept in `apps.core.constants` as a single constant
(decision 49).
"""

from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from django.db.models import Sum

from apps.core.constants import B2C_LARGE_THRESHOLD
from apps.core.money import q2

from ...purchases.models import Purchase
from ..models import Invoice

ZERO = Decimal("0.00")


class GSTReportError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return self.message


def _is_registered(party) -> bool:
    return bool(getattr(party, "gstin", "") or "").strip()


def _period_bounds(period: str):
    """'YYYY-MM' -> (start_date, end_date) inclusive."""
    year, month = period.split("-")
    year = int(year)
    month = int(month)
    from calendar import monthrange

    start = f"{year:04d}-{month:02d}-01"
    last_day = monthrange(year, month)[1]
    end = f"{year:04d}-{month:02d}-{last_day:02d}"
    return start, end


def gstr1_summary(business, period: str) -> dict:
    """
    Build the GSTR-1 summary for a business and tax period (YYYY-MM).

    Sections:
    - b2b: invoices to registered parties (GSTIN present)
    - b2c_large: inter-state sales to unregistered parties above threshold
    - b2c_small: other sales to unregistered parties
    - hsn_summary: grouped by HSN/SAC code
    """
    start, end = _period_bounds(period)

    invoices = (
        Invoice.objects.filter(
            business=business,
            status=Invoice.Status.ISSUED,
            invoice_date__gte=start,
            invoice_date__lte=end,
        )
        .select_related("party")
        .order_by("invoice_date", "id")
    )

    b2b = []
    b2c_large = []
    b2c_small = []
    hsn_grouped: OrderedDict = OrderedDict()

    b2b_total = ZERO
    b2c_large_total = ZERO
    b2c_small_total = ZERO

    for inv in invoices:
        items = list(inv.items.all())
        registered = _is_registered(inv.party)

        row = {
            "invoice_number": inv.invoice_number,
            "invoice_date": inv.invoice_date.isoformat(),
            "party_name": inv.recipient_name or inv.party.name,
            "gstin": inv.recipient_gstin or inv.party.gstin or "",
            "place_of_supply": inv.place_of_supply,
            "supply_type": inv.supply_type,
            "taxable_value": str(inv.taxable_total),
            "cgst": str(inv.cgst_total),
            "sgst": str(inv.sgst_total),
            "igst": str(inv.igst_total),
            "total": str(inv.grand_total),
        }

        if registered:
            b2b.append(row)
            b2b_total = q2(b2b_total + inv.grand_total)
        else:
            is_interstate = inv.supply_type == "INTER"
            over_threshold = inv.grand_total >= B2C_LARGE_THRESHOLD
            if is_interstate and over_threshold:
                b2c_large.append(row)
                b2c_large_total = q2(b2c_large_total + inv.grand_total)
            else:
                b2c_small.append(row)
                b2c_small_total = q2(b2c_small_total + inv.grand_total)

        # HSN aggregation from lines
        for line in items:
            code = line.hsn_sac_code or ""
            if not code or not line.item_name:
                continue
            if code not in hsn_grouped:
                hsn_grouped[code] = {
                    "hsn_sac_code": code,
                    "total_quantity": ZERO,
                    "taxable_value": ZERO,
                    "cgst": ZERO,
                    "sgst": ZERO,
                    "igst": ZERO,
                }
            g = hsn_grouped[code]
            g["total_quantity"] = q2(g["total_quantity"] + line.quantity)
            g["taxable_value"] = q2(g["taxable_value"] + Decimal(line.taxable_value))
            g["cgst"] = q2(g["cgst"] + Decimal(line.cgst_amount))
            g["sgst"] = q2(g["sgst"] + Decimal(line.sgst_amount))
            g["igst"] = q2(g["igst"] + Decimal(line.igst_amount))

    hsn_summary = [
        {
            "hsn_sac_code": g["hsn_sac_code"],
            "total_quantity": str(g["total_quantity"]),
            "taxable_value": str(g["taxable_value"]),
            "cgst": str(g["cgst"]),
            "sgst": str(g["sgst"]),
            "igst": str(g["igst"]),
            "total_tax": str(q2(g["cgst"] + g["sgst"] + g["igst"])),
        }
        for g in hsn_grouped.values()
    ]

    return {
        "period": period,
        "b2b": b2b,
        "b2b_total": str(b2b_total),
        "b2c_large": b2c_large,
        "b2c_large_total": str(b2c_large_total),
        "b2c_small": b2c_small,
        "b2c_small_total": str(b2c_small_total),
        "hsn_summary": hsn_summary,
        "grand_total": str(q2(b2b_total + b2c_large_total + b2c_small_total)),
    }


def gstr3b_summary(business, period: str) -> dict:
    """
    Build the GSTR-3B summary for a business and tax period (YYYY-MM).

    Sections:
    - outward_supplies: total taxable value and tax from issued sales invoices
    - itc: input tax credit from purchase bills (Regular suppliers only)
    - net_tax_ligibility: outward tax minus ITC
    """
    start, end = _period_bounds(period)

    # Outward supplies from sales invoices
    invoices = Invoice.objects.filter(
        business=business,
        status=Invoice.Status.ISSUED,
        invoice_date__gte=start,
        invoice_date__lte=end,
    )
    outward_agg = invoices.aggregate(
        taxable=Sum("taxable_total"),
        cgst=Sum("cgst_total"),
        sgst=Sum("sgst_total"),
        igst=Sum("igst_total"),
    )
    outward_taxable = outward_agg["taxable"] or ZERO
    outward_cgst = outward_agg["cgst"] or ZERO
    outward_sgst = outward_agg["sgst"] or ZERO
    outward_igst = outward_agg["igst"] or ZERO
    outward_tax_total = q2(outward_cgst + outward_sgst + outward_igst)

    # Input tax credit from purchase bills (Regular GSTIN suppliers)
    purchases = Purchase.objects.filter(
        business=business,
        status=Purchase.Status.ISSUED,
        purchase_date__gte=start,
        purchase_date__lte=end,
        supplier_gstin__gt="",
    )
    itc_agg = purchases.aggregate(
        taxable=Sum("taxable_total"),
        cgst=Sum("cgst_total"),
        sgst=Sum("sgst_total"),
        igst=Sum("igst_total"),
    )
    itc_taxable = itc_agg["taxable"] or ZERO
    itc_cgst = itc_agg["cgst"] or ZERO
    itc_sgst = itc_agg["sgst"] or ZERO
    itc_igst = itc_agg["igst"] or ZERO
    itc_tax_total = q2(itc_cgst + itc_sgst + itc_igst)

    net_cgst = q2(outward_cgst - itc_cgst)
    net_sgst = q2(outward_sgst - itc_sgst)
    net_igst = q2(outward_igst - itc_igst)
    net_tax = q2(net_cgst + net_sgst + net_igst)

    return {
        "period": period,
        "outward_supplies": {
            "taxable_value": str(outward_taxable),
            "cgst": str(outward_cgst),
            "sgst": str(outward_sgst),
            "igst": str(outward_igst),
            "tax_total": str(outward_tax_total),
        },
        "input_tax_credit": {
            "taxable_value": str(itc_taxable),
            "cgst": str(itc_cgst),
            "sgst": str(itc_sgst),
            "igst": str(itc_igst),
            "tax_total": str(itc_tax_total),
        },
        "net_tax_liability": {
            "cgst": str(net_cgst),
            "sgst": str(net_sgst),
            "igst": str(net_igst),
            "tax_total": str(net_tax),
        },
    }
