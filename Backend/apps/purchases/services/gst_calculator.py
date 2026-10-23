"""
Purchase GST calculation service.

Mirrors the invoice GST calculator's patterns but for purchase bills:
- Computes CGST/SGST/IGST based on place of supply vs business state.
- All money as Decimal, never float.
- Reuses `apps.core.constants` GST rate validation.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from apps.core.constants import DEFAULT_GST_RATE, valid_gst_rate
from apps.core.money import q2

from ..models import Purchase

ZERO = Decimal("0.00")


class PurchaseCalculationError(Exception):
    """A purchase calculation gate refused the operation."""

    def __init__(self, code: str, message: str, field: str = "", status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.status = status

    def __str__(self) -> str:
        return self.message


def compute_purchase_totals(
    *,
    business,
    party,
    lines,
    place_of_supply: str,
    supply_type: str,
    prices_include_tax: bool = False,
    round_invoice_total: bool = False,
):
    """
    Compute all money figures for a purchase bill from its input lines.

    Returns a small object whose attributes mirror `Purchase` money fields plus
    `as_dicts()` for the line-level breakdown.

    `prices_include_tax` only affects presentation on the purchase bill; the
    stored figures are always tax-exclusive taxable_value + tax columns.
    """
    if party is None:
        raise PurchaseCalculationError(
            "PARTY_REQUIRED",
            "A supplier party is required to calculate tax.",
            field="party",
        )

    business_state = getattr(business, "state_code", "") or ""
    supplier_state = place_of_supply or ""

    # Intra-state: supplier state == business state. Inter-state: different.
    # For a purchase, the supplier's state is the place of supply.
    is_intra = bool(business_state) and bool(supplier_state) and business_state == supplier_state

    if supply_type == "INTRA" and not is_intra:
        raise PurchaseCalculationError(
            "SUPPLY_TYPE_MISMATCH",
            "INTRA supply type requires the supplier to be in the same state as the business.",
            field="supply_type",
        )
    if supply_type == "INTER" and is_intra:
        raise PurchaseCalculationError(
            "SUPPLY_TYPE_MISMATCH",
            "INTER supply type requires the supplier to be in a different state than the business.",
            field="supply_type",
        )

    # --- Line-level --------------------------------------------------
    line_results = []
    subtotal = ZERO
    total_discount = ZERO
    taxable_total = ZERO
    cgst_total = ZERO
    sgst_total = ZERO
    igst_total = ZERO

    for index, payload in enumerate(lines):
        quantity = Decimal(str(payload.get("quantity") or ZERO))
        unit_price = Decimal(str(payload.get("unit_price") or ZERO))
        line_discount = Decimal(str(payload.get("line_discount") or ZERO))
        tax_rate = Decimal(str(payload.get("tax_rate") or DEFAULT_GST_RATE))

        if not valid_gst_rate(tax_rate):
            raise PurchaseCalculationError(
                "INVALID_TAX_RATE",
                f"Line {index + 1}: tax rate {tax_rate} is not a valid GST slab.",
                field=f"items.{index}.tax_rate",
            )

        gross = q2(quantity * unit_price)
        net = q2(gross - line_discount)
        taxable = q2(net)

        # GST split: INTRA = CGST + SGST (half rate each), INTER = IGST (full rate)
        rate = Decimal(str(tax_rate))
        half_rate = rate / 2
        if is_intra:
            cgst = q2(taxable * half_rate / 100)
            sgst = q2(taxable * half_rate / 100)
            igst = ZERO
        else:
            cgst = ZERO
            sgst = ZERO
            igst = q2(taxable * rate / 100)

        total = q2(taxable + cgst + sgst + igst)

        line_results.append(
            {
                "index": index,
                "item_name": payload.get("item_name", ""),
                "hsn_sac_code": payload.get("hsn_sac_code", ""),
                "unit": payload.get("unit", ""),
                "quantity": str(quantity),
                "unit_price": str(unit_price),
                "line_discount": str(line_discount),
                "tax_rate": str(tax_rate),
                "gross_amount": str(gross),
                "net_amount": str(net),
                "taxable_value": str(taxable),
                "cgst_amount": str(cgst),
                "sgst_amount": str(sgst),
                "igst_amount": str(igst),
                "total_amount": str(total),
            }
        )

        subtotal = q2(subtotal + gross)
        total_discount = q2(total_discount + line_discount)
        taxable_total = q2(taxable_total + taxable)
        cgst_total = q2(cgst_total + cgst)
        sgst_total = q2(sgst_total + sgst)
        igst_total = q2(igst_total + igst)

    # --- Invoice-level rounding --------------------------------------
    tax_total = q2(cgst_total + sgst_total + igst_total)
    grand_total = q2(taxable_total + tax_total)

    round_off = ZERO
    if round_invoice_total:
        rounded = grand_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        round_off = q2(rounded - grand_total)
        grand_total = rounded

    class _Result:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def as_dicts(self):
            return line_results

    return _Result(
        subtotal=str(subtotal),
        total_discount=str(total_discount),
        taxable_total=str(taxable_total),
        cgst_total=str(cgst_total),
        sgst_total=str(sgst_total),
        igst_total=str(igst_total),
        round_off=str(round_off),
        grand_total=str(grand_total),
        tax_total=str(tax_total),
        supply_type=supply_type,
        lines=line_results,
    )


def hsn_summary_from_lines(lines, totals) -> list[dict]:
    """
    Aggregate purchase lines by HSN/SAC code for GSTR reporting.

    Returns rows with: hsn_sac_code, total_quantity, taxable_value, cgst,
    sgst, igst, total_tax.
    """
    from collections import OrderedDict

    grouped: OrderedDict = OrderedDict()
    for line in lines:
        code = line["hsn_sac_code"] or ""
        if not code:
            continue
        if code not in grouped:
            grouped[code] = {
                "hsn_sac_code": code,
                "total_quantity": ZERO,
                "taxable_value": ZERO,
                "cgst": ZERO,
                "sgst": ZERO,
                "igst": ZERO,
            }
        g = grouped[code]
        g["total_quantity"] = q2(g["total_quantity"] + Decimal(line["quantity"]))
        g["taxable_value"] = q2(g["taxable_value"] + Decimal(line["taxable_value"]))
        g["cgst"] = q2(g["cgst"] + Decimal(line["cgst_amount"]))
        g["sgst"] = q2(g["sgst"] + Decimal(line["sgst_amount"]))
        g["igst"] = q2(g["igst"] + Decimal(line["igst_amount"]))

    return [
        {
            "hsn_sac_code": g["hsn_sac_code"],
            "total_quantity": str(g["total_quantity"]),
            "taxable_value": str(g["taxable_value"]),
            "cgst": str(g["cgst"]),
            "sgst": str(g["sgst"]),
            "igst": str(g["igst"]),
            "total_tax": str(q2(g["cgst"] + g["sgst"] + g["igst"])),
        }
        for g in grouped.values()
    ]


def apply_totals(purchase: Purchase, totals) -> Purchase:
    """
    Persist computed totals onto a Purchase instance and save.

    Called inside the purchase service's transaction.
    """
    purchase.subtotal = Decimal(totals.subtotal)
    purchase.total_discount = Decimal(totals.total_discount)
    purchase.taxable_total = Decimal(totals.taxable_total)
    purchase.cgst_total = Decimal(totals.cgst_total)
    purchase.sgst_total = Decimal(totals.sgst_total)
    purchase.igst_total = Decimal(totals.igst_total)
    purchase.round_off = Decimal(totals.round_off)
    purchase.grand_total = Decimal(totals.grand_total)
    purchase.save(
        update_fields=[
            "subtotal",
            "total_discount",
            "taxable_total",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "round_off",
            "grand_total",
            "updated_at",
        ]
    )
    return purchase
