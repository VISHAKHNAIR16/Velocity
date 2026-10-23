"""
Quotation GST calculation service.

Mirrors the purchase GST calculator but for quotations. All money as Decimal,
never float.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from apps.core.constants import DEFAULT_GST_RATE, valid_gst_rate
from apps.core.money import q2

from ..models import Quotation

ZERO = Decimal("0.00")


class QuotationCalculationError(Exception):
    """A quotation calculation gate refused the operation."""

    def __init__(self, code: str, message: str, field: str = "", status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.status = status

    def __str__(self) -> str:
        return self.message


def compute_quotation_totals(
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
    Compute all money figures for a quotation from its input lines.

    Returns a small object whose attributes mirror `Quotation` money fields
    plus `as_dicts()` for the line-level breakdown.
    """
    if party is None:
        raise QuotationCalculationError(
            "PARTY_REQUIRED",
            "A customer party is required to calculate tax.",
            field="party",
        )

    business_state = getattr(business, "state_code", "") or ""
    customer_state = place_of_supply or ""
    is_intra = bool(business_state) and bool(customer_state) and business_state == customer_state

    if supply_type == "INTRA" and not is_intra:
        raise QuotationCalculationError(
            "SUPPLY_TYPE_MISMATCH",
            "INTRA supply type requires the customer to be in the same state as the business.",
            field="supply_type",
        )
    if supply_type == "INTER" and is_intra:
        raise QuotationCalculationError(
            "SUPPLY_TYPE_MISMATCH",
            "INTER supply type requires the customer to be in a different state than the business.",
            field="supply_type",
        )

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
            raise QuotationCalculationError(
                "INVALID_TAX_RATE",
                f"Line {index + 1}: tax rate {tax_rate} is not a valid GST slab.",
                field=f"items.{index}.tax_rate",
            )

        gross = q2(quantity * unit_price)
        net = q2(gross - line_discount)
        taxable = q2(net)

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


def apply_totals(quotation: Quotation, totals) -> Quotation:
    """Persist computed totals onto a Quotation instance and save."""
    quotation.subtotal = Decimal(totals.subtotal)
    quotation.total_discount = Decimal(totals.total_discount)
    quotation.taxable_total = Decimal(totals.taxable_total)
    quotation.cgst_total = Decimal(totals.cgst_total)
    quotation.sgst_total = Decimal(totals.sgst_total)
    quotation.igst_total = Decimal(totals.igst_total)
    quotation.round_off = Decimal(totals.round_off)
    quotation.grand_total = Decimal(totals.grand_total)
    quotation.save(
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
    return quotation
