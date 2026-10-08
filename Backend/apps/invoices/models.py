"""
Invoice models for the GST invoicing engine.

Two design rules worth knowing before editing:

1. **Every number an invoice shows is computed, never accepted.** The request
   body carries inputs (party, date, lines, discounts); the server runs
   `apps/invoices/services/gst_calculator.py` and writes the result. That is why
   all money fields are `read_only` on the serializer.

2. **Snapshots freeze history.** Party name/GSTIN/address and business
   name/address/GSTIN are copied onto the invoice when it is issued, so
   renaming a party or the business later cannot rewrite a document that was
   already given to a customer.

`InvoiceItem.invoice_discount_share` exists so a draft can be recalculated from
its own stored inputs in step 4 (issue) without re-deriving anything.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.constants import GST_STATE_CHOICES
from apps.core.models import TenantModel
from apps.inventory.models import Item

ZERO = Decimal("0.00")


class Invoice(TenantModel):
    """
    A customer invoice.

    Lifecycle: DRAFT (editable) -> ISSUED (frozen forever) -> CANCELLED.
    The invoice number is allocated on ISSUE, not on create, so abandoned drafts
    never leave gaps in the legal numbering series.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ISSUED = "ISSUED", "Issued"
        CANCELLED = "CANCELLED", "Cancelled"

    class SupplyType(models.TextChoices):
        INTRA = "INTRA", "Intra-state (CGST + SGST/UTGST)"
        INTER = "INTER", "Inter-state (IGST)"

    # --- Who and when --------------------------------------------------
    # PROTECT: an invoice's customer must never be deleted out from under it.
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    invoice_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)

    # Allocated on issue. Max 16 characters per GST Rule 46.
    invoice_number = models.CharField(max_length=16, blank=True, default="")

    # --- Supply ---------------------------------------------------------
    place_of_supply = models.CharField(
        max_length=2, blank=True, default="", choices=GST_STATE_CHOICES
    )
    supply_type = models.CharField(
        max_length=5, choices=SupplyType.choices, default=SupplyType.INTRA
    )
    prices_include_tax = models.BooleanField(default=False)

    # --- Lifecycle ------------------------------------------------------
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default="")

    # --- Audit ----------------------------------------------------------
    # Added now, while there are no invoices, because making them optional later
    # needs a backfill migration across every historical invoice.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    # --- Snapshots (frozen at issue) ------------------------------------
    recipient_name = models.CharField(max_length=200, blank=True, default="")
    recipient_gstin = models.CharField(max_length=15, blank=True, default="")
    recipient_state_code = models.CharField(max_length=2, blank=True, default="")
    recipient_address = models.TextField(blank=True, default="")
    shipping_address = models.TextField(blank=True, default="")
    business_name = models.CharField(max_length=200, blank=True, default="")
    business_address = models.TextField(blank=True, default="")
    business_gstin = models.CharField(max_length=15, blank=True, default="")
    business_state_code = models.CharField(max_length=2, blank=True, default="")
    document_title = models.CharField(max_length=20, blank=True, default="Tax Invoice")

    # --- Money (always recomputed, never accepted from the client) ------
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    invoice_discount = models.DecimalField(
        max_digits=14, decimal_places=2, default=ZERO,
        help_text="Bill-level discount, apportioned across the lines.",
    )
    total_discount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    taxable_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    cgst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    round_off = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)

    # --- Free text ------------------------------------------------------
    notes = models.TextField(blank=True, default="")
    terms = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-invoice_date", "-id"]
        indexes = [
            # This will be the largest table we ever have; the list view
            # filters on business + date and business + status.
            models.Index(fields=["business", "invoice_date"]),
            models.Index(fields=["business", "status"]),
            models.Index(fields=["business", "party"]),
        ]
        constraints = [
            # Conditional so any number of DRAFTs (blank number) may coexist.
            models.UniqueConstraint(
                fields=["business", "invoice_number"],
                condition=models.Q(invoice_number__gt=""),
                name="unique_invoice_number_per_business",
            ),
        ]

    def __str__(self) -> str:
        if self.invoice_number:
            return f"{self.invoice_number} - {self.party_id and self.party.name}"
        return f"Draft #{self.pk} - {self.party_id and self.party.name}"

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
        """What to show in the UI. Never leaks the global primary key as a number."""
        return self.invoice_number or "Draft"


class InvoiceItem(TenantModel):
    """
    One line on an invoice.

    `item` is NULL for a free-text line (transport, packing, a one-off charge).
    Item details are SNAPSHOTTED on save and never re-read from the item master,
    so changing an item later cannot reinterpret an invoice that was already
    issued - including whether it counted as goods or a service, which decides
    the place of supply.
    """

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    # PROTECT: an item referenced by an invoice can never be deleted. NULL is
    # allowed for a free-text line.
    item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="invoice_items"
    )

    # --- Snapshots ------------------------------------------------------
    item_name = models.CharField(max_length=200)
    item_type = models.CharField(
        max_length=8, choices=Item.ItemType.choices, default=Item.ItemType.PRODUCT
    )
    hsn_sac_code = models.CharField(max_length=8, blank=True, default="")
    unit = models.CharField(max_length=8, blank=True, default="")
    service_description = models.CharField(max_length=80, blank=True, default="")

    # --- Inputs (frozen on save) ----------------------------------------
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_discount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2)

    # --- Computed (server only) -----------------------------------------
    invoice_discount_share = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    cgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    igst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)

    class Meta:
        ordering = ["id"]
        constraints = [
            # A line must be either backed by a catalogue item, or fully
            # self-describing. The serializer is the friendly first line of
            # defence; this constraint is the backstop.
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
                name="invoice_item_is_item_backed_or_self_describing",
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

        return q2(self.gross_amount - self.line_discount - self.invoice_discount_share)

    @property
    def tracks_stock(self) -> bool:
        """Mirrors the item master, but read from the snapshot, not the live item."""
        return self.item_type == Item.ItemType.PRODUCT


class InvoiceCounter(TenantModel):
    """
    Per-business, per-financial-year invoice number counter.

    `number_prefix` is snapshotted here when the FY counter is created, so a
    business that edits its prefix mid-year does not end up with a series like
    `INV/26-27/00001 ... ABC/26-27/00008`, which would look like tampering.
    """

    financial_year = models.CharField(max_length=7)  # "2026-27"
    last_number = models.PositiveIntegerField(default=0)
    number_prefix = models.CharField(max_length=4, default="INV")

    class Meta:
        ordering = ["business", "-financial_year"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "financial_year"],
                name="unique_invoice_counter_per_business_fy",
            ),
        ]
        verbose_name = "invoice counter"
        verbose_name_plural = "invoice counters"

    def __str__(self) -> str:
        return f"{self.number_prefix}/{self.financial_year} (last {self.last_number})"