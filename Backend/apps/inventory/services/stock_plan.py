"""
Stock planning - pure logic, no database, no models (step 2.1.2).

Separated from the ledger service on purpose. The arithmetic that decides *what*
should move is the part most likely to be wrong, and the part hardest to test
once it is tangled up with ORM rows and locks. Here it is a pure function over
dataclasses, so every test is a `SimpleTestCase` - no database, no fixtures, no
transaction wrapping - and it runs in milliseconds.

Nothing in this module may import a Django model. That is the whole design: if it
can be proved correct without a database, there is no database-related way for it
to be wrong.

Quantities are `Decimal` with at most 3 decimal places throughout. A float can
represent 0.1 but not 0.001 exactly, and stock is counted in pieces, KG and
litres - so a float would silently lose precision on exactly the quantities this
ledger exists to track.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0.000")
#: Stock is Decimal(12, 3). More precision than that cannot be stored, so
#: accepting it would mean the ledger and the cache disagreeing.
MAX_DECIMALS = 3

#: Mirrors `BusinessProfile.NegativeStockPolicy` without importing it - this
#: module must not depend on Django. The service layer maps the model onto it.
POLICY_ALLOW = "ALLOW"
POLICY_BLOCK = "BLOCK"


class StockPlanError(ValueError):
    """The request cannot be planned at all (as opposed to being refused)."""


@dataclass(frozen=True)
class StockLine:
    """One invoice line that moves stock."""

    line_id: int
    item_id: int
    quantity: Decimal


@dataclass(frozen=True)
class MovementRequest:
    """A row the ledger service should write. Nothing is written here."""

    line_id: int
    item_id: int
    quantity_change: Decimal  # negative for a sale


@dataclass
class SalePlan:
    """What a sale will do, before anything is written."""

    movements: list[MovementRequest] = field(default_factory=list)
    #: item_id -> total quantity being sold. Decision 38: sufficiency is checked
    #: per ITEM, not per line. Three lines of 4 against stock of 10 each look fine
    #: on their own and sell 12.
    per_item_total: dict[int, Decimal] = field(default_factory=dict)

    @property
    def item_ids(self) -> list[int]:
        return sorted(self.per_item_total)


@dataclass(frozen=True)
class Shortage:
    item_id: int
    requested: Decimal
    available: Decimal

    @property
    def short_by(self) -> Decimal:
        return self.requested - self.available


@dataclass(frozen=True)
class NegativeWarning:
    """Under ALLOW: this sale takes the balance below zero."""

    item_id: int
    resulting_balance: Decimal


@dataclass(frozen=True)
class ReversalRequest:
    """Undo one SALE row."""

    item_id: int
    source_line_id: int
    quantity_change: Decimal  # positive: adds stock back


def _as_quantity(value) -> Decimal:
    """
    Coerce to Decimal and reject anything the ledger cannot store.

    Rejects floats explicitly rather than silently converting: `float` input
    means the caller is already off the `Decimal` track, and quietly accepting it
    is how `0.1 + 0.2 != 0.3` reaches a stock count.
    """
    if isinstance(value, float):
        raise StockPlanError(
            "Stock quantities must be Decimal or int, not float. A float cannot "
            "represent 0.001 exactly, and stock is counted to three decimals."
        )
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise StockPlanError(f"{value!r} is not a number") from exc

    if quantity != quantity:  # NaN
        raise StockPlanError("Quantity cannot be NaN")
    if quantity <= ZERO:
        raise StockPlanError(f"Quantity must be greater than zero, got {quantity}")
    if -quantity.as_tuple().exponent > MAX_DECIMALS:
        raise StockPlanError(
            f"Quantity {quantity} has more than {MAX_DECIMALS} decimal places; "
            "the ledger stores three."
        )
    return quantity.quantize(Decimal("0.001"))


def plan_sale(lines) -> SalePlan:
    """
    Work out what a sale removes, without removing anything.

    Ordering is by `(item_id, line_id)` and is deterministic: the ledger service
    walks items in ascending id order to take locks, so two invoices touching the
    same items must produce the same order or they can deadlock.
    """
    plan = SalePlan()
    normalised: list[StockLine] = []

    for raw in lines or ():
        quantity = _as_quantity(raw.quantity)
        normalised.append(
            StockLine(
                line_id=int(raw.line_id),
                item_id=int(raw.item_id),
                quantity=quantity,
            )
        )

    if not normalised:
        raise StockPlanError("A sale needs at least one line that moves stock.")

    # Deterministic order: item first (lock order), then line.
    for line in sorted(normalised, key=lambda item: (item.item_id, item.line_id)):
        plan.movements.append(
            MovementRequest(
                line_id=line.line_id,
                item_id=line.item_id,
                quantity_change=-line.quantity,
            )
        )
        plan.per_item_total[line.item_id] = (
            plan.per_item_total.get(line.item_id, ZERO) + line.quantity
        )

    return plan


def check_sufficiency(
    per_item_total: dict[int, Decimal],
    balances: dict[int, Decimal | None],
    policy: str = POLICY_ALLOW,
) -> tuple[list[Shortage], list[NegativeWarning]]:
    """
    Compare what is wanted against what is on hand.

    Returns `(shortages, warnings)`:
      * `BLOCK` -> the shortages that must stop the sale.
      * `ALLOW` -> the items whose balance would go below zero, with the
        resulting balance, so the user can be told the number.

    **Exactly equal stock is not a shortage.** Selling the last unit is the
    normal case, not an error. `0.001` short *is* a shortage.
    """
    if policy not in (POLICY_ALLOW, POLICY_BLOCK):
        raise StockPlanError(f"Unknown negative-stock policy: {policy!r}")

    shortages: list[Shortage] = []
    warnings: list[NegativeWarning] = []

    for item_id in sorted(per_item_total):
        requested = per_item_total[item_id]
        # A NULL balance means the item is not tracked; the caller should have
        # filtered those out. Treat it as zero rather than crashing.
        available = balances.get(item_id)
        available = ZERO if available is None else available

        if available < requested:
            if policy == POLICY_BLOCK:
                shortages.append(
                    Shortage(item_id=item_id, requested=requested, available=available)
                )
            else:
                warnings.append(
                    NegativeWarning(
                        item_id=item_id, resulting_balance=available - requested
                    )
                )

    return shortages, warnings


def plan_reversal(
    existing_sale_movements,
    existing_reversals,
) -> list[ReversalRequest]:
    """
    Undo sales that have not already been undone.

    Decision 35: a cancellation reverses *that invoice's own* SALE rows, and only
    ones with no matching reversal. Two things follow, and both matter:

      * Reversing nothing is a no-op, not an error - an invoice issued before the
        ledger existed has no SALE rows, and "add stock back" for it would
        **increase** stock that was never removed.
      * Reversing twice is a no-op too, so a double cancellation cannot add the
        same units twice.

    Reversal is always positive (stock comes back) and is never subject to the
    negative-stock policy (decision 36): a shopkeeper must always be able to void
    a wrong bill.
    """
    already_reversed = {
        int(row.source_line_id)
        for row in existing_reversals
        if getattr(row, "source_line_id", None) is not None
    }

    requests: list[ReversalRequest] = []
    for movement in existing_sale_movements:
        source_line_id = getattr(movement, "source_line_id", None)
        if source_line_id is None:
            # A SALE with no source line cannot be traced back to a line, so it
            # cannot be reversed reliably. Skip it rather than guess.
            continue
        if int(source_line_id) in already_reversed:
            continue
        requests.append(
            ReversalRequest(
                item_id=int(movement.item_id),
                source_line_id=int(source_line_id),
                quantity_change=abs(Decimal(str(movement.quantity_change))),
            )
        )

    # Same deterministic order as plan_sale, so reversal and sale take item
    # locks in the same sequence.
    return sorted(requests, key=lambda request: (request.item_id, request.source_line_id))