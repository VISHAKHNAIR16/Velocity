"""
Advisory stock availability for the billing form (tasks.md 2.1.4).

Separate from `apps/inventory/services/stock.py` on purpose. That module is the
ledger - it locks, it writes, it decides. This module reads, advises, and decides
nothing. Keeping them apart makes the boundary checkable: if a preview path ever
starts importing `reserve_sale`, something is wrong.
"""

from decimal import Decimal

from apps.inventory.models import Item


def stock_check_for_lines(business, lines):
    """
    Return one advisory row per tracked product line.

        {item_id, item_name, unit, available, requested, stock_after,
         will_go_negative, shortfall}

    **Never authoritative.** No lock, no write. `/invoices/preview/` runs on every
    debounce while the user types, so taking `select_for_update()` here would
    turn typing into lock contention against real sales. Between this reading and
    the issue request another sale may commit and change the answer, so the only
    thing that can actually refuse an invoice is `reserve_sale`.

    Quantities are aggregated per item before comparing, matching
    `stock_plan.plan`. Three lines of 4 against a stock of 10 look fine line by
    line and are short by 2 in total; a check that only ever compares one line at
    a time reports "fine" for precisely the case the shopkeeper needs to be warned
    about. Getting this wrong is not a cosmetic bug - it is a warning system that
    reliably stays quiet exactly when it matters.

    Lines that cannot move stock (services, free-text rows, untracked products)
    are omitted rather than reported as available. "No warning" and "in stock"
    are different claims, and only the first is true.

    Rows are sorted so the ones that need attention come first: the frontend
    renders the top of the list in a fixed space.
    """
    from apps.inventory.models import ItemType

    requested: dict[int, Decimal] = {}
    names: dict[int, str] = {}
    units: dict[int, str] = {}

    for line in lines:
        item = getattr(line, "item", None)
        if item is None or not getattr(item, "track_stock", False):
            continue

        snapshot_type = getattr(line, "item_type_snapshot", None)
        if snapshot_type is None and item is not None:
            snapshot_type = item.item_type
        if snapshot_type != ItemType.PRODUCT:
            continue

        quantity = Decimal(str(getattr(line, "quantity", 0) or 0))
        requested[item.pk] = requested.get(item.pk, Decimal("0")) + quantity
        names.setdefault(item.pk, item.name)
        units.setdefault(item.pk, getattr(item, "unit", "") or "")

    if not requested:
        return []

    # One read for every item, not one per line. `business` is deliberately not
    # used as a filter here: the lines were already resolved through tenant-scoped
    # serializers, so an id from another tenant cannot appear here, and filtering
    # again would only hide a bug upstream.
    balances = dict(Item.objects.filter(pk__in=requested).values_list("pk", "current_stock"))

    checks = []
    for item_id, needed in requested.items():
        available = balances.get(item_id)
        if available is None:
            continue
        after = available - needed
        checks.append(
            {
                "item_id": item_id,
                "item_name": names.get(item_id, ""),
                "unit": units.get(item_id, ""),
                "available": str(available),
                "requested": str(needed),
                "stock_after": str(after),
                "will_go_negative": after < 0,
                "shortfall": str(max(Decimal("0"), -after)),
                # Under BLOCK the issue will be refused outright; under ALLOW it
                # will go through. The frontend uses this to say "will be
                # rejected" rather than a vague warning.
                "will_be_rejected": bool(
                    after < 0 and business.negative_stock_policy == "BLOCK"
                ),
            }
        )

    checks.sort(key=lambda row: (not row["will_go_negative"], row["item_name"]))
    return checks