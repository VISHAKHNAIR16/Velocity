"""
Purchase models for the Velocity GST Billing platform.

A Purchase is a bill received from a supplier party. Design rules mirror the
invoice patterns established in 1.x and Phase 3:

1. **Every number is computed, never accepted.** The request body carries
   inputs (supplier party, date, lines, discounts); the server runs the GST
   calculation and writes the result. All money fields are read_only on the
   serializer.

2. **Snapshots freeze history.** Supplier name/GSTIN/address and business
   name/address/GSTIN are copied onto the purchase bill when it is issued, so
   renaming a supplier or the business later cannot rewrite a document that was
   already received from a vendor.

3. **Append-only movements.** Posting a purchase creates a `PURCHASE`
   StockMovement per line; posting a purchase return creates a `PURCHASE_RETURN`
   movement. Existing rows are never edited - corrections are new rows.

4. **Tenancy.** Every purchase belongs to exactly one business, enforced by
   TenantModel and TenantPrimaryKeyRelatedField.
"""

from decimal import Decimal

from django.db import models

from apps.core.constants import GST_STATE_CHOICES
from apps.core.models import TenantModel
from apps.inventory.models import Item
from apps.parties.models import Party

ZERO = Decimal("0.00")


class PurchaseStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ISSUED = "ISSUED", "Issued"
    CANCELLED = "CANCELLED", "Cancelled"


class Purchase(TenantModel):
    """
    A supplier purchase bill.

    Lifecycle: DRAFT (editable) -> ISSUED (frozen) -> CANCELLED.
    """

    Status = PurchaseStatus

    # --- Who and when --------------------------------------------------
    party = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="purchases",
        help_text="Supplier party. Only SUPPLIER or BOTH parties should appear here.",
    )
    purchase_date = models.DateField(default=None, null=True, blank=True)
    bill_number = models.CharField(
        max_length=64, blank=True, default="", help_text="Supplier's own bill/invoice number."
    )

    # --- Supply ---------------------------------------------------------
    place_of_supply = models.CharField(
        max_length=2,
        blank=True,
        default="",
        choices=GST_STATE_CHOICES,
        help_text="Place of supply state code (the supplier's state for a purchase).",
    )
    supply_type = models.CharField(
        max_length=5,
        choices=[
            ("INTRA", "Intra-state (CGST + SGST/UTGST)"),
            ("INTER", "Inter-state (IGST)"),
        ],
        default="INTRA",
    )
    prices_include_tax = models.BooleanField(default=False)

    # --- Lifecycle ------------------------------------------------------
    status = models.CharField(
        max_length=10, choices=PurchaseStatus.choices, default=PurchaseStatus.DRAFT, db_index=True
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default="")

    # --- Snapshots (frozen at issue) ------------------------------------
    supplier_name = models.CharField(max_length=200, blank=True, default="")
    supplier_gstin = models.CharField(max_length=15, blank=True, default="")
    supplier_state_code = models.CharField(max_length=2, blank=True, default="")
    supplier_address = models.TextField(blank=True, default="")
    business_name = models.CharField(max_length=200, blank=True, default="")
    business_address = models.TextField(blank=True, default="")
    business_gstin = models.CharField(max_length=15, blank=True, default="")
    business_state_code = models.CharField(max_length=2, blank=True, default="")

    # --- Money (server-computed only) -----------------------------------
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_discount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    taxable_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    cgst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    round_off = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)

    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-purchase_date", "-id"]
        indexes = [
            models.Index(fields=["business", "purchase_date"]),
            models.Index(fields=["business", "status"]),
            models.Index(fields=["business", "party"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(grand_total__gte=0), name="purchase_grand_total_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=0), name="purchase_subtotal_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.bill_number or 'Draft'} - {self.party_id and self.party.name}"

    @property
    def is_draft(self) -> bool:
        return self.status == self.Status.DRAFT

    @property
    def is_issued(self) -> bool:
        return self.status == self.Status.ISSUED

    @property
    def tax_total(self) -> Decimal:
        return self.cgst_total + self.sgst_total + self.igst_total

    @property
    def display_number(self) -> str:
        return self.bill_number or "Draft"


class PurchaseItem(TenantModel):
    """
    One line on a purchase bill.

    Item details are snapshotted on save and never re-read from the item
    master, so changing an item later cannot reinterpret a bill that was
    already recorded.
    """

    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_items"
    )

    # --- Snapshots ------------------------------------------------------
    item_name = models.CharField(max_length=200)
    item_type = models.CharField(max_length=8, default="PRODUCT")
    hsn_sac_code = models.CharField(max_length=8, blank=True, default="")
    unit = models.CharField(max_length=8, blank=True, default="")

    # --- Inputs ---------------------------------------------------------
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_discount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2)

    # --- Computed (server only) -----------------------------------------
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    cgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(item__isnull=False)
                    | (
                        models.Q(item_name__gt="")
                        & models.Q(item_type__gt="")
                        & models.Q(hsn_sac_code__gt="")
                        & models.Q(unit__gt="")
                    )
                ),
                name="purchase_item_is_item_backed_or_self_describing",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="purchase_item_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0), name="purchase_item_unit_price_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(line_discount__gte=0),
                name="purchase_item_line_discount_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_rate__gte=0), name="purchase_item_tax_rate_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(taxable_value__gte=0),
                name="purchase_item_taxable_value_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=0), name="purchase_item_total_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item_name} x {self.quantity}"

    @property
    def gross_amount(self) -> Decimal:
        from apps.core.money import q2

        return q2(self.quantity * self.unit_price)

    @property
    def net_amount(self) -> Decimal:
        from apps.core.money import q2

        return q2(self.gross_amount - self.line_discount)

    @property
    def tracks_stock(self) -> bool:
        return self.item_type == "PRODUCT"


class PurchaseReturn(TenantModel):
    """
    A purchase return (debit note to supplier).

    Links back to the original purchase bill and reverses stock + tax pro-rata.
    """

    Status = PurchaseStatus

    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="returns")
    return_date = models.DateField(default=None, null=True, blank=True)
    return_number = models.CharField(max_length=64, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=PurchaseStatus.choices, default=PurchaseStatus.ISSUED, db_index=True
    )

    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    cgst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)

    class Meta:
        ordering = ["-return_date", "-id"]
        indexes = [
            models.Index(fields=["business", "return_date"]),
            models.Index(fields=["business", "purchase"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(grand_total__gte=0),
                name="purchase_return_grand_total_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.return_number or 'Draft'} - return of {self.purchase_id}"

    @property
    def tax_total(self) -> Decimal:
        return self.cgst_total + self.sgst_total + self.igst_total
