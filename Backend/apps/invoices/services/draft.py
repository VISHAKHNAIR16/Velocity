"""
Adapter between the invoice models and the pure GST calculator.

This is the ONLY place that converts model rows into calculator inputs and
writes the results back. `/invoices/preview/` and invoice persistence both go
through `compute_totals()`, so the preview can never disagree with what is
saved - there is one code path, not two that happen to match today.
"""

from decimal import Decimal

from django.db import transaction

from apps.core.constants import DEFAULT_GST_RATE, is_hsn, is_sac
from apps.core.money import q2, q3
from apps.inventory.models import Item
from apps.invoices.models import Invoice, InvoiceItem
from apps.invoices.services.gst_calculator import (
    GSTCalculationError,
    InvoiceTotals,
    LineInput,
    calculate_invoice,
    default_place_of_supply,
)

ZERO = Decimal("0.00")


def build_line_inputs(lines) -> list[LineInput]:
    """Model rows -> calculator inputs. Only the stored INPUTS are read."""
    return [
        LineInput.build(
            quantity=line.quantity,
            unit_price=line.unit_price,
            tax_rate=line.tax_rate,
            line_discount=line.line_discount,
            item_type=line.item_type,
        )
        for line in lines
    ]


def has_goods_line(lines) -> bool:
    """Goods are governed by the place of delivery, services by the recipient."""
    return any(line.item_type == Item.ItemType.PRODUCT for line in lines)


def resolve_place_of_supply(party, lines, business=None) -> str:
    """
    Default place of supply: shipping state for goods, billing state otherwise.

    Decision 9 / 20 / 21. The history matters because this function is where
    two bugs lived:

    * **Decision 9 was never implemented.** It said "walk-in party state ->
      business state", but this returned `""` whenever the party had no state,
      and the auto-created walk-in always has a blank state. Result: `preview`
      answered `400 "Select a place of supply"` and a walk-in sale - the single
      most common retail invoice - could not be created at all.
    * **Decision 20 fixed it too broadly** (finding #1, P0). It let *every*
      blank-state party borrow the business state, so a real customer who left
      their state blank silently got CGST+SGST instead of IGST - the exact
      wrong-tax-type outcome `PARTY_STATE_MISSING` existed to prevent.

    The fallback therefore applies **only when `party.is_walk_in` is True**
    (decision 21). Everyone else with a blank state gets `""` back, which makes
    `PARTY_STATE_MISSING` reachable again.

    Returns "" when nothing is resolvable, so the calculator can raise its own,
    more specific error.
    """
    party_state = getattr(party, "state_code", "") or ""

    if not party_state:
        # Only the walk-in may borrow the business's state.
        if getattr(party, "is_walk_in", False):
            return getattr(business, "state_code", "") or ""
        return ""

    return default_place_of_supply(
        billing_state=party_state,
        shipping_state=getattr(party, "shipping_state_code", "") or "",
        has_goods_line=has_goods_line(lines),
    )


def compute_totals(
    *,
    business,
    party,
    lines,
    place_of_supply="",
    invoice_discount=ZERO,
    prices_include_tax=False,
    round_invoice_total=False,
) -> InvoiceTotals:
    """Run the calculator. Pure with respect to the database: it only reads rows."""
    resolved_pos = place_of_supply or resolve_place_of_supply(party, lines, business)

    # Name the ACTUAL problem before the calculator raises a generic one.
    # `calculate_invoice` is deliberately pure - it has no `party` and cannot tell
    # "the customer's state is blank" from "nothing resolved a place of supply",
    # so it reports PLACE_OF_SUPPLY_MISSING for both. This wrapper does have the
    # party, and the two need different instructions from the user: one means
    # "set the customer's state", the other "pick a place of supply".
    #
    # This also makes /preview/ agree with the issue gate, which already reported
    # PARTY_STATE_MISSING for this same condition (1.4.9 finding #1, P0).
    if not resolved_pos and not getattr(party, "state_code", ""):
        raise GSTCalculationError(
            "PARTY_STATE_MISSING",
            f"'{getattr(party, 'name', 'This customer')}' has no billing state. "
            "Set the customer's state, or pick the place of supply manually on "
            "the invoice.",
            field="party",
        )

    return calculate_invoice(
        build_line_inputs(lines),
        business_state=business.state_code,
        place_of_supply=resolved_pos,
        business_is_regular=(business.gst_registration_type == business.GstRegistrationType.REGULAR),
        invoice_discount=invoice_discount,
        prices_include_tax=prices_include_tax,
        round_invoice_total=round_invoice_total,
        document_title=(
            "Tax Invoice"
            if business.gst_registration_type == business.GstRegistrationType.REGULAR
            else "Bill of Supply"
        ),
    )


