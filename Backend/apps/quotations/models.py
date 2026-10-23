"""
Quotation models for the Velocity GST Billing platform.

A quotation is an estimate sent to a customer. It can be converted (one click)
into a DRAFT sales invoice, which the user reviews and then issues.

Design rules:
1. Every computed money field is read_only — the server computes GST and totals.
2. Snapshots freeze party and business details at creation time.
3. Conversion is atomic: creates a DRAFT invoice and marks the quotation
   CONVERTED in one transaction.
4. Tenancy enforced by TenantModel + TenantPrimaryKeyRelatedField.
"""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.constants import GST_STATE_CHOICES
from apps.core.models import TenantModel
from apps.inventory.models import Item
from apps.parties.models import Party

ZERO = Decimal("0.00")


class QuotationStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SENT = "SENT", "Sent"
    CONVERTED = "CONVERTED", "Converted"
    EXPIRED = "EXPIRED", "Expired"
    LOST = "LOST", "Lost"


class Quotation(TenantModel):
    """
    An estimate/quote sent to a customer party.

    Lifecycle: DRAFT -> SENT -> CONVERTED (into an invoice) / EXPIRED / LOST.
    """

    Status = QuotationStatus

    party = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="quotations",
        help_text="Customer party.",
    )
    quotation_date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(
        help_text="Quote is valid until this date. Defaults to 30 days from quotation date."
    )
    status = models.CharField(
        max_length=10,
        choices=QuotationStatus.choices,
        default=QuotationStatus.DRAFT,
        db_index=True,
    )

    # --- Supply ---------------------------------------------------------
    place_of_supply = models.CharField(
        max_length=2,
        blank=True,
        default="",
        choices=GST_STATE_CHOICES,
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

    # --- Snapshots (frozen at creation) ---------------------------------
    customer_name = models.CharField(max_length=200, blank=True, default="")
    customer_gstin = models.CharField(max_length=15, blank=True, default="")
    customer_state_code = models.CharField(max_length=2, blank=True, default="")
    customer_address = models.TextField(blank=True, default="")
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
    terms = models.TextField(blank=True, default="")

    # Link back to the invoice this quotation was converted into.
    converted_invoice = models.ForeignKey(
        "invoices.Invoice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="converted_from_quotation",
    )
    converted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-quotation_date", "-id"]
        indexes = [
            models.Index(fields=["business", "quotation_date"]),
            models.Index(fields=["business", "status"]),
            models.Index(fields=["business", "party"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(grand_total__gte=0), name="quotation_grand_total_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"Q-{self.pk} - {self.party_id and self.party.name}"

    @property
    def is_draft(self) -> bool:
        return self.status == self.Status.DRAFT

    @property
    def is_converted(self) -> bool:
        return self.status == self.Status.CONVERTED

    @property
    def tax_total(self) -> Decimal:
        return self.cgst_total + self.sgst_total + self.igst_total

    @property
    def display_number(self) -> str:
        return f"Q-{self.pk}"

    @property
    def is_expired(self) -> bool:
        if self.status in (self.Status.CONVERTED, self.Status.LOST, self.Status.EXPIRED):
            return False
        return timezone.localdate() > self.valid_until


class QuotationItem(TenantModel):
    """One line on a quotation."""

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(
        Item, null=True, blank=True, on_delete=models.PROTECT, related_name="quotation_items"
    )

    item_name = models.CharField(max_length=200)
    item_type = models.CharField(max_length=8, default="PRODUCT")
    hsn_sac_code = models.CharField(max_length=8, blank=True, default="")
    unit = models.CharField(max_length=8, blank=True, default="")
    service_description = models.CharField(max_length=80, blank=True, default="")

    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_discount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2)

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
                name="quotation_item_is_item_backed_or_self_describing",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="quotation_item_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0), name="quotation_item_unit_price_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(line_discount__gte=0),
                name="quotation_item_line_discount_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_rate__gte=0), name="quotation_item_tax_rate_not_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(taxable_value__gte=0),
                name="quotation_item_taxable_value_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=0), name="quotation_item_total_not_negative"
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
