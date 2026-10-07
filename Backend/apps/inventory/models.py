"""Item (Product & Service) models for the GST Billing platform."""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F

from apps.core.constants import (
    DEFAULT_GST_RATE,
    DEFAULT_UNIT,
    GST_RATE_CHOICES,
    MEASURING_UNITS,
    SERVICE_DESCRIPTION_MAX_LENGTH,
)
from apps.core.models import TenantModel, TenantQuerySet


class ItemQuerySet(TenantQuerySet):
    """
    Stock-aware helpers.

    Every phase that touches stock (2.1 auto-deduction, 3.3 low-stock widget)
    should go through `stock_tracked()` / `low_stock()` instead of re-testing
    `item_type` inline, so a service can never be mistaken for an out-of-stock
    product.
    """

    def active(self):
        return self.filter(is_active=True)

    def stock_tracked(self):
        """Products that actually carry a stock quantity."""
        return self.filter(
            item_type=Item.ItemType.PRODUCT,
            current_stock__isnull=False,
        )

    def low_stock(self):
        """Stock-tracked products at or below their low-stock threshold."""
        return self.stock_tracked().filter(
            low_stock_threshold__isnull=False,
            current_stock__lte=F("low_stock_threshold"),
        )


class Item(TenantModel):
    """
    A product or service belonging to a business.

    Money is always `Decimal(12, 2)`. Stock is `Decimal(12, 3)` and is **NULL**
    for services: `NULL` means "not stock-tracked", which is different from
    `0` ("tracked, and none left"). Phase 2.1 must therefore gate stock maths
    on `tracks_stock` rather than comparing against zero.
    """

    class ItemType(models.TextChoices):
        PRODUCT = "PRODUCT", "Product"
        SERVICE = "SERVICE", "Service"

    NON_NEGATIVE = [MinValueValidator(Decimal("0.00"))]

    # --- Identity ---
    name = models.CharField(max_length=200)
    item_code = models.CharField(
        "Item code / SKU",
        max_length=50,
        blank=True,
        help_text="Your own code for this item. Leave blank if not used.",
    )
    barcode = models.CharField(
        max_length=50,
        blank=True,
        help_text="EAN / UPC barcode. Leave blank if not used.",
    )

    # --- Classification ---
    item_type = models.CharField(
        max_length=8,
        choices=ItemType.choices,
        default=ItemType.PRODUCT,
        db_index=True,
        help_text="Products carry stock. Services do not.",
    )

    # --- Tax codes ---
    hsn_sac_code = models.CharField(
        "HSN / SAC code",
        max_length=8,
        blank=True,
        help_text="4, 6 or 8 digits. Goods use HSN, services use SAC.",
    )
    service_description = models.CharField(
        max_length=SERVICE_DESCRIPTION_MAX_LENGTH,
        blank=True,
        help_text="Required for services: the description printed on the tax invoice.",
    )

    # --- Unit ---
    unit = models.CharField(
        max_length=8,
        choices=MEASURING_UNITS,
        default=DEFAULT_UNIT,
        help_text="Unit of sale, e.g. PCS, KG, LTR.",
    )

    # --- Pricing (money: Decimal(12, 2)) ---
    sales_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=NON_NEGATIVE,
        help_text="Default selling price before tax (or tax-inclusive, see below).",
    )
    purchase_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=NON_NEGATIVE,
        help_text="Default buying price. Optional.",
    )
    price_includes_tax = models.BooleanField(
        default=False,
        help_text="On when sales_price already contains GST, so tax is extracted from it.",
    )
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        choices=GST_RATE_CHOICES,
        default=DEFAULT_GST_RATE,
        help_text="Applicable GST rate as a percentage.",
    )

    # --- Stock (quantities: Decimal(12, 3), NULL for services) ---
    current_stock = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.000"))],
        help_text="Quantity on hand. NULL for services (not stock-tracked).",
    )
    low_stock_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.000"))],
        help_text="Alert when stock falls to this level. NULL to disable the alert.",
    )

    # --- Status ---
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Soft delete flag. Inactive items are hidden from pickers but preserved for historic invoices.",
    )

    objects = ItemQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["business", "item_type", "is_active"]),
            models.Index(fields=["business", "name"]),
            models.Index(fields=["business", "item_code"]),
        ]
        constraints = [
            # Conditional on both a non-blank value AND still being active, so a
            # soft-deleted item's code/barcode can be reused while the row is kept
            # for historic invoices.
            models.UniqueConstraint(
                fields=["business", "item_code"],
                condition=models.Q(item_code__gt="", is_active=True),
                name="unique_item_code_per_business",
            ),
            models.UniqueConstraint(
                fields=["business", "barcode"],
                condition=models.Q(barcode__gt="", is_active=True),
                name="unique_barcode_per_business",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_item_type_display()})"

    # ------------------------------------------------------------------
    # Stock semantics
    # ------------------------------------------------------------------
    @property
    def tracks_stock(self) -> bool:
        """True when this item carries a stock quantity (products only)."""
        return self.item_type == self.ItemType.PRODUCT

    @property
    def is_low_stock(self) -> bool:
        """True when a tracked product is at or below its alert threshold."""
        if not self.tracks_stock or self.current_stock is None:
            return False
        if self.low_stock_threshold is None:
            return False
        return self.current_stock <= self.low_stock_threshold

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------
    def get_display_unit(self) -> str:
        """Human-readable unit, e.g. 'Kilogram' for 'KG'."""
        return dict(MEASURING_UNITS).get(self.unit, self.unit)

    @property
    def code_type_label(self) -> str:
        """'HSN Code' for goods, 'SAC Code' for services."""
        return "SAC Code" if self.item_type == self.ItemType.SERVICE else "HSN Code"

    @property
    def tax_rate_label(self) -> str:
        """'18%' style label for the UI."""
        from apps.core.constants import gst_rate_label

        return gst_rate_label(self.tax_rate)