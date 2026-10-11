"""
Purchase models — what the business BUYS (phase 2.2).

Three rules make this different from `Invoice`, and every oddity below follows
from one of them.

1. **Amounts are entered, not computed.**
   For a sales invoice the server calculates every figure and the request body is
   ignored if it disagrees. That is right for sales: we author the document. It
   is wrong for purchases. The supplier authored *their* bill, their return has to
   match it, and their rounding can legitimately differ from ours by a paisa. So
   the numbers a user types are the truth and are stored verbatim.

   The calculator still runs, but as a **validator**: its result is stored
   alongside in `*_computed` columns and the gap is recorded in
   `max_divergence`. Storing the discarded figure is the point — months later,
   `audit_purchases` can tell whether a supplier's rounding was a one-off or
   something they do every single bill, which is the question that matters when
   the 4.2 GSTR-3B numbers have to reconcile.

2. **`financial_year` is a stored column, not a derived one.**
   The duplicate-entry guard is a database constraint on
   `(business, supplier, bill_number, financial_year)`. A `UniqueConstraint`
   cannot contain a derived expression, so the FY is written from `bill_date` on
   every save. Without it the guard would have to live in Python, where a race
   lets two identical bills through — the exact class of bug the 1.4.4
   concurrency work was about.

3. **Snapshots freeze the supplier.**
   `supplier_name`, `supplier_gstin`, `supplier_state_code` and
   `supplier_address` are copied at entry, for the same reason `Invoice` snapshots
   its recipient: renaming a supplier later must not rewrite a bill that has
   already been filed and claimed as input tax credit.

A purchase has **no document number of its own**. The supplier's bill number is
the identifier. An internal `PUR/YY-YY/NNNNN` series was considered and dropped —
it would have forced a `series` column onto `InvoiceCounter` and rewritten its
`(business, financial_year)` uniqueness, to produce a number nothing reads.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.constants import GST_STATE_CHOICES
from apps.core.fiscal import financial_year
from apps.core.models import TenantModel
from apps.inventory.models import Item

ZERO = Decimal("0.00")


class PurchaseStatus(models.TextChoices):
    """
    Module level for the same reason as `InvoiceStatus`: the lifecycle
    `CheckConstraint`s in `Purchase.Meta` reference these values from inside the
    class body, and a nested class is not bound while its own body is executing.
    `Purchase.Status` is aliased below so `Purchase.Status.DRAFT` works.
    """

    DRAFT = "DRAFT", "Draft"
    POSTED = "POSTED", "Posted"
    CANCELLED = "CANCELLED", "Cancelled"


class Purchase(TenantModel):
    """
    A supplier bill. `DRAFT` is editable; `POSTED` is a filed document that has
    already increased stock; `CANCELLED` has reversed that increase.
    """

    Status = PurchaseStatus

    # --- Who ----------------------------------------------------------
    # PROTECT, never CASCADE: a supplier with a purchase history cannot be
    # deleted out from under it. That is the same rule that stopped a business
    # being deleted with all its invoices (2.0.10).
    supplier = models.ForeignKey(
        "parties.Party",
        on_delete=models.PROTECT,
        related_name="purchases",
    )

    # --- The document -------------------------------------------------
    bill_number = models.CharField(
        max_length=40,
        help_text="The number printed on the supplier's bill. This is how the "
        "bill is identified - there is no internal purchase series.",
    )
    #: Stored, not derived - see the module docstring. Written from `bill_date`.
    financial_year = models.CharField(max_length=7, editable=False, db_index=True)

    bill_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=10,
        choices=PurchaseStatus.choices,
        default=PurchaseStatus.DRAFT,
        db_index=True,
    )

    # --- Tax placement -------------------------------------------------
    #: Where the goods come FROM. The supplier's state, snapshotted.
    origin_state_code = models.CharField(
        max_length=2,
        choices=GST_STATE_CHOICES,
        help_text="The supplier's state. Determines CGST+SGST vs IGST credit.",
    )
    #: Where the goods come TO. Always the business's own state for a purchase.
    place_of_supply = models.CharField(
        max_length=2,
        choices=GST_STATE_CHOICES,
        help_text="The business's own state. Always this for a purchase.",
    )
    supply_type = models.CharField(
        max_length=5,
        choices=[("INTRA", "Intra-state"), ("INTER", "Inter-state")],
        default="INTRA",
    )

    prices_include_tax = models.BooleanField(default=False)

    # --- Money, ENTERED as printed on the supplier's bill ---------------
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_discount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    taxable_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    cgst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    round_off = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)

    # --- The same figures as the calculator sees them ------------------
    # Never overwritten into the entered columns. Read by `audit_purchases` to
    # report drift over time.
    taxable_total_computed = models.DecimalField(
        max_digits=14, decimal_places=2, default=ZERO
    )
    cgst_total_computed = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst_total_computed = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst_total_computed = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    grand_total_computed = models.DecimalField(
        max_digits=14, decimal_places=2, default=ZERO
    )

    #: Largest absolute gap between an entered figure and the computed one,
    #: across every line and the header. Zero means the bill adds up.
    max_divergence = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        help_text="Largest difference between what was typed and what the "
        "calculator produced. Above Rs 1 is worth investigating.",
    )
    has_divergence = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True when any figure differs from the computed one by more "
        "than the tolerance.",
    )

    # --- Behaviour ----------------------------------------------------
    #: Opt-in only. A posted bill must never silently rewrite the catalogue's
    #: purchase price - that changes future costing without anyone deciding to.
    update_item_purchase_price = models.BooleanField(default=False)

    # --- Snapshots ----------------------------------------------------
    supplier_name = models.CharField(max_length=200)
    supplier_gstin = models.CharField(max_length=15, blank=True, default="")
    supplier_state_code = models.CharField(max_length=2, blank=True, default="")
    supplier_address = models.TextField(blank=True, default="")

    business_name = models.CharField(max_length=200, blank=True, default="")
    business_state_code = models.CharField(max_length=2, blank=True, default="")

    document_title = models.CharField(max_length=20, default="Purchase Bill")

    notes = models.TextField(blank=True, default="")
    terms = models.TextField(blank=True, default="")

    # --- Lifecycle timestamps -----------------------------------------
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-bill_date", "-id"]
        indexes = [
            models.Index(fields=["business", "-bill_date"]),
            models.Index(fields=["business", "status"]),
        ]
        constraints = [
            # The duplicate-entry guard. Two identical supplier bills in the same
            # FY is the mistake this exists to stop, and it has to be a database
            # constraint rather than a serializer check - a Python check loses to
            # a race, and then the same bill gets input credit twice.
            models.UniqueConstraint(
                fields=["business", "supplier", "bill_number", "financial_year"],
                name="unique_purchase_bill_per_supplier_fy",
            ),
            # `POSTED` requires a bill number AND a timestamp; `DRAFT` requires no
            # timestamp. Written as `~Q(status=...) | ...` rather than
            # `Q(status=...) | ...`, which is the inversion 2.0.7 hit twice: the
            # short form reads as though it were permissive when it is actually
            # the one demanding the data.
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=PurchaseStatus.POSTED)
                    | (
                        models.Q(bill_number__gt="")
                        & models.Q(posted_at__isnull=False)
                    )
                ),
                name="posted_purchase_has_number_and_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=PurchaseStatus.DRAFT)
                    | models.Q(posted_at__isnull=True)
                ),
                name="draft_purchase_has_no_posted_at",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=PurchaseStatus.CANCELLED)
                    | (
                        models.Q(cancelled_at__isnull=False)
                        & models.Q(cancellation_reason__isnull=False)
                        & ~models.Q(cancellation_reason="")
                    )
                ),
                name="cancelled_purchase_has_timestamp_and_reason",
            ),
            models.CheckConstraint(
                condition=models.Q(grand_total__gte=0), name="purchase_grand_total_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=0), name="purchase_subtotal_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(total_discount__gte=0),
                name="purchase_total_discount_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(taxable_total__gte=0),
                name="purchase_taxable_total_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.bill_number or '(no bill number)'} - {self.supplier_name}"

    # --- Keep the stored FY honest -------------------------------------
    def save(self, *args, **kwargs):
        # Recomputed on every save rather than set once, so a bill moved from
        # March to April cannot keep April's date with March's FY - which would
        # let a duplicate slip past the unique guard by changing the date alone.
        if self.bill_date:
            expected = financial_year(self.bill_date)
            if self.financial_year != expected:
                self.financial_year = expected
        super().save(*args, **kwargs)

    @property
    def is_draft(self) -> bool:
        return self.status == PurchaseStatus.DRAFT

    @property
    def is_posted(self) -> bool:
        return self.status == PurchaseStatus.POSTED

    @property
    def is_cancelled(self) -> bool:
        return self.status == PurchaseStatus.CANCELLED

    @property
    def tax_total(self) -> Decimal:
        """Input tax on the bill as entered. The figure 4.2 will claim."""
        return self.cgst_total + self.sgst_total + self.igst_total

    @property
    def display_number(self) -> str:
        return self.bill_number or "(draft)"

    @property
    def supplier_is_unregistered(self) -> bool:
        """
        True when the supplier cannot be claimed as input tax credit.

        Driven by the snapshot's GSTIN rather than the live Party, because a
        bill filed last year must be judged by the supplier as they were then.
        A supplier who registered later does not retroactively make last year's
        bills claimable.
        """
        return not bool(self.supplier_gstin)


class PurchaseItem(TenantModel):
    """
    One line of a purchase bill.

    Carries both the figures as entered and the calculator's version, for the
    reason in the module docstring. `itc_eligible` defaults True so 4.2 can
    total claimable credit later; the blocked-credit rules (17(5), 17(8) and
    friends) are explicitly out of scope for 2.2.
    """

    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item = models.ForeignKey(
        Item,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="purchase_items",
    )

    # --- Snapshots, filled by the service from the catalogue -----------
    item_name = models.CharField(max_length=200)
    item_type = models.CharField(max_length=8, default=Item.ItemType.PRODUCT)
    hsn_sac_code = models.CharField(max_length=8, blank=True, default="")
    unit = models.CharField(max_length=8, blank=True, default="")
    service_description = models.CharField(max_length=80, blank=True, default="")

    # --- Entered inputs ------------------------------------------------
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_discount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)

    # --- Entered amounts ------------------------------------------------
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    cgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)

    # --- The calculator's version of the same amounts -------------------
    taxable_value_computed = models.DecimalField(
        max_digits=14, decimal_places=2, default=ZERO
    )
    cgst_amount_computed = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst_amount_computed = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst_amount_computed = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_amount_computed = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)

    #: Can this line be claimed as input tax credit? 4.2 will use it.
    itc_eligible = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]
        constraints = [
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
                condition=models.Q(total_amount__gte=0),
                name="purchase_item_total_amount_not_negative",
            ),
            # Mirrors the invoice's "either it points at a catalogue item, or it
            # describes itself completely". A line with neither is unprintable
            # and unattributable.
            models.CheckConstraint(
                condition=(
                    ~models.Q(item__isnull=True)
                    | (
                        ~models.Q(item_name="")
                        & ~models.Q(item_type="")
                        & ~models.Q(hsn_sac_code="")
                        & ~models.Q(unit="")
                    )
                ),
                name="purchase_item_is_item_backed_or_self_describing",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.item_name}"

    @property
    def gross_amount(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    @property
    def net_amount(self) -> Decimal:
        return self.gross_amount - self.line_discount

    @property
    def tracks_stock(self) -> bool:
        """
        Whether posting this line moves stock.

        Same rule as a sale: only an item-backed, snapshot-PRODUCT line that is
        actually tracked. A freight charge or a service has no stock, and
        refusing the bill over it would be absurd.
        """
        if self.item_id is None:
            return False
        if self.item_type != Item.ItemType.PRODUCT:
            return False
        return bool(getattr(self.item, "track_stock", False))

    def clean(self):
        if self.line_discount > self.gross_amount:
            raise ValidationError(
                {"line_discount": "The discount cannot be more than the line total."}
            )