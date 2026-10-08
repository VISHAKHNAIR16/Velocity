"""
Serializers for invoices.

The single most important rule here: **every computed money field is
`read_only`**. The request body carries inputs only. If the client could post
`grand_total`, a buggy (or tampered) browser could decide what a legal document
says - and the preview would stop being a prediction of the saved result.

Line totals are produced by `apps/invoices/services/draft.py`, which calls the
same `calculate_invoice()` used by `/invoices/preview/`.
"""

from rest_framework import serializers

from apps.core.constants import GST_RATE_CHOICES, MEASURING_UNITS, gst_rate_label
from apps.core.fields import TenantPrimaryKeyRelatedField
from apps.core.money import q2
from apps.inventory.models import Item
from apps.parties.models import Party

from .models import Invoice, InvoiceItem

ZERO = "0.00"


class InvoiceItemSerializer(serializers.ModelSerializer):
    """One line. Accepts inputs; every computed figure is read-only."""

    #: Scoped to the caller's business: another tenant's id is a 400, not a leak.
    item = TenantPrimaryKeyRelatedField(
        queryset=Item.objects.all(), required=False, allow_null=True
    )

    item_type_label = serializers.CharField(source="get_item_type_display", read_only=True)
    gross_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    net_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    tracks_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = InvoiceItem
        fields = [
            "id",
            "item",
            # Inputs
            "item_name",
            "item_type",
            "item_type_label",
            "hsn_sac_code",
            "unit",
            "service_description",
            "quantity",
            "unit_price",
            "line_discount",
            "tax_rate",
            # Computed - never accepted from the client
            "gross_amount",
            "net_amount",
            "invoice_discount_share",
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
            "invoice_discount_share",
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
            # The text fields are writable so a FREE-TEXT line can be described.
            # For an item-backed line build_line_from_payload() ignores whatever
            # was posted and copies the catalogue instead, so these cannot be
            # used to forge an item's description.
            "item_name": {"required": False, "allow_blank": True, "max_length": 255},
            "item_type": {"required": False},
            "hsn_sac_code": {"required": False, "allow_blank": True},
            "unit": {"required": False, "allow_blank": True},
            "service_description": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        """
        Reject a line that has neither a catalogue item nor the fields a
        free-text line needs, with a 400 instead of a 500 from the service layer.
        """
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
                    "A line without a catalogue item needs "
                    f"{', '.join(missing)}."
                )
            if (
                attrs.get("item_type") == Item.ItemType.SERVICE
                and not (attrs.get("service_description") or "").strip()
            ):
                raise serializers.ValidationError(
                    "Services need a description - it is printed on the tax invoice."
                )
        return attrs


class InvoiceSerializer(serializers.ModelSerializer):
    """Full invoice, with nested lines."""

    party = TenantPrimaryKeyRelatedField(queryset=Party.objects.all())
    items = InvoiceItemSerializer(many=True, required=False)

    document_title = serializers.CharField(read_only=True)
    tax_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    display_number = serializers.CharField(read_only=True)
    state_tax_label = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id",
            "party",
            "invoice_number",
            "display_number",
            "invoice_date",
            "due_date",
            "place_of_supply",
            "supply_type",
            "prices_include_tax",
            "status",
            "issued_at",
            "cancelled_at",
            "cancellation_reason",
            # Snapshots (written at issue)
            "recipient_name",
            "recipient_gstin",
            "recipient_state_code",
            "recipient_address",
            "shipping_address",
            "business_name",
            "business_address",
            "business_gstin",
            "business_state_code",
            "document_title",
            # Money - read-only, always recomputed by the server
            "subtotal",
            "invoice_discount",
            "total_discount",
            "taxable_total",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "round_off",
            "grand_total",
            "tax_total",
            "state_tax_label",
            "notes",
            "terms",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            # Allocated on issue in step D, never accepted from a client.
            "invoice_number",
            "display_number",
            "status",
            "issued_at",
            "cancelled_at",
            "cancellation_reason",
            "recipient_name",
            "recipient_gstin",
            "recipient_state_code",
            "recipient_address",
            "shipping_address",
            "business_name",
            "business_address",
            "business_gstin",
            "business_state_code",
            "document_title",
            "supply_type",
            "subtotal",
            "total_discount",
            "taxable_total",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "round_off",
            "grand_total",
            "tax_total",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "invoice_discount": {"required": False, "default": ZERO},
            "prices_include_tax": {"required": False, "default": False},
        }

    def get_state_tax_label(self, obj: Invoice) -> str:
        from apps.invoices.services.gst_calculator import state_tax_label

        return state_tax_label(obj.place_of_supply, obj.supply_type)

    def validate(self, attrs):
        """Draft-level checks that do not need the calculator."""
        invoice_date = attrs.get("invoice_date") or getattr(self.instance, "invoice_date", None)
        due_date = attrs.get("due_date") or getattr(self.instance, "due_date", None)
        if invoice_date and due_date and due_date < invoice_date:
            raise serializers.ValidationError(
                {"due_date": "The due date cannot be before the invoice date."}
            )

        if self.instance and self.instance.status != Invoice.Status.DRAFT:
            raise serializers.ValidationError(
                "This invoice has been issued and can no longer be edited."
            )
        return attrs


class InvoiceListSerializer(serializers.ModelSerializer):
    """Lightweight row payload - the table never shows the snapshots."""

    party_name = serializers.CharField(source="party.name", read_only=True)
    display_number = serializers.CharField(read_only=True)
    tax_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "invoice_number",
            "display_number",
            "status",
            "invoice_date",
            "due_date",
            "party",
            "party_name",
            "place_of_supply",
            "supply_type",
            "subtotal",
            "total_discount",
            "taxable_total",
            "cgst_total",
            "sgst_total",
            "igst_total",
            "round_off",
            "grand_total",
            "tax_total",
            "created_at",
        ]


# ---------------------------------------------------------------------------
# Preview - same inputs, nothing saved
# ---------------------------------------------------------------------------
class InvoicePreviewSerializer(serializers.Serializer):
    """
    Input-only payload for `POST /api/v1/invoices/preview/`.

    Deliberately has no money output fields: it returns whatever
    `compute_totals()` produced. Kept as a `Serializer` (not a ModelSerializer)
    so it is structurally impossible for it to accept a total from the client.

    `items` reuses `InvoiceItemSerializer` so preview validates, resolves
    catalogue items and rejects bad free-text lines by exactly the same rules as
    a real save. If these two ever drift apart the preview stops predicting the
    saved result, which is the whole point of the endpoint.
    """

    party = TenantPrimaryKeyRelatedField(queryset=Party.objects.all())
    place_of_supply = serializers.CharField(required=False, allow_blank=True, max_length=2)
    prices_include_tax = serializers.BooleanField(required=False, default=False)
    invoice_discount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, default=ZERO
    )
    items = InvoiceItemSerializer(many=True, allow_empty=False)


def units_choices():
    return MEASURING_UNITS


def tax_rate_choices():
    return GST_RATE_CHOICES


def format_tax_rate(rate) -> str:
    return gst_rate_label(rate)


def coerce_decimal(value):
    return q2(value or "0")