@transaction.atomic
def apply_totals(invoice: Invoice, totals: InvoiceTotals) -> Invoice:
    """Write computed figures onto the invoice and its lines. Never the reverse."""
    for line, result in zip(invoice.items.all(), totals.lines, strict=True):
        line.invoice_discount_share = result.invoice_discount_share
        line.taxable_value = result.taxable_value
        line.cgst_amount = result.cgst_amount
        line.sgst_amount = result.sgst_amount
        line.igst_amount = result.igst_amount
        line.total_amount = result.total_amount
        line.save(
            update_fields=[
                "invoice_discount_share",
                "taxable_value",
                "cgst_amount",
                "sgst_amount",
                "igst_amount",
                "total_amount",
                "updated_at",
            ]
        )

    invoice.subtotal = totals.subtotal
    invoice.total_discount = totals.total_discount
    invoice.taxable_total = totals.taxable_total
    invoice.cgst_total = totals.cgst_total
    invoice.sgst_total = totals.sgst_total
    invoice.igst_total = totals.igst_total
    invoice.round_off = totals.round_off
    invoice.grand_total = totals.grand_total
    # Only fill in the place of supply when the caller has not pinned one.
    # An explicit override (e.g. goods shipped to another state while billing
    # stays local) is a deliberate user decision and must survive a recalc.
    invoice.place_of_supply = invoice.place_of_supply or resolve_place_of_supply(
        invoice.party, invoice.items.all(), invoice.business
    )
    invoice.supply_type = totals.supply_type
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
            "place_of_supply",
            "supply_type",
            "updated_at",
        ]
    )
    return invoice


def recalculate_draft(invoice: Invoice) -> Invoice:
    """Recompute a draft from its own stored line inputs, then persist the result."""
    totals = compute_totals(
        business=invoice.business,
        party=invoice.party,
        lines=list(invoice.items.all()),
        place_of_supply=invoice.place_of_supply,
        invoice_discount=invoice.invoice_discount,
        prices_include_tax=invoice.prices_include_tax,
        round_invoice_total=invoice.business.round_invoice_total,
    )
    return apply_totals(invoice, totals)


def summarize_hsn(rows) -> list[dict]:
    """
    Bucket arbitrary line-like rows by HSN/SAC code.

    `rows` yields dicts with keys: hsn, description, quantity, taxable, cgst,
    sgst, igst, total. Both the saved-invoice path (model rows) and the preview
    path (calculator results) funnel through here, so there is exactly one
    bucketing implementation.

    Required on a tax invoice to a registered recipient, and reused by the
    GSTR-1 HSN summary in phase 4.2. Computed on demand from frozen line
    snapshots, so it is always reproducible.
    """
    buckets: dict[str, dict] = {}
    for row in rows:
        key = row["hsn"] or "—"
        bucket = buckets.setdefault(
            key,
            {
                "hsn_sac_code": key,
                "description": row["description"],
                "quantity": ZERO,
                "taxable_value": ZERO,
                "cgst": ZERO,
                "sgst": ZERO,
                "igst": ZERO,
                "total": ZERO,
            },
        )
        bucket["quantity"] = bucket["quantity"] + row["quantity"]
        bucket["taxable_value"] += row["taxable"]
        bucket["cgst"] += row["cgst"]
        bucket["sgst"] += row["sgst"]
        bucket["igst"] += row["igst"]
        bucket["total"] += row["total"]

    for bucket in buckets.values():
        for field in ("taxable_value", "cgst", "sgst", "igst", "total"):
            bucket[field] = q2(bucket[field])

    return [buckets[key] for key in sorted(buckets)]


def hsn_summary(invoice: Invoice) -> list[dict]:
    """HSN/SAC summary for a SAVED invoice, from its stored lines."""
    return summarize_hsn(
        {
            "hsn": line.hsn_sac_code,
            "description": line.service_description or line.item_name,
            "quantity": line.quantity,
            "taxable": line.taxable_value,
            "cgst": line.cgst_amount,
            "sgst": line.sgst_amount,
            "igst": line.igst_amount,
            "total": line.total_amount,
        }
        for line in invoice.items.all()
    )


def preview_hsn_summary(lines, totals) -> list[dict]:
    """HSN/SAC summary for PREVIEW, from calculator results (nothing saved)."""
    return summarize_hsn(
        {
            "hsn": line.hsn_sac_code,
            "description": line.service_description or line.item_name,
            "quantity": result.quantity,
            "taxable": result.taxable_value,
            "cgst": result.cgst_amount,
            "sgst": result.sgst_amount,
            "igst": result.igst_amount,
            "total": result.total_amount,
        }
        for line, result in zip(lines, totals.lines, strict=True)
    )


# ---------------------------------------------------------------------------
# Line building (shared by create, update, copy and preview)
# ---------------------------------------------------------------------------
def validate_hsn_sac(item_type: str, code: str) -> str:
    """
    Goods use HSN (4, 6 or 8 digits); services use SAC (4 or 6). A 6-digit code
    is valid for both, so this can never be used to tell the types apart - only
    to reject a structurally impossible code for the given type.
    """
    if not code:
        return code
    if item_type == Item.ItemType.SERVICE:
        if not is_sac(code):
            raise ValueError("A SAC code must be 4 or 6 digits (services never use 8).")
    elif not is_hsn(code):
        raise ValueError("An HSN code must be 4, 6 or 8 digits (5 and 7 are not used).")
    return code


def build_line_from_payload(payload: dict, *, business, existing: InvoiceItem | None = None):
    """
    Create or update one line.

    An item-backed line copies name/type/HSN/unit/description from the catalogue
    at save time; the user may still override price, discount and tax rate. A
    free-text line must supply those fields itself.

    `payload["item"]` may be an `Item` (the nested serializer has already
    resolved it) or a raw primary key, so this works from either a validated
    serializer payload or a hand-built dict.
    """
    item = payload.get("item")
    if item is not None and not isinstance(item, Item):
        item = Item.objects.filter(pk=item, business=business).first()
        if item is None:
            raise ValueError("That item does not exist in your business.")
    elif item is not None and item.business_id != business.id:
        # Belt and braces: never snapshot another tenant's catalogue entry.
        raise ValueError("That item does not exist in your business.")

    if item is not None:
        # Everything that identifies WHAT was sold comes from the catalogue, not
        # from the request. The invoice must not be able to print a different
        # name or HSN than the item master holds, so these are not overridable.
        # Price, discount and tax rate remain the user's to set per line.
        item_name = item.name
        item_type = item.item_type
        hsn_sac = item.hsn_sac_code
        unit = item.unit
        description = item.service_description
        default_rate = item.tax_rate
    else:
        item_name = (payload.get("item_name") or "").strip()
        item_type = (payload.get("item_type") or Item.ItemType.PRODUCT).strip()
        hsn_sac = (payload.get("hsn_sac_code") or "").strip()
        unit = (payload.get("unit") or "").strip()
        description = (payload.get("service_description") or "").strip()
        default_rate = payload.get("tax_rate")

        missing = [
            name
            for name, value in (
                ("item_name", item_name),
                ("item_type", item_type),
                ("hsn_sac_code", hsn_sac),
                ("unit", unit),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "A line without a catalogue item needs "
                f"{', '.join(missing)}."
            )
        if item_type == Item.ItemType.SERVICE and not description:
            raise ValueError(
                "Services need a description - it is printed on the tax invoice."
            )

    validate_hsn_sac(item_type, hsn_sac)

    tax_rate = payload.get("tax_rate")
    if tax_rate in (None, ""):
        tax_rate = default_rate if default_rate is not None else DEFAULT_GST_RATE

    values = {
        "item": item,
        "item_name": item_name[:200],
        "item_type": item_type[:8],
        "hsn_sac_code": (hsn_sac or "")[:8],
        "unit": (unit or "")[:8],
        "service_description": (description or "")[:80],
        # Quantity is a quantity, not money: 3 decimal places (1.500 kg).
        "quantity": q3(payload.get("quantity") or "0"),
        "unit_price": q2(payload.get("unit_price") or "0"),
        "line_discount": q2(payload.get("line_discount") or "0"),
        "tax_rate": tax_rate,
        # Denormalised business must come from the PARENT, never from
        # get_business(request.user), so the two can never diverge.
        "business": business,
    }

    line = existing or InvoiceItem(**values)
    for field, value in values.items():
        setattr(line, field, value)
    return line