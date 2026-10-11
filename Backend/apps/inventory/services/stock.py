"""
The stock ledger service (step 2.1.3).

Everything that *writes* stock lives here. `stock_plan` decides what should move;
this decides when it may move and records that it did.

The load-bearing invariant, which `reconcile_stock` checks and the tests pin:

    Item.current_stock  ==  SUM(StockMovement.quantity_change for that item)
    ...or 0 when the item is not stock-tracked (decision 40)

`current_stock` is a cache. Treat it as the source of truth and the ledger becomes
decorative.

Locking (decision 32)
---------------------
Two-phase, so the invoice's `source_label` can be the real invoice number while
the counter is still locked *last*:

    1. `reserve_sale`  - lock items ascending, check sufficiency, build the plan.
                         **Writes nothing.** A failed stock check therefore never
                         touches the counter.
    2. (caller locks the counter and allocates the number)
    3. `commit_sale`   - write the movements and update the cached balances.

Items are locked in ascending id order, never invoice-line order: two invoices
selling items A,B and B,A would otherwise deadlock.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.core.errors import StockError

from ..models_stock import AdjustmentReason, MovementType, SourceType, StockMovement
from .stock_plan import (
    POLICY_ALLOW,
    POLICY_BLOCK,
    SalePlan,
    StockPlanError,
    check_sufficiency,
    plan_reversal,
    plan_sale,
)

ZERO = Decimal("0.000")


class InsufficientStock(StockError):
    """
    A `BLOCK`-policy business tried to sell more than it has.

    Carries per-item detail so the API can tell the user *which* line and by how
    much, instead of a flat "not enough stock".
    """

    def __init__(self, shortages):
        self.shortages = list(shortages)
        first = self.shortages[0] if self.shortages else None
        message = (
            f"Only {first.available} in stock for one item, but {first.requested} "
            "are needed."
            if first
            else "Not enough stock."
        )
        super().__init__("INSUFFICIENT_STOCK", message, field="items")


class ItemInactive(StockError):
    """An invoice line sells something that is no longer sellable."""

    def __init__(self, item_names):
        self.item_names = list(item_names)
        super().__init__(
            "ITEM_INACTIVE",
            f"These items are inactive and cannot be sold: {', '.join(self.item_names)}.",
            field="items",
        )


def _policy_for(business) -> str:
    return POLICY_BLOCK if business.negative_stock_policy == "BLOCK" else POLICY_ALLOW


def _stock_lines_for_invoice(invoice) -> list:
    """
    Which lines move stock (decision 37).

    Only lines that are BOTH item-backed AND a snapshot PRODUCT AND tracked.
    Free-text lines, services and untracked products are skipped silently - a
    freight charge has no stock, and refusing to invoice one would be absurd.

    Quantity is in the item's own unit. **No unit conversion in 2.1** (decision
    37): selling in dozens against stock in pieces needs a conversion model that
    is explicitly out of scope, and guessing here would be worse than not having
    it.
    """
    from ..models import ItemType

    rows = (
        invoice.items.select_related("item")
        .filter(item__isnull=False)
        .order_by("item_id", "id")
    )
    lines = []
    for line in rows:
        item = line.item
        if item is None or not item.track_stock:
            continue
        if line.item_type != ItemType.PRODUCT:
            continue
        lines.append(
            type("PlanLine", (), {"line_id": line.pk, "item_id": item.pk, "quantity": line.quantity})()
        )
    return lines


def reserve_sale(invoice):
    """
    Phase 1: lock the items, check stock, build the plan. **Writes nothing.**

    Must run inside the caller's `@transaction.atomic`, together with the number
    allocation and `commit_sale`, so a failure anywhere rolls the whole issue
    back with no half-deduction.

    Returns a `SalePlan`, or raises `InsufficientStock` / `ItemInactive`.
    """
    if not transaction.get_connection().in_atomic_block:
        raise AssertionError(
            "reserve_sale() must run inside a transaction; otherwise a rollback "
            "would leave item locks or partial deductions behind."
        )

    business = invoice.business
    lines = _stock_lines_for_invoice(invoice)
    if not lines:
        # Nothing moves (free-text, services, untracked). An empty plan is the
        # honest answer - `commit_sale` writes nothing for it.
        return SalePlan()

    item_ids = sorted({line.item_id for line in lines})
    # Ascending id, ALWAYS. Locking in line order is the classic deadlock.
    items = _lock_items(business, item_ids)

    inactive = [items[i].name for i in item_ids if i in items and not items[i].is_active]
    if inactive:
        raise ItemInactive(inactive)

    plan = plan_sale(lines)

    balances = {pk: (items[pk].current_stock if pk in items else None) for pk in item_ids}
    shortages, warnings = check_sufficiency(
        plan.per_item_total, balances, _policy_for(business)
    )
    if shortages:
        # BLOCK policy only - ALLOW returns warnings, never raises.
        raise InsufficientStock(shortages)

    plan.warnings = warnings
    return plan


def _lock_items(business, item_ids):
    """
    Lock the items, ascending, and confirm every one belongs to this business.

    Defence in depth: the invoice lines were already scoped to this business by
    the tenant queryset, but the ledger is the place where a wrong item id would
    quietly corrupt another shop's stock, so it is re-checked here rather than
    trusted.
    """
    from ..models import Item

    return {
        item.pk: item
        for item in Item.objects.filter(business=business, pk__in=item_ids)
        .select_for_update()
        .order_by("pk")
    }


def commit_sale(invoice, plan, label: str, user=None) -> list[StockMovement]:
    """
    Phase 2: write the movements and update the cached balances.

    Called after the invoice number is known, so every row carries the real
    `source_label`. The item locks from `reserve_sale` are still held (same
    transaction), so the balances cannot have moved underneath this.
    """
    if not getattr(plan, "movements", None):
        return []

    movements = []
    running: dict[int, Decimal] = {}
    business = invoice.business

    for request in plan.movements:
        from ..models import Item

        item = Item.objects.select_for_update().get(pk=request.item_id)
        balance = running.get(request.item_id, item.current_stock or ZERO) + request.quantity_change
        running[request.item_id] = balance

        movements.append(
            StockMovement.objects.create(
                business=business,
                item=item,
                movement_type=MovementType.SALE,
                quantity_change=request.quantity_change,
                stock_after=balance,
                source_type=SourceType.INVOICE,
                source_id=invoice.pk,
                source_line_id=request.line_id,
                source_label=label[:32],
                created_by=user if (user and user.is_authenticated) else None,
            )
        )

    _write_balances(business, running)
    return movements


def _write_balances(business, balances: dict[int, Decimal]):
    from ..models import Item

    for item_id, balance in balances.items():
        Item.objects.filter(pk=item_id, business=business).update(current_stock=balance)


def reverse_sale(invoice, user=None) -> list[StockMovement]:
    """
    Undo this invoice's own sales (decision 35).

    Derived from the ledger, never from "restore the quantities on the invoice
    lines": an invoice issued before the ledger existed has no SALE rows, and
    restoring its lines would ADD stock that was never removed.

    Never raises for stock reasons (decision 36) - a shopkeeper must always be
    able to void a wrong bill.
    """
    sales = list(
        StockMovement.objects.filter(
            business=invoice.business,
            source_type=SourceType.INVOICE,
            source_id=invoice.pk,
            movement_type=MovementType.SALE,
        ).order_by("item_id", "id")
    )
    if not sales:
        return []

    reversals = list(
        StockMovement.objects.filter(
            business=invoice.business,
            source_type=SourceType.INVOICE,
            source_id=invoice.pk,
            movement_type=MovementType.SALE_REVERSAL,
        )
    )
    try:
        requests = plan_reversal(sales, reversals)
    except StockPlanError:
        # Reversal must never fail on a bad stored quantity: a wrong reversal is
        # worse than none, and the plan is idempotent by source line anyway.
        return []
    if not requests:
        return []


    item_ids = sorted({request.item_id for request in requests})
    items = _lock_items(invoice.business, item_ids)

    movements = []
    running: dict[int, Decimal] = {}
    for request in requests:
        item = items.get(request.item_id)
        if item is None:
            continue
        balance = running.get(request.item_id, item.current_stock or ZERO) + request.quantity_change
        running[request.item_id] = balance
        movements.append(
            StockMovement.objects.create(
                business=invoice.business,
                item=item,
                movement_type=MovementType.SALE_REVERSAL,
                quantity_change=request.quantity_change,
                stock_after=balance,
                source_type=SourceType.INVOICE,
                source_id=invoice.pk,
                source_line_id=request.source_line_id,
                source_label=(invoice.display_number or "")[:32],
                created_by=user if (user and user.is_authenticated) else None,
            )
        )

    _write_balances(invoice.business, running)
    return movements


def adjust_stock(
    business,
    item,
    *,
    mode: str = "SET",
    quantity=None,
    reason: str = "",
    note: str = "",
    user=None,
) -> StockMovement:
    """
    Record a manual correction.

    `mode="SET"` makes the balance equal `quantity`; `mode="CHANGE"` moves it by
    `quantity` (which may be negative). Either way the ledger records the
    **difference**, so a SET that changes nothing writes nothing.

    Wrapped in its own transaction: `_lock_items` uses `select_for_update()`,
    which raises `TransactionManagementError` outside one. That failure is
    invisible on SQLite (no row locking, so the guard never trips) and is a 500 on
    PostgreSQL - the exact "local is more forgiving" trap this project has hit
    twice already.
    """
    with transaction.atomic():
        return _adjust_stock_locked(
            business,
            item,
            mode=mode,
            quantity=quantity,
            reason=reason,
            note=note,
            user=user,
        )


def _adjust_stock_locked(business, item, *, mode, quantity, reason, note, user):
    if mode not in ("SET", "CHANGE"):
        raise StockError("STOCK_MODE_INVALID", "mode must be SET or CHANGE.")

    target = _lock_items(business, [item.pk]).get(item.pk)
    if target is None:
        raise StockError("ITEM_NOT_FOUND", "That item does not exist in this business.")

    current = target.current_stock or ZERO
    if mode == "SET":
        wanted = Decimal(str(quantity))
        delta = wanted - current
    else:
        delta = Decimal(str(quantity))
        wanted = current + delta

    if delta == 0:
        # Writing a no-op movement would violate the `stock_movement_not_zero`
        # constraint and pollute the history for no reason.
        return None

    target.current_stock = wanted
    target.save(update_fields=["current_stock", "updated_at"])

    return StockMovement.objects.create(
        business=business,
        item=target,
        movement_type=MovementType.ADJUSTMENT,
        quantity_change=delta,
        stock_after=wanted,
        source_type=SourceType.MANUAL,
        source_label="MANUAL ADJUSTMENT",
        reason=reason or AdjustmentReason.OTHER,
        note=note,
        created_by=user if (user and user.is_authenticated) else None,
    )


def set_tracking(business, item, enabled: bool, opening_quantity=None, user=None) -> StockMovement:
    """
    Turning tracking on or off is itself a movement (decision 40).

    Disabling writes a closing adjustment that brings the ledger sum to 0 and then
    nulls the balance. Enabling writes an OPENING. Without this the invariant
    `sum(movements) == current_stock` would break in both directions, and
    `reconcile_stock` would report drift that was really just a settings change.

    Own transaction, for the same reason as `adjust_stock`.
    """
    with transaction.atomic():
        return _set_tracking_locked(business, item, enabled, opening_quantity, user)


def _set_tracking_locked(business, item, enabled, opening_quantity, user):

    target = _lock_items(business, [item.pk]).get(item.pk)
    if target is None:
        raise StockError("ITEM_NOT_FOUND", "That item does not exist in this business.")

    if enabled:
        opening = Decimal(str(opening_quantity if opening_quantity is not None else 0))
        target.track_stock = True
        target.current_stock = opening
        target.save(update_fields=["track_stock", "current_stock", "updated_at"])
        if opening == 0:
            return None
        return StockMovement.objects.create(
            business=business,
            item=target,
            movement_type=MovementType.OPENING,
            quantity_change=opening,
            stock_after=opening,
            source_type=SourceType.SYSTEM,
            source_label="TRACKING ENABLED",
            reason=AdjustmentReason.COUNT_CORRECTION,
            note="Stock tracking enabled",
            created_by=user if (user and user.is_authenticated) else None,
        )

    balance = target.current_stock or ZERO
    target.track_stock = False
    target.current_stock = None
    target.low_stock_threshold = None
    target.save(
        update_fields=["track_stock", "current_stock", "low_stock_threshold", "updated_at"]
    )
    if balance == 0:
        return None
    return StockMovement.objects.create(
        business=business,
        item=target,
        movement_type=MovementType.ADJUSTMENT,
        quantity_change=-balance,
        stock_after=ZERO,
        source_type=SourceType.SYSTEM,
        source_label="TRACKING DISABLED",
        reason=AdjustmentReason.TRACKING_DISABLED,
        note="Stock tracking turned off; balance closed to zero",
        created_by=user if (user and user.is_authenticated) else None,
    )


def ledger_balance(item) -> Decimal:
    """The authoritative balance: the sum of the ledger, not the cache."""
    total = item.stock_movements.aggregate(total=Sum("quantity_change"))["total"]
    return total or ZERO


def reconcile(business, fix: bool = False, user=None) -> list[dict]:
    """
    Compare every tracked item's cached balance with its ledger sum.

    On drift the **ledger wins** (decision 28): `current_stock` is a cache, so the
    cache is set to the ledger's value and nothing else happens.

    Deliberately writes NO correcting movement. The obvious version - append an
    ADJUSTMENT for `cached - ledger` as a "trace" - is wrong: writing the movement
    *changes the very ledger sum it was reconciling to*, so the two still disagree
    afterwards. Pinned by the idempotency test.

    The trace is the command's own output, which prints cached -> ledger for every
    item it changed, plus the fact that a later `reconcile` stays clean. A ledger
    entry claiming stock moved when no stock moved would be a worse lie than no
    entry at all.
    """
    from ..models import Item

    drift = []
    items = Item.objects.filter(business=business, track_stock=True).order_by("pk")

    for item in items.iterator():
        cached = item.current_stock or ZERO
        summed = ledger_balance(item)
        if cached == summed:
            continue

        drift.append(
            {
                "item_id": item.pk,
                "item": item.name,
                "cached": str(cached.quantize(Decimal("0.001"))),
                "ledger": str(summed.quantize(Decimal("0.001"))),
                "difference": str((cached - summed).quantize(Decimal("0.001"))),
            }
        )

        if fix:
            _write_balances(business, {item.pk: summed})
            drift[-1]["fixed"] = True

    return drift