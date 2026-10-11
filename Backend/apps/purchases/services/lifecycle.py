"""
Purchase lifecycle: `DRAFT -> POSTED -> CANCELLED`.

Order of operations on post, and why
------------------------------------
    lock the bill -> status check -> period check -> reserve_purchase (locks the
    items, writes nothing) -> commit_purchase (writes the ledger) -> save

The same shape as issuing a sales invoice, for the same reasons. Stock is
reserved before anything is written so a refusal cannot leave a half-applied
increase; the item locks are taken in ascending id order because locking in line
order is the classic deadlock; and the whole thing is one transaction so a
failure at any point leaves the bill a draft.

The one genuine difference: a purchase has **no number to allocate**. The
supplier's bill number is the identifier and it exists from the first keystroke,
so there is no counter to lock last and no gap to leave behind. An internal
`PUR/` series was considered and dropped - see `apps/purchases/models.py`.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.inventory.services.stock import (
    check_purchase_cancellation,
    commit_purchase,
    reserve_purchase,
    reverse_purchase,
)
from apps.invoices.services.gst_calculator import LineInput, calculate_invoice
from apps.invoices.services.period_rules import assert_period_open
from apps.purchases.errors import DuplicateBillNumber, PurchaseConflict, PurchaseError
from apps.purchases.models import Purchase, PurchaseStatus

ZERO = Decimal("0.00")


@transaction.atomic
def post_purchase(purchase_id: int, *, user=None) -> tuple[Purchase, bool]:
    """
    Move a draft bill to POSTED and increase stock.

    Returns `(purchase, already_posted)`. Idempotent: posting an already-posted
    bill returns it unchanged rather than raising, so a double-click or a retried
    request cannot increase stock twice.
    """
    purchase = Purchase.objects.select_for_update().get(pk=purchase_id)

    if purchase.status == PurchaseStatus.POSTED:
        return purchase, True
    if purchase.status == PurchaseStatus.CANCELLED:
        raise PurchaseConflict(
            "This bill was cancelled. Copy it to a new draft instead of posting it."
        )
    if not purchase.items.exists():
        raise PurchaseError("PURCHASE_EMPTY", "A purchase bill needs at least one line.")

    assert_period_open(purchase.business, purchase.bill_date)
    guard_duplicate(purchase)

    plan = reserve_purchase(purchase)
    commit_purchase(purchase, plan, label=purchase.bill_number or "PURCHASE", user=user)

    purchase.status = PurchaseStatus.POSTED
    purchase.posted_at = timezone.now()
    purchase.posted_by = user if (user and user.is_authenticated) else None
    purchase.save(update_fields=["status", "posted_at", "posted_by", "updated_at"])

    maybe_update_purchase_prices(purchase)
    return purchase, False


def guard_duplicate(purchase: Purchase) -> None:
    """
    Friendly duplicate check.

    The database constraint is the real guard - it wins a race, this does not.
    But a constraint violation arrives as an `IntegrityError` naming a constraint
    nobody can read, so this exists to say something actionable.
    """
    clash = (
        Purchase.objects.filter(
            supplier_id=purchase.supplier_id,
            bill_number=purchase.bill_number,
            financial_year=purchase.financial_year,
        )
        .exclude(pk=purchase.pk)
        .exists()
    )
    if clash:
        raise DuplicateBillNumber(purchase.bill_number, purchase.financial_year)


@transaction.atomic
def cancel_purchase(purchase_id: int, *, reason: str, user=None) -> Purchase:
    """
    Cancel a posted bill and reverse its stock increase.

    Unlike cancelling a sale this can push stock negative - the goods arrived
    earlier and may since have been sold - so `negative_stock_policy` applies and
    under BLOCK it is refused outright. The period lock still applies too: a bill
    in a period that has been filed cannot be unwound by cancellation.

    Safe to call twice: `reverse_purchase` skips lines it has already reversed,
    so a repeated call cannot remove stock twice.
    """
    purchase = Purchase.objects.select_for_update().get(pk=purchase_id)

    if purchase.status == PurchaseStatus.CANCELLED:
        return purchase
    if purchase.status == PurchaseStatus.DRAFT:
        raise PurchaseConflict(
            "This bill was never posted, so there is nothing to cancel."
        )

    assert_period_open(purchase.business, purchase.bill_date)

    # Raises under BLOCK when the reversal would go below the floor; returns
    # quietly under ALLOW, where the caller warns instead.
    check_purchase_cancellation(purchase, purchase.business.negative_stock_policy)

    reverse_purchase(purchase, user=user)

    purchase.status = PurchaseStatus.CANCELLED
    purchase.cancelled_at = timezone.now()
    purchase.cancelled_by = user if (user and user.is_authenticated) else None
    purchase.cancellation_reason = (reason or "").strip()
    purchase.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "updated_at",
        ]
    )
    return purchase


def maybe_update_purchase_prices(purchase: Purchase) -> None:
    """
    Update catalogue purchase prices, but only when asked.

    Silently rewriting `Item.purchase_price` on every posted bill would change
    future costing without anyone deciding to, and a shop buying the same widget
    at three different prices would see the catalogue flip-flop. The flag is the
    user's decision, per bill.

    Only item-backed product lines are touched; a free-text freight line has no
    catalogue entry to update.
    """
    if not purchase.update_item_purchase_price:
        return

    from apps.inventory.models import Item

    for line in purchase.items.select_related("item").exclude(item__isnull=True):
        item = line.item
        if item is None or line.item_type != Item.ItemType.PRODUCT:
            continue
        if line.unit_price != item.purchase_price:
            item.purchase_price = line.unit_price
            item.save(update_fields=["purchase_price", "updated_at"])


def supplier_can_charge_tax(purchase: Purchase) -> bool:
    """
    May this bill carry tax at all?

    An unregistered or composition supplier issues bills with **no** tax and
    earns no input credit. This is a warning at entry time rather than a hard
    block - the user may be transcribing a real bill and the answer is still
    "no tax" - but the calculator must agree with the policy, or it will compute
    tax on a bill that must not carry any.

    Driven by the snapshot GSTIN, not the live `Party`: a supplier who registers
    today does not retroactively make last year's bills claimable.
    """
    return bool(purchase.supplier_gstin)


def recalculate_draft(purchase: Purchase, *, business) -> Purchase:
    """
    Recompute the `*_computed` columns and the divergence from stored inputs.

    Never alters the entered figures - that is the whole contract. Safe to call on
    a posted bill: it only refreshes what the calculator *thinks*, which is what
    lets `audit_purchases` report drift long after the fact.
    """
    from apps.purchases.services.draft import apply_computed

    lines = list(purchase.items.all())
    if not lines:
        return purchase

    inputs = [
        LineInput.build(
            quantity=line.quantity,
            unit_price=line.unit_price,
            tax_rate=line.tax_rate,
            line_discount=line.line_discount,
            item_type=line.item_type,
        )
        for line in lines
    ]

    totals = calculate_invoice(
        inputs,
        origin_state=purchase.origin_state_code or purchase.supplier_state_code or "",
        place_of_supply=purchase.place_of_supply or business.state_code or "",
        business_is_regular=supplier_can_charge_tax(purchase),
        prices_include_tax=purchase.prices_include_tax,
        # The supplier's rounding is theirs. Never impose ours on their figure.
        round_invoice_total=False,
        document_title=purchase.document_title,
    )

    apply_computed(purchase, totals)
    purchase.save(
        update_fields=[
            "taxable_total_computed",
            "cgst_total_computed",
            "sgst_total_computed",
            "igst_total_computed",
            "grand_total_computed",
            "max_divergence",
            "has_divergence",
            "updated_at",
        ]
    )
    return purchase


__all__ = [
    "cancel_purchase",
    "guard_duplicate",
    "maybe_update_purchase_prices",
    "post_purchase",
    "recalculate_draft",
    "supplier_can_charge_tax",
]