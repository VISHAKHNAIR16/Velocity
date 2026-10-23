"""
Purchase service layer.

`record_purchase()` is the single write path for purchase bills. It runs inside
`@transaction.atomic` so the purchase row, its lines, the stock movements, and
the supplier's payable state always commit together.

`record_purchase_return()` creates a purchase return (debit note) that reverses
stock and tax pro-rata against the original purchase bill.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.core.money import q2
from apps.inventory.services.stock import (
    commit_purchase_movements,
    reverse_purchase_movements,
)
from apps.parties.models import Party

from ..errors import PurchaseConflict, PurchaseError
from ..models import Purchase, PurchaseItem, PurchaseReturn
from .gst_calculator import apply_totals, compute_purchase_totals

ZERO = Decimal("0.00")


def record_purchase(
    *,
    business,
    party: Party,
    purchase_date,
    bill_number: str = "",
    place_of_supply: str = "",
    supply_type: str = "INTRA",
    prices_include_tax: bool = False,
    round_invoice_total: bool = False,
    notes: str = "",
    lines: list[dict],
    user=None,
) -> Purchase:
    """
    Create a Purchase bill with nested lines, compute GST, and record stock
    movements. Runs inside `@transaction.atomic`.

    Returns the created Purchase (ISSUED).

    Raises PurchaseError / PurchaseConflict on invalid input.
    """
    # --- Gate: party must be a supplier or BOTH -------------------------
    if party.party_type not in ("SUPPLIER", "BOTH"):
        raise PurchaseError(
            code="PARTY_NOT_SUPPLIER",
            message=("A purchase bill can only be recorded against a supplier party."),
            field="party",
        )

    # --- Build throwaway lines for the calculator -----------------------
    calc_lines = []
    stock_lines = []  # carries item_id for the stock movement step
    for index, payload in enumerate(lines):
        item = payload.get("item")
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
        if item is not None and item.track_stock:
            stock_lines.append(
                {
                    "item_id": item.pk,
                    "quantity": payload.get("quantity"),
                    "line_id": index,
                }
            )

    try:
        totals = compute_purchase_totals(
            business=business,
            party=party,
            lines=calc_lines,
            place_of_supply=place_of_supply,
            supply_type=supply_type,
            prices_include_tax=prices_include_tax,
            round_invoice_total=round_invoice_total,
        )
    except Exception as exc:
        from ..api_errors import translate as _translate

        if hasattr(exc, "code"):
            raise _translate(exc) from exc
        raise

    with transaction.atomic():
        purchase = Purchase.objects.create(
            business=business,
            party=party,
            purchase_date=purchase_date,
            bill_number=bill_number,
            place_of_supply=place_of_supply,
            supply_type=supply_type,
            prices_include_tax=prices_include_tax,
            notes=notes,
            status=Purchase.Status.ISSUED,
            # Snapshots
            supplier_name=party.name,
            supplier_gstin=party.gstin or "",
            supplier_state_code=party.state_code or "",
            supplier_address=party.billing_address or "",
            business_name=business.trade_name or "",
            business_address=business.address_line or "",
            business_gstin=business.gstin or "",
            business_state_code=business.state_code or "",
        )

        # --- Create purchase item lines ---------------------------------
        for index, payload in enumerate(lines):
            item = payload.get("item")
            line_result = totals.lines[index]
            PurchaseItem.objects.create(
                purchase=purchase,
                business=business,
                item=item,
                item_name=payload.get("item_name") or (item.name if item else ""),
                item_type=payload.get("item_type", "PRODUCT"),
                hsn_sac_code=payload.get("hsn_sac_code", ""),
                unit=payload.get("unit", ""),
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

        # --- Persist totals ---------------------------------------------
        apply_totals(purchase, totals)

        # --- Stock movements (PURCHASE adds stock) ----------------------
        commit_purchase_movements(
            purchase, stock_lines, label=purchase.bill_number or str(purchase.pk), user=user
        )

    return purchase


def record_purchase_return(
    *,
    business,
    purchase: Purchase,
    return_date,
    return_number: str = "",
    reason: str = "",
    lines: list[dict],
    user=None,
) -> PurchaseReturn:
    """
    Record a purchase return (debit note to supplier).

    `lines` is a list of { purchase_item_id, quantity } specifying which lines
    are being returned and how much. Stock is reversed via PURCHASE_RETURN
    movements; tax is reversed pro-rata.

    Returns the created PurchaseReturn (ISSUED).
    """
    if purchase.business_id != business.pk:
        raise PurchaseError(
            code="PURCHASE_WRONG_BUSINESS",
            message="That purchase bill belongs to a different business.",
            field="purchase",
        )
    if purchase.status != Purchase.Status.ISSUED:
        raise PurchaseConflict(
            code="PURCHASE_NOT_ISSUED",
            message=("Returns can only be made against ISSUED purchase bills."),
            field="purchase",
        )

    # --- Build return lines from the purchase's items -------------------
    purchase_items = {pi.pk: pi for pi in purchase.items.all()}
    return_line_results = []
    return_subtotal = ZERO
    return_taxable = ZERO
    return_cgst = ZERO
    return_sgst = ZERO
    return_igst = ZERO

    for ret in lines:
        pi = purchase_items.get(ret["purchase_item_id"])
        if pi is None:
            raise PurchaseError(
                code="INVALID_PURCHASE_ITEM",
                message=f"Purchase item {ret['purchase_item_id']} is not on this bill.",
                field="lines",
            )
        qty = Decimal(str(ret["quantity"]))
        if qty <= 0:
            raise PurchaseError(
                code="INVALID_RETURN_QUANTITY",
                message="Return quantity must be greater than zero.",
                field="lines",
            )
        if qty > pi.quantity:
            raise PurchaseError(
                code="RETURN_EXCEEDS_PURCHASED",
                message=f"Cannot return {qty} of {pi.item_name}; only {pi.quantity} was purchased.",
                field="lines",
            )

        ratio = qty / pi.quantity
        taxable = q2(pi.taxable_value * ratio)
        cgst = q2(pi.cgst_amount * ratio)
        sgst = q2(pi.sgst_amount * ratio)
        igst = q2(pi.igst_amount * ratio)
        total = q2(pi.total_amount * ratio)

        return_line_results.append(
            {
                "purchase_item": pi,
                "quantity": qty,
                "taxable_value": taxable,
                "cgst_amount": cgst,
                "sgst_amount": sgst,
                "igst_amount": igst,
                "total_amount": total,
            }
        )
        return_subtotal = q2(return_subtotal + pi.gross_amount * ratio)
        return_taxable = q2(return_taxable + taxable)
        return_cgst = q2(return_cgst + cgst)
        return_sgst = q2(return_sgst + sgst)
        return_igst = q2(return_igst + igst)

    grand_total = q2(return_taxable + return_cgst + return_sgst + return_igst)

    with transaction.atomic():
        purchase_return = PurchaseReturn.objects.create(
            business=business,
            purchase=purchase,
            return_date=return_date,
            return_number=return_number,
            reason=reason,
            status=PurchaseReturn.Status.ISSUED,
            grand_total=grand_total,
            cgst_total=return_cgst,
            sgst_total=return_sgst,
            igst_total=return_igst,
        )

        # --- Reverse stock ----------------------------------------------
        reverse_purchase_movements(
            purchase_return,
            return_line_results,
            label=return_number or str(purchase_return.pk),
            user=user,
        )

    return purchase_return
