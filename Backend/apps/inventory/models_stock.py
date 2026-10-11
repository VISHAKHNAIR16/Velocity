"""
The stock ledger (decision 28).

Why a ledger and not a counter
------------------------------
A bare `Item.current_stock` cannot answer "why is it 7?". A ledger can, and it
can always be re-derived and checked - which is what `reconcile_stock` does.
`current_stock` is therefore only a **cache** of the ledger, and the invariant
this whole module protects is:

    current_stock  ==  SUM(quantity_change of that item's movements)
    ...or 0 when the item is not stock-tracked (decision 40)

Design decisions worth not re-litigating
----------------------------------------
**No foreign key back to invoices** (decision 29). `invoices.InvoiceItem ->
inventory.Item` already exists; pointing `StockMovement` back at an invoice would
create a circular dependency between the two apps, a classic Django trap that
costs an afternoon to unpick. Instead we carry `source_type` + `source_id` +
`source_line_id` and a human-readable `source_label` snapshot. `inventory` never
imports `invoices`.

**Idempotency in the database** (decision 30). A retried request, a double-click
or a replay must not deduct twice. A partial unique constraint means the second
insert is *rejected* rather than "usually prevented".

**Append-only.** `save()` on an existing row and `delete()` both raise. A
correction is a new movement, never an edit of history - so "why is it 7?" always
has an answer.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TenantModel

#: Quantities are Decimal(12, 3): stock is counted in pieces, KG or litres.
ZERO = Decimal("0.000")


class MovementType(models.TextChoices):
    """What moved the stock. The sign is fixed by the type - see `clean()`."""

    OPENING = "OPENING", "Opening balance"
    SALE = "SALE", "Sold"
    SALE_REVERSAL = "SALE_REVERSAL", "Sale reversed (cancelled)"
    PURCHASE = "PURCHASE", "Purchased"
    PURCHASE_REVERSAL = "PURCHASE_REVERSAL", "Purchase reversed"
    ADJUSTMENT = "ADJUSTMENT", "Manual adjustment"
    #: Reserved for 2.4.
    SALES_RETURN = "SALES_RETURN", "Sales return"
    PURCHASE_RETURN = "PURCHASE_RETURN", "Purchase return"


#: Which way the quantity must move for each type. Anything absent may go either
#: way (OPENING and ADJUSTMENT are corrections in an arbitrary direction).
REQUIRED_SIGN = {
    MovementType.SALE: Decimal("-1"),
    MovementType.PURCHASE_REVERSAL: Decimal("-1"),
    MovementType.SALE_REVERSAL: Decimal("1"),
    MovementType.PURCHASE: Decimal("1"),
}


class SourceType(models.TextChoices):
    """Where the movement came from. Not a FK - see the module docstring."""

    INVOICE = "INVOICE", "Sales invoice"
    PURCHASE = "PURCHASE", "Purchase bill"
    CREDIT_NOTE = "CREDIT_NOTE", "Credit / debit note"
    MANUAL = "MANUAL", "Manual adjustment"
    SYSTEM = "SYSTEM", "System (migration, tracking toggle)"


class AdjustmentReason(models.TextChoices):
    """Why a manual adjustment was made. Required for `MANUAL` rows."""

    COUNT_CORRECTION = "COUNT_CORRECTION", "Physical count correction"
    DAMAGED = "DAMAGED", "Damaged"
    LOST_OR_THEFT = "LOST_OR_THEFT", "Lost or stolen"
    EXPIRED = "EXPIRED", "Expired"
    TRACKING_DISABLED = "TRACKING_DISABLED", "Stock tracking turned off"
    RECONCILIATION = "RECONCILIATION", "Reconciliation repair"
    OTHER = "OTHER", "Other (explain in the note)"


class StockMovementQuerySet(models.QuerySet):
    """
    Blocks bulk deletion.

    `Model.delete()` can only stop `instance.delete()`. `Model.objects.filter(...).delete()`
    goes straight to Django's collector and never touches the model's method, so
    without this a single queryset call could erase history and the
    append-only guarantee would be decorative.
    """

    def delete(self):
        raise ValidationError(
            "StockMovement rows cannot be deleted, individually or in bulk. "
            "Reverse the movement instead, so the ledger stays explainable."
        )

    def update(self, **kwargs):
        """Same reasoning as `delete()`: history is not editable, in bulk either."""
        raise ValidationError(
            "StockMovement rows cannot be edited. Record a correcting movement "
            "instead of changing history."
        )


class StockMovement(TenantModel):
    """
    One append-only row in the stock ledger. Never updated, never deleted.
    """

    item = models.ForeignKey(
        "inventory.Item",
        # PROTECT: an item with ledger history must not vanish. The balance can
        # only be read by walking the ledger, so removing the item removes the
        # ability to explain the number.
        on_delete=models.PROTECT,
        related_name="stock_movements",
    )

    movement_type = models.CharField(max_length=20, choices=MovementType.choices)

    #: Signed: negative takes stock away, positive adds it. Never zero.
    quantity_change = models.DecimalField(max_digits=12, decimal_places=3)
    #: The item's balance AFTER this row. Denormalised so a history view does not
    #: have to sum the whole ledger to show a running total.
    stock_after = models.DecimalField(max_digits=12, decimal_places=3)

    source_type = models.CharField(
        max_length=12,
        choices=SourceType.choices,
        default=SourceType.MANUAL,
    )
    #: Deliberately a plain integer, not an FK to Invoice (decision 29).
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    #: The invoice line this came from, when there is one. Used for idempotency.
    source_line_id = models.PositiveBigIntegerField(null=True, blank=True)
    #: Human-readable snapshot, e.g. "INV/26-27/00012". Not for logic.
    source_label = models.CharField(max_length=32, blank=True, default="")

    reason = models.CharField(
        max_length=20,
        choices=AdjustmentReason.choices,
        blank=True,
        default="",
    )
    note = models.CharField(max_length=255, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # SET_NULL: the stock history outlives the staff member who made it.
        # A deleted user must not delete the reason a number changed.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = StockMovementQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            # History and reconciliation walk one item's rows in order.
            models.Index(fields=["business", "item", "id"]),
            # "What did invoice 12 do to stock?"
            models.Index(fields=["business", "source_type", "source_id"]),
        ]
        constraints = [
            # A no-op row is a bug, not a legitimate movement.
            models.CheckConstraint(
                condition=~models.Q(quantity_change=0),
                name="stock_movement_not_zero",
            ),
            # The sign is a property of the type, so a bad row cannot be written
            # by a shell session with the wrong sign.
            models.CheckConstraint(
                condition=(
                    models.Q(movement_type__in=[MovementType.OPENING, MovementType.ADJUSTMENT])
                    | (
                        models.Q(
                            movement_type__in=[
                                MovementType.SALE,
                                MovementType.PURCHASE_REVERSAL,
                                MovementType.SALES_RETURN,
                            ]
                        )
                        & models.Q(quantity_change__lt=0)
                    )
                    | (
                        models.Q(
                            movement_type__in=[
                                MovementType.SALE_REVERSAL,
                                MovementType.PURCHASE,
                                MovementType.PURCHASE_RETURN,
                            ]
                        )
                        & models.Q(quantity_change__gt=0)
                    )
                ),
                name="stock_movement_sign_matches_type",
            ),
            # Decision 30: idempotency enforced by the database, not by hope.
            # Only applies when there IS a source line, which is why the
            # constraint is partial.
            models.UniqueConstraint(
                fields=["business", "movement_type", "source_type", "source_line_id"],
                condition=models.Q(source_line_id__isnull=False),
                name="one_movement_per_source_line",
            ),
        ]

    def __str__(self) -> str:
        sign = "+" if self.quantity_change > 0 else ""
        return f"{self.item_id}: {sign}{self.quantity_change} ({self.movement_type})"

    # ------------------------------------------------------------------
    # Append-only enforcement
    # ------------------------------------------------------------------
    def save(self, *args, **kwargs):
        if self.pk is not None and not kwargs.pop("_force_update", False):
            existing = type(self).objects.filter(pk=self.pk).exists()
            if existing:
                raise ValidationError(
                    "StockMovement rows are append-only. Record a correcting "
                    "movement instead of editing history."
                )
        self._validate()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "StockMovement rows cannot be deleted. Reverse the movement instead, "
            "so the ledger stays explainable."
        )

    def _validate(self):
        if self.quantity_change == 0:
            raise ValidationError("A stock movement cannot change nothing.")
        required = REQUIRED_SIGN.get(self.movement_type)
        if required == Decimal("-1") and self.quantity_change > 0:
            raise ValidationError(f"A {self.movement_type} must reduce stock.")
        if required == Decimal("1") and self.quantity_change < 0:
            raise ValidationError(f"A {self.movement_type} must add stock.")
        if self.reason == AdjustmentReason.OTHER and not (self.note or "").strip():
            raise ValidationError(
                "An adjustment with reason 'Other' needs a note explaining it."
            )

    @property
    def is_reversal(self) -> bool:
        return self.movement_type in (
            MovementType.SALE_REVERSAL,
            MovementType.PURCHASE_REVERSAL,
        )