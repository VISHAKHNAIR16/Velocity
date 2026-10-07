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
            "current_stock",
            "low_stock_threshold",
            "is_low_stock",
            "is_active",
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

    #: Fields stored upper case so "abc-123" and "ABC-123" are one item.
    UPPERCASE_FIELDS = ("item_code", "barcode", "hsn_sac_code")

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
            "current_stock",
            "low_stock_threshold",
            "is_low_stock",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
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
        # Services are NOT stock-tracked. NULL is the honest value; storing 0
        # would make Phase 2.1 refuse to invoice the service and would show a
        # permanent "out of stock" alert.
        if item_type == Item.ItemType.SERVICE:
            if attrs.get("current_stock") not in (None, "") or attrs.get("low_stock_threshold") not in (None, ""):
                errors.setdefault("current_stock", "Services do not track stock; leave it empty.")
            attrs["current_stock"] = None
            attrs["low_stock_threshold"] = None

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