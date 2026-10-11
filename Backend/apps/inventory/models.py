"""Item (Product & Service) models for the GST Billing platform."""

from __future__ import annotations

import os
import uuid
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


def item_image_upload_path(instance: Item, filename: str) -> str:
    """
    Store item photos as item_images/<business id>/<random>.<ext>.

    Uses a random name like the business logo does, so a long or odd user
    filename cannot break the path or clash. On Cloudinary the path segment is
    only a folder hint.
    """
    extension = os.path.splitext(filename)[1].lower() or ".jpg"
    return f"item_images/{instance.pk}/{uuid.uuid4().hex}{extension}"


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
        """
        Items that carry a stock quantity.

        One definition, shared with the `Item.tracks_stock` property: the
        explicit `track_stock` flag (decision 25). This used to be
        `item_type=PRODUCT AND current_stock IS NOT NULL`, which disagreed with
        the property's `item_type == PRODUCT`.
        """
        return self.filter(track_stock=True)

    def low_stock(self):
        """Stock-tracked products at or below their low-stock threshold."""
        return self.stock_tracked().filter(
            low_stock_threshold__isnull=False,
            current_stock__lte=F("low_stock_threshold"),
        )


class ItemType(models.TextChoices):
    """
    Module level, not nested inside `Item`.

    The stock constraints added in 2.0.6 reference `ItemType.PRODUCT` from
    `Item.Meta`, and a nested class is not bound while its own class body is
    still executing. Promoted out so the constraints can use the real enum
    rather than a string literal that a typo would silently defeat.

    `Item.ItemType` is kept as an alias below, so every existing
    `Item.ItemType.PRODUCT` reference keeps working.
    """

    PRODUCT = "PRODUCT", "Product"
    SERVICE = "SERVICE", "Service"


class Item(TenantModel):
    """
    A product or service belonging to a business.

    Money is always `Decimal(12, 2)`. Stock is `Decimal(12, 3)`. Whether an item
    has a stock quantity at all is decided by the explicit `track_stock` flag
    (decision 25); `NULL` in `current_stock` therefore means "not tracked", which
    is different from `0` ("tracked, and none left"). Phase 2.1 must gate stock
    maths on `tracks_stock` rather than comparing against zero.
    """

    #: Back-compat alias. `Item.ItemType.PRODUCT` is used across the codebase.
    ItemType = ItemType

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

    # --- Stock (quantities: Decimal(12, 3), NULL when untracked) ---
    #
    # `track_stock` is the ONE definition of "this item has a stock quantity"
    # (decision 25). Before this, the model property said "is a PRODUCT" while
    # the queryset said "is a PRODUCT *and* current_stock is not null" - so a
    # product saved with blank stock was "tracked" to one and "untracked" to the
    # other, and 2.1's `current_stock - quantity` on a NULL would 500 on issue.
    #
    # The implicit "NULL means untracked" convention also let a shop skip its
    # opening stock and silently never have stock move, with nothing to tell it.
    # Being explicit means the shop has to decide, and the decision is visible.
    track_stock = models.BooleanField(
        default=False,
        help_text=(
            "Track a stock quantity for this item. Always False for a SERVICE. "
            "For a PRODUCT, turning this off nulls the stock fields so 'no stock "
            "recorded' cannot be mistaken for 'zero stock'."
        ),
    )
    current_stock = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.000"))],
        help_text="Quantity on hand. NULL when stock is not tracked.",
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

    # --- Photo (optional; shown on the catalogue and invoice PDFs) ---
    # Left read-only in the API and set through a dedicated upload endpoint, so
    # image validation (format + size) happens in one place instead of on every
    # item write. Deleting the file is NOT tied to is_active: an invoice reprint
    # must still be able to render the photo of a soft-deleted item.
    image = models.ImageField(
        upload_to=item_image_upload_path,
        blank=True,
        help_text="Optional product photo. Shown on the catalogue and on invoice PDFs.",
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
            # A tracked item MUST carry a quantity. Without this, "tracked but
            # null" is representable and 2.1's arithmetic on it fails at issue
            # time rather than at save time.
            #
            # Note this permits a negative `current_stock`: under the ALLOW
            # negative-stock policy (decision 33) a sale can legitimately drive
            # the balance below zero. The constraint is about *tracking*, not
            # about the balance being non-negative.
            models.CheckConstraint(
                condition=(
                    models.Q(track_stock=False)
                    | (
                        models.Q(item_type=ItemType.PRODUCT)
                        & models.Q(current_stock__isnull=False)
                    )
                ),
                name="tracked_item_must_be_product_with_stock",
            ),
            # A SERVICE can never be stock-tracked, whatever the serializer sent.
            models.CheckConstraint(
                condition=models.Q(item_type=ItemType.PRODUCT) | models.Q(track_stock=False),
                name="service_is_never_stock_tracked",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_item_type_display()})"

    # ------------------------------------------------------------------
    # Stock semantics
    # ------------------------------------------------------------------
    @property
    def tracks_stock(self) -> bool:
        """
        True when this item carries a stock quantity.

        Now a straight read of the flag, so it can never disagree with
        `Item.objects.stock_tracked()` - which is what decision 25 fixed. It was
        previously `item_type == PRODUCT`, which said a product with no stock
        value was tracked while the queryset excluded it.
        """
        return bool(self.track_stock)

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


# The stock ledger lives in its own module (2.1.1) because it is a substantial,
# self-contained concern - append-only semantics, its own idempotency rule, and a
# module docstring explaining why it holds no FK back to invoices. Importing it
# here registers `StockMovement` with this app, which is why it must be at the
# END of the file: it imports `TenantModel` and this module defines `Item`.
from .models_stock import (  # noqa: E402,F401
    AdjustmentReason,
    MovementType,
    SourceType,
    StockMovement,
)