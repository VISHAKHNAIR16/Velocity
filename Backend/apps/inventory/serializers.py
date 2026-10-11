"""Serializers for the Inventory app."""

from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from apps.accounts.services import get_business
from apps.core.constants import (
    VALID_UNITS,
    is_hsn,
    is_sac,
    valid_gst_rate,
)

from .models import Item
from .services.stock import set_tracking


class ItemImageUploadSerializer(serializers.Serializer):
    """
    Validates an uploaded item photo: a real image, an allowed type, and a size
    limit. Mirrors LogoUploadSerializer so both behave identically.
    """

    MAX_BYTES = 2 * 1024 * 1024  # 2 MB
    ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}

    image = serializers.ImageField()  # opened with Pillow, so non-images are rejected

    def validate_image(self, file):
        if file.size > self.MAX_BYTES:
            raise serializers.ValidationError("Image must be 2 MB or smaller.")
        if file.image.format not in self.ALLOWED_FORMATS:
            raise serializers.ValidationError("Use a JPG, PNG or WebP image.")
        return file


class ItemListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""

    item_type_label = serializers.CharField(source="get_item_type_display", read_only=True)
    code_type_label = serializers.CharField(read_only=True)
    display_unit = serializers.CharField(source="get_display_unit", read_only=True)
    tax_rate_label = serializers.CharField(read_only=True)
    tracks_stock = serializers.BooleanField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Item
        fields = [
            "id",
            "name",
            "item_type",
            "item_type_label",
            "item_code",
            "barcode",
            "hsn_sac_code",
            "code_type_label",
            "unit",
            "display_unit",
            "sales_price",
            "tax_rate",
            "tax_rate_label",
            "tracks_stock",
            "track_stock",
            "current_stock",
            "low_stock_threshold",
            "is_low_stock",
            "is_active",
            "image",
            "created_at",
        ]


class ItemSerializer(serializers.ModelSerializer):
    """Full serializer for Item create/update/retrieve."""

    #: Declared explicitly (instead of inheriting a ChoiceField) so an unsupported
    #: rate gets a helpful message, and so the value is always parsed as Decimal.
    tax_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, min_value=Decimal("0.00")
    )

    item_type_label = serializers.CharField(source="get_item_type_display", read_only=True)
    code_type_label = serializers.CharField(read_only=True)
    display_unit = serializers.CharField(source="get_display_unit", read_only=True)
    tax_rate_label = serializers.CharField(read_only=True)
    tracks_stock = serializers.BooleanField(read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)
    #: True once the ledger has history for this item, so the unit is frozen
    #: (decision 39). Exposed so the UI can disable the control and explain why,
    #: rather than letting the user fill it in and then rejecting it on save -
    #: which reads as "the app is broken", not "this is a deliberate rule".
    unit_locked = serializers.SerializerMethodField()

    #: Fields stored upper case so "abc-123" and "ABC-123" are one item.
    UPPERCASE_FIELDS = ("item_code", "barcode", "hsn_sac_code")

    def get_unit_locked(self, obj) -> bool:
        return obj.stock_movements.exists()

    class Meta:
        model = Item
        fields = [
            "id",
            "name",
            "item_type",
            "item_type_label",
            "item_code",
            "barcode",
            "hsn_sac_code",
            "code_type_label",
            "service_description",
            "unit",
            "display_unit",
            "sales_price",
            "purchase_price",
            "price_includes_tax",
            "tax_rate",
            "tax_rate_label",
            "tracks_stock",
            "unit_locked",
            "track_stock",
            "current_stock",
            "low_stock_threshold",
            "is_low_stock",
            "is_active",
            "image",
            "created_at",
            "updated_at",
        ]
        # `image` is read-only: it is set through the upload endpoint so image
        # format/size validation lives in one place (see ItemImageUploadSerializer).
        read_only_fields = ["id", "image", "created_at", "updated_at"]
        extra_kwargs = {
            # Services never carry stock, so the field may legitimately be absent.
            "current_stock": {"required": False},
            "low_stock_threshold": {"required": False},
            "purchase_price": {"required": False},
            "service_description": {"required": False},
        }

    # ------------------------------------------------------------------
    # Input cleaning
    # ------------------------------------------------------------------
    def to_internal_value(self, data):
        """Clean raw input BEFORE field validators run (upper case the codes)."""
        data = data.copy() if hasattr(data, "copy") else dict(data)
        for field in self.UPPERCASE_FIELDS:
            if isinstance(data.get(field), str):
                data[field] = data[field].strip().upper()
        return super().to_internal_value(data)

    # ------------------------------------------------------------------
    # Field validation
    # ------------------------------------------------------------------
    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Enter an item name.")
        return value

    def validate_unit(self, value: str) -> str:
        if value not in VALID_UNITS:
            raise serializers.ValidationError(f"'{value}' is not a supported unit.")
        return value

    def validate_tax_rate(self, value):
        # The field already parsed this to Decimal; re-check it is a real GST slab.
        try:
            rate = Decimal(str(value))
        except (InvalidOperation, TypeError):
            raise serializers.ValidationError("Enter a valid GST rate.") from None
        if not valid_gst_rate(rate):
            raise serializers.ValidationError(
                f"{value}% is not a supported GST rate. "
                "Use 0, 0.25, 1.5, 3, 5, 12, 18 or 28."
            )
        return rate

    # ------------------------------------------------------------------
    # Cross-field validation
    # ------------------------------------------------------------------
    def validate(self, attrs: dict) -> dict:
        instance = self.instance

        def current(name, default=""):
            if name in attrs:
                return attrs[name]
            return getattr(instance, name, default) if instance else default

        item_type = current("item_type", Item.ItemType.PRODUCT)
        hsn_sac = current("hsn_sac_code", "")
        service_description = current("service_description", "")
        errors: dict = {}

        # --- HSN / SAC -----------------------------------------------------
        # Length rules are asymmetric: 5 and 7 digits are never valid; 8 digits
        # are fine for goods HSN but too long for a services SAC. A 6-digit code
        # is legitimately valid for BOTH, so it cannot identify the type.
        if hsn_sac:
            if item_type == Item.ItemType.SERVICE:
                if not is_sac(hsn_sac):
                    errors["hsn_sac_code"] = (
                        "A SAC code must be 4 or 6 digits (services never use 8)."
                    )
            elif not is_hsn(hsn_sac):
                errors["hsn_sac_code"] = (
                    "An HSN code must be 4, 6 or 8 digits (5 and 7 are not used)."
                )

        # --- Service description -------------------------------------------
        if item_type == Item.ItemType.SERVICE:
            if not (service_description or "").strip():
                errors["service_description"] = (
                    "Services need a description - it is printed on the tax invoice."
                )
        elif (service_description or "").strip():
            # Keep the record clean: a product has no service description.
            attrs["service_description"] = ""

        # --- Stock semantics ------------------------------------------------
        # Services are NEVER stock-tracked. NULL is the honest value; storing 0
        # would make Phase 2.1 refuse to invoice the service and would show a
        # permanent "out of stock" alert.
        #
        # Decision 25 made tracking explicit, so the flag is set here rather than
        # left to the model's `False` default - otherwise a product created
        # through the API would silently be untracked, which is precisely the
        # "shop never notices its stock is not moving" trap the flag exists to
# prevent.
        if item_type == Item.ItemType.SERVICE:
            if attrs.get("current_stock") not in (None, "") or attrs.get("low_stock_threshold") not in (None, ""):
                errors.setdefault("current_stock", "Services do not track stock; leave it empty.")
            attrs["current_stock"] = None
            attrs["low_stock_threshold"] = None
            attrs["track_stock"] = False
        elif self.instance is not None:
            # UPDATE. Every default below is CREATE-only. Applying them here
            # meant a PATCH of just the NAME reset `current_stock` to 0 - a
            # partial update silently wiping the shop's stock.
            #
            # On update the values are whatever is already stored unless the
            # caller actually sent a new one.
            if "track_stock" in attrs:
                attrs["track_stock"] = bool(attrs["track_stock"])
                if not attrs["track_stock"]:
                    attrs["current_stock"] = None
                    attrs["low_stock_threshold"] = None
            elif "current_stock" not in attrs and "low_stock_threshold" not in attrs:
                # Nothing stock-related was sent: leave every stock field alone.
                pass
        else:
            # CREATE. A PRODUCT defaults to tracked. A tracked item MUST carry a
            # quantity, so default the quantity to 0 rather than leaving it
            # NULL - "tracked, and none left" is a real state the shop can then
            # correct with an opening adjustment.
            wants_tracking = attrs.get("track_stock")
            if wants_tracking is None:
                wants_tracking = True
            attrs["track_stock"] = bool(wants_tracking)

            if attrs["track_stock"]:
                if attrs.get("current_stock") in (None, ""):
                    attrs["current_stock"] = Decimal("0.000")
            else:
                # Not tracked: NULL the quantity so "no stock recorded" cannot
                # be mistaken for "zero stock".
                attrs["current_stock"] = None
                attrs["low_stock_threshold"] = None

        # --- The stock rules below can add their own errors ----------------
        # (stock read-only, unit locked), so the original `if errors: raise`
        # was moved down to after them - raising here would have discarded the
        # messages those checks produced.
        # --- Stock is never edited directly (decision 34) ------------------
        # Changing `current_stock` through a PUT would bypass the ledger, so
        # `reconcile_stock` would report drift for ever with no way to explain
        # it. Corrections go through POST /items/{id}/adjust-stock/.
        #
        # The SAME value is tolerated, because HTML forms re-send every field on
        # save and refusing that would make the edit form unusable.
        if self.instance is not None and "current_stock" in attrs:
            wanted = attrs.get("current_stock")
            # NOT named `current`: this method already has a `current()` helper
            # used by the uniqueness checks further down, and shadowing it broke
            # them with "Decimal object is not callable".
            stored = self.instance.current_stock
            if wanted not in (None, "") and stored is not None:
                if Decimal(str(wanted)) != Decimal(str(stored)):
                    errors["current_stock"] = (
                        "Stock is changed through 'Adjust stock', not by editing "
                        "the item. That keeps the stock history explainable."
                    )

        # --- The unit is locked once the ledger has history (decision 39) ---
        # PCS -> KG would silently reinterpret every historical quantity, so the
        # item must become a different item instead.
        if self.instance is not None and "unit" in attrs:
            if attrs["unit"] != self.instance.unit and self.instance.stock_movements.exists():
                errors["unit"] = (
                    "This item has stock history, so its unit cannot be changed - "
                    "that would reinterpret past quantities. Create a new item "
                    "with the other unit."
                )

        # --- Turning tracking on or off is itself a movement (decision 40) ---
        if self.instance is not None and "track_stock" in attrs:
            wanted = attrs["track_stock"]
            if bool(wanted) != bool(self.instance.track_stock):
                opening = attrs.get("opening_quantity")
                set_tracking(
                    get_business(self.context.get("request").user),
                    self.instance,
                    enabled=bool(wanted),
                    opening_quantity=opening,
                    user=self.context.get("request").user,
                )
                # The ledger owns the balance from here on; drop the caller's
                # value so it cannot overwrite what we just wrote.
                attrs.pop("current_stock", None)
                attrs.pop("low_stock_threshold", None)
                # ...and re-read, because `save()` below writes the WHOLE
                # in-memory instance. Without this the stale `current_stock`
                # still on the instance is written back and the
                # `tracked_item_must_be_product_with_stock` constraint fires.
                self.instance.refresh_from_db()

        if errors:
            raise serializers.ValidationError(errors)

        # --- Uniqueness, scoped to this business ---------------------------
        request = self.context.get("request")
        business = get_business(request.user) if request else None

        if business:
            for field, label in (("item_code", "item code"), ("barcode", "barcode")):
                value = current(field, "")
                if not value:
                    continue
                qs = Item.objects.filter(business=business, **{field: value})
                if instance:
                    qs = qs.exclude(pk=instance.pk)
                if qs.exists():
                    errors[field] = f"Another item already uses this {label}."

        if errors:
            raise serializers.ValidationError(errors)

        return attrs
