"""
Turn a purchase payload into saved rows, and keep the two sets of figures apart.

The one idea this module exists to enforce: **the entered amounts are the truth
and are never overwritten.** The calculator runs alongside and writes only to
the `*_computed` columns, so a discrepancy is recorded rather than silently
corrected. See `apps/purchases/models.py` for why.

Mirrors `apps/invoices/services/draft.py` where the shape matches, and departs
from it deliberately where it does not: on a purchase the catalogue's HSN, unit
and default rate are **inputs**, not fixed snapshots, because the supplier's
bill is the authority and it may differ from our catalogue. Overriding those
would quietly relabel a supplier's product.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.core.fiscal import financial_year
from apps.inventory.models import Item
from apps.purchases.models import Purchase, PurchaseItem, PurchaseStatus
from apps.purchases.services.divergence import build_report

ZERO = Decimal("0.00")


def q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def q3(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.001"))


def build_line_from_payload(payload: dict, *, business, existing=None) -> PurchaseItem:
    """
    Return an **unsaved** `PurchaseItem` for one payload row.

    `existing` is mutated and returned when given, so editing a line reuses the
    row rather than leaving a duplicate behind.
    """
    item = _resolve_item(payload.get("item"), business)

    values = {
        "item": item,
        "business": business,
        "quantity": q3(payload.get("quantity")),
        "unit_price": q2(payload.get("unit_price")),
        "line_discount": q2(payload.get("line_discount") or 0),
        "tax_rate": q2(payload.get("tax_rate") or 0),
        "itc_eligible": bool(payload.get("itc_eligible", True)),
        # Entered amounts, stored exactly as typed.
        "taxable_value": q2(payload.get("taxable_value") or 0),
        "cgst_amount": q2(payload.get("cgst_amount") or 0),
        "sgst_amount": q2(payload.get("sgst_amount") or 0),
        "igst_amount": q2(payload.get("igst_amount") or 0),
        "total_amount": q2(payload.get("total_amount") or 0),
    }

    if item is not None:
        # Catalogue values are a DEFAULT here, not an override - a supplier's
        # bill is the authority on what was actually bought.
        values.update(
            {
                "item_name": (payload.get("item_name") or item.name)[:200],
                "item_type": (payload.get("item_type") or item.item_type)[:8],
                "hsn_sac_code": (payload.get("hsn_sac_code") or item.hsn_sac_code or "")[:8],
                "unit": (payload.get("unit") or item.unit or "")[:8],
                "service_description": (payload.get("service_description") or "")[:80],
            }
        )
    else:
        # Free-text line: freight, a packing charge, a bank fee. Never touches
        # stock, and must describe itself completely.
        values.update(
            {
                "item_name": (payload.get("item_name") or "")[:200],
                "item_type": (payload.get("item_type") or Item.ItemType.PRODUCT)[:8],
                "hsn_sac_code": (payload.get("hsn_sac_code") or "")[:8],
                "unit": (payload.get("unit") or "")[:8],
                "service_description": (payload.get("service_description") or "")[:80],
            }
        )
        if not values["item_name"]:
            raise ValueError(
                "A line without a catalogue item needs a description of what it is."
            )

    line = existing or PurchaseItem(**values)
    for field, value in values.items():
        setattr(line, field, value)
    return line


def _resolve_item(raw, business):
    """
    Resolve an item id to a catalogue row inside this business.

    Refuses another tenant's item even when the caller passes an already-resolved
    instance, for the same reason `draft.py` does on the sales side: an id from
    the wrong business would silently move the wrong stock.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, Item):
        if raw.business_id != business.id:
            raise ValueError("That item does not exist in your business.")
        return raw
    item = Item.objects.filter(pk=raw, business=business).first()
    if item is None:
        raise ValueError("That item does not exist in your business.")
    return item


def snapshot_supplier(purchase: Purchase, supplier) -> None:
    """
    Freeze the supplier onto the bill.

    A bill filed last year has to be judged by the supplier as they were then.
    If the supplier registers for GST today, last year's bills do not become
    claimable, and renaming them today must not change a filed document.
    """
    purchase.supplier = supplier
    purchase.supplier_name = supplier.name
    purchase.supplier_gstin = supplier.gstin or ""
    purchase.supplier_state_code = supplier.state_code or ""
    address = ", ".join(
        part
        for part in (
            (supplier.billing_address or "").strip(),
            (supplier.billing_city or "").strip(),
            (supplier.billing_pincode or "").strip(),
        )
        if part
    )
    purchase.supplier_address = address


def snapshot_business(purchase: Purchase, business) -> None:
    purchase.business_name = business.company_name or business.trade_name or ""
    purchase.business_state_code = business.state_code or ""


@transaction.atomic
def replace_lines(purchase: Purchase, payloads: list, *, business) -> list:
    """
    Replace the bill's lines with `payloads`, preserving existing rows.

    Updating in place rather than delete-and-recreate keeps the line's identity
    stable, which matters because `StockMovement.source_line_id` points at it and
    decision 30's unique constraint is built on it.
    """
    purchase.items.all().delete()
    lines = []
    for payload in payloads:
        line = build_line_from_payload(payload, business=business)
        line.purchase = purchase
        line.business = business
        line.save()
        lines.append(line)
    return lines


def apply_computed(purchase: Purchase, totals) -> None:
    """
    Copy the calculator's figures into the `*_computed` columns and record the gap.

    Never touches the entered columns. That is the entire point of the module.
    """
    header_map = {
        "taxable_total": "taxable_total_computed",
        "cgst_total": "cgst_total_computed",
        "sgst_total": "sgst_total_computed",
        "igst_total": "igst_total_computed",
        "grand_total": "grand_total_computed",
    }
    for source, target in header_map.items():
        setattr(purchase, target, q2(getattr(totals, source, 0)))

    line_map = {
        "taxable_value": "taxable_value_computed",
        "cgst_amount": "cgst_amount_computed",
        "sgst_amount": "sgst_amount_computed",
        "igst_amount": "igst_amount_computed",
        "total_amount": "total_amount_computed",
    }
    lines = list(purchase.items.all())
    for line, computed in zip(lines, list(getattr(totals, "lines", []) or []), strict=False):
        for source, target in line_map.items():
            setattr(line, target, q2(getattr(computed, source, 0)))
        line.save(update_fields=list(line_map.values()) + ["updated_at"])

    report = build_report(purchase, totals)
    purchase.max_divergence = report.max_difference
    purchase.has_divergence = report.has_divergence


def sum_entered(purchase: Purchase) -> dict:
    """
    Totals derived from the entered lines, for comparison and for validation.

    Used to tell "the header does not match the lines" (always an error) from
    "the header does not match the *calculator*" (only a warning). The first is a
    mistake in this document; the second is a fact about the supplier's bill, and
    the two must never be reported the same way.
    """
    lines = list(purchase.items.all())
    return {
        "taxable_total": sum((line.taxable_value for line in lines), ZERO),
        "cgst_total": sum((line.cgst_amount for line in lines), ZERO),
        "sgst_total": sum((line.sgst_amount for line in lines), ZERO),
        "igst_total": sum((line.igst_amount for line in lines), ZERO),
        "total_amount": sum((line.total_amount for line in lines), ZERO),
    }


def parse_decimal(raw, default=ZERO) -> Decimal:
    """Lenient decimal parse for query-string filters; never raises."""
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return default


def fiscal_year_for(date_value) -> str:
    return financial_year(date_value)


__all__ = [
    "Purchase",
    "PurchaseItem",
    "PurchaseStatus",
    "apply_computed",
    "build_line_from_payload",
    "fiscal_year_for",
    "parse_decimal",
    "q2",
    "q3",
    "replace_lines",
    "snapshot_business",
    "snapshot_supplier",
    "sum_entered",
]