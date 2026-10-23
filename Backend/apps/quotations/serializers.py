"""
Serializers for quotations.
"""

from rest_framework import serializers

from apps.core.fields import TenantPrimaryKeyRelatedField
from apps.inventory.models import Item
from apps.parties.models import Party

from .models import Quotation, QuotationItem

ZERO = "0.00"


class QuotationItemSerializer(serializers.ModelSerializer):
    """One quotation line."""

    item = TenantPrimaryKeyRelatedField(
        queryset=Item.objects.all(), required=False, allow_null=True
    )
    gross_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    net_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    tracks_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = QuotationItem
        fields = [
            "id",
            "item",
            "item_name",
            "item_type",
            "hsn_sac_code",
            "unit",
            "service_description",
            "quantity",
            "unit_price",
            "line_discount",
            "tax_rate",
            "gross_amount",
            "net_amount",
            "taxable_value",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
            "total_amount",
            "tracks_stock",
        ]
        read_only_fields = [
            "id",
            "gross_amount",
            "net_amount",
            "taxable_value",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
            "total_amount",
            "tracks_stock",
        ]
        extra_kwargs = {
            "quantity": {"required": True},
            "unit_price": {"required": True},
            "tax_rate": {"required": False},
            "line_discount": {"required": False, "default": ZERO},
            "item_name": {"required": False, "allow_blank": True},
            "item_type": {"required": False},
            "hsn_sac_code": {"required": False, "allow_blank": True},
            "unit": {"required": False, "allow_blank": True},
            "service_description": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        if self.instance is not None:
            return attrs
        if not attrs.get("item"):
            missing = [
                name
                for name in ("item_name", "hsn_sac_code", "unit")
                if not (attrs.get(name) or "").strip()
            ]
            if missing:
                raise serializers.ValidationError(
                    f"A line without a catalogue item needs {', '.join(missing)}."
                )
        return attrs


class QuotationSerializer(serializers.ModelSerializer):
    """Full quotation, with nested lines."""

    party = TenantPrimaryKeyRelatedField(queryset=Party.objects.all())
    items = QuotationItemSerializer(many=True, required=False)

    tax_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    display_number = serializers.CharField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = Quotation
        fields = [
            "id",
            "party",
            "display_number",
            "quotation_date",
            "valid_until",
            "status",
            "place_of_supply",
            "supply_type",
            "prices_include_tax",
            "customer_name",
            "customer_gstin",
            "customer_state_code",
            "customer_address",
            "business_name",
            "business_address",
            "business_gstin",
            "business_state_code",
            "subtotal",
            "total_discount",
            "taxable_total",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "round_off",
            "grand_total",
            "tax_total",
            "notes",
            "terms",
            "is_expired",
            "converted_invoice",
            "converted_at",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "display_number",
            "status",
            "customer_name",
            "customer_gstin",
            "customer_state_code",
            "customer_address",
            "business_name",
            "business_address",
            "business_gstin",
            "business_state_code",
            "subtotal",
            "total_discount",
            "taxable_total",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "round_off",
            "grand_total",
            "tax_total",
            "is_expired",
            "converted_invoice",
            "converted_at",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "valid_until": {"required": False},
            "place_of_supply": {"required": False, "allow_blank": True},
            "supply_type": {"required": False, "default": "INTRA"},
            "prices_include_tax": {"required": False, "default": False},
            "notes": {"required": False, "allow_blank": True},
            "terms": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        if self.instance and self.instance.status != Quotation.Status.DRAFT:
            raise serializers.ValidationError(
                "This quotation has already been sent or converted and can no longer be edited."
            )
        return attrs


class QuotationListSerializer(serializers.ModelSerializer):
    """Lightweight row payload for the list view."""

    party_name = serializers.CharField(source="party.name", read_only=True)
    tax_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = Quotation
        fields = [
            "id",
            "display_number",
            "status",
            "quotation_date",
            "valid_until",
            "party",
            "party_name",
            "place_of_supply",
            "supply_type",
            "subtotal",
            "taxable_total",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "round_off",
            "grand_total",
            "tax_total",
            "is_expired",
            "created_at",
        ]
