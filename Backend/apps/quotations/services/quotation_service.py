"""
Quotation service layer.

`create_quotation()` creates a quotation with nested lines and computes GST.
`convert_to_invoice()` atomically creates a DRAFT sales invoice from a
quotation and marks it CONVERTED.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.invoices.models import Invoice, InvoiceItem

from ..errors import QuotationConflict, QuotationError
from ..models import Quotation, QuotationItem
from .gst_calculator import apply_totals as apply_quotation_totals
from .gst_calculator import compute_quotation_totals
from .gst_calculator import compute_quotation_totals as compute_totals

ZERO = Decimal("0.00")


def create_quotation(
    *,
    business,
    party,
    quotation_date,
    valid_until,
    place_of_supply: str = "",
    supply_type: str = "INTRA",
    prices_include_tax: bool = False,
    notes: str = "",
    terms: str = "",
    lines: list[dict],
    user=None,
) -> Quotation:
    """Create a quotation with nested lines and compute GST. Atomic."""

    # Party must be a customer or BOTH
    if party.party_type not in ("CUSTOMER", "BOTH"):
        raise QuotationError(
            code="PARTY_NOT_CUSTOMER",
            message="A quotation can only be created for a customer party.",
            field="party",
        )

    calc_lines = []
    for payload in lines:
        calc_lines.append(
            {
                "item_name": payload.get("item_name", ""),
                "hsn_sac_code": payload.get("hsn_sac_code", ""),
                "unit": payload.get("unit", ""),
                "quantity": payload.get("quantity"),
                "unit_price": payload.get("unit_price"),
                "line_discount": payload.get("line_discount", ZERO),
                "tax_rate": payload.get("tax_rate"),
            }
        )

    totals = compute_quotation_totals(
        business=business,
        party=party,
        lines=calc_lines,
        place_of_supply=place_of_supply,
        supply_type=supply_type,
        prices_include_tax=prices_include_tax,
    )

    with transaction.atomic():
        quotation = Quotation.objects.create(
            business=business,
            party=party,
            quotation_date=quotation_date,
            valid_until=valid_until,
            place_of_supply=place_of_supply,
            supply_type=supply_type,
            prices_include_tax=prices_include_tax,
            notes=notes,
            terms=terms,
            status=Quotation.Status.DRAFT,
            customer_name=party.name,
            customer_gstin=party.gstin or "",
            customer_state_code=party.state_code or "",
            customer_address=party.billing_address or "",
            business_name=business.trade_name or "",
            business_address=business.address_line or "",
            business_gstin=business.gstin or "",
            business_state_code=business.state_code or "",
        )

        for index, payload in enumerate(lines):
            item = payload.get("item")
            line_result = totals.lines[index]
            QuotationItem.objects.create(
                quotation=quotation,
                business=business,
                item=item,
                item_name=payload.get("item_name") or (item.name if item else ""),
                item_type=payload.get("item_type", "PRODUCT"),
                hsn_sac_code=payload.get("hsn_sac_code", ""),
                unit=payload.get("unit", ""),
                service_description=payload.get("service_description", ""),
                quantity=payload.get("quantity"),
                unit_price=payload.get("unit_price"),
                line_discount=payload.get("line_discount", ZERO),
                tax_rate=payload.get("tax_rate"),
                taxable_value=Decimal(line_result["taxable_value"]),
                cgst_amount=Decimal(line_result["cgst_amount"]),
                sgst_amount=Decimal(line_result["sgst_amount"]),
                igst_amount=Decimal(line_result["igst_amount"]),
                total_amount=Decimal(line_result["total_amount"]),
            )

        apply_quotation_totals(quotation, totals)

    return quotation


def convert_to_invoice(quotation: Quotation, user=None) -> Invoice:
    """
    Atomically convert a quotation into a DRAFT sales invoice.

    The quotation status becomes CONVERTED and `converted_invoice`/`converted_at`
    are stamped. The new invoice is DRAFT — the user reviews and issues it.
    """
    if quotation.is_converted:
        raise QuotationConflict(
            code="ALREADY_CONVERTED",
            message=f"Quotation {quotation.display_number} is already converted.",
            field="quotation",
        )
    if quotation.status == Quotation.Status.EXPIRED:
        raise QuotationConflict(
            code="EXPIRED",
            message=f"Quotation {quotation.display_number} has expired and cannot be converted.",
            field="quotation",
        )

    business = quotation.business

    # --- Compute GST totals using the quotation GST calculator -------------
    calc_lines = []
    for qi in quotation.items.all():
        calc_lines.append(
            {
                "item_name": qi.item_name,
                "hsn_sac_code": qi.hsn_sac_code,
                "unit": qi.unit,
                "quantity": str(qi.quantity),
                "unit_price": str(qi.unit_price),
                "line_discount": str(qi.line_discount),
                "tax_rate": str(qi.tax_rate),
            }
        )

    totals = compute_totals(
        business=business,
        party=quotation.party,
        lines=calc_lines,
        place_of_supply=quotation.place_of_supply,
        supply_type=quotation.supply_type,
        prices_include_tax=quotation.prices_include_tax,
    )

    with transaction.atomic():
        invoice = Invoice.objects.create(
            business=business,
            party=quotation.party,
            invoice_date=timezone.localdate(),
            status=Invoice.Status.DRAFT,
            place_of_supply=quotation.place_of_supply,
            supply_type=quotation.supply_type,
            prices_include_tax=quotation.prices_include_tax,
            notes=quotation.notes,
            terms=quotation.terms,
            recipient_name=quotation.customer_name,
            recipient_gstin=quotation.customer_gstin,
            recipient_state_code=quotation.customer_state_code,
            recipient_address=quotation.customer_address,
            business_name=quotation.business_name,
            business_address=quotation.business_address,
            business_gstin=quotation.business_gstin,
            business_state_code=quotation.business_state_code,
            created_by=user if user and user.is_authenticated else None,
        )

        # --- Create invoice line items with computed totals ----------------
        for qi, line_result in zip(quotation.items.all(), totals.lines, strict=True):
            InvoiceItem.objects.create(
                invoice=invoice,
                business=business,
                item=qi.item,
                item_name=qi.item_name,
                item_type=qi.item_type,
                hsn_sac_code=qi.hsn_sac_code,
                unit=qi.unit,
                service_description=qi.service_description,
                quantity=qi.quantity,
                unit_price=qi.unit_price,
                line_discount=qi.line_discount,
                tax_rate=qi.tax_rate,
                taxable_value=Decimal(line_result["taxable_value"]),
                cgst_amount=Decimal(line_result["cgst_amount"]),
                sgst_amount=Decimal(line_result["sgst_amount"]),
                igst_amount=Decimal(line_result["igst_amount"]),
                total_amount=Decimal(line_result["total_amount"]),
            )

        # --- Persist invoice-level totals ----------------------------------
        invoice.subtotal = Decimal(totals.subtotal)
        invoice.total_discount = Decimal(totals.total_discount)
        invoice.taxable_total = Decimal(totals.taxable_total)
        invoice.cgst_total = Decimal(totals.cgst_total)
        invoice.sgst_total = Decimal(totals.sgst_total)
        invoice.igst_total = Decimal(totals.igst_total)
        invoice.round_off = Decimal(totals.round_off)
        invoice.grand_total = Decimal(totals.grand_total)
        invoice.save(
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

        # --- Mark quotation converted --------------------------------------
        quotation.status = Quotation.Status.CONVERTED
        quotation.converted_invoice = invoice
        quotation.converted_at = timezone.now()
        quotation.save(update_fields=["status", "converted_invoice", "converted_at", "updated_at"])

    return invoice
