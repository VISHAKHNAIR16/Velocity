"""
Serializers for the payments app.

The single most important rule here: **every computed field is `read_only`**.
The request body carries inputs (party, amount, date, mode, invoice link);
the service layer writes the payment row and recalculates the linked invoice.
The client must never be able to post `balance_due` or `payment_status` on
an invoice through a payment payload.
"""

from rest_framework import serializers

from apps.core.fields import TenantPrimaryKeyRelatedField
from apps.invoices.models import Invoice
from apps.parties.models import Party

from .models import PaymentRecord

ZERO = "0.00"


class PaymentRecordSerializer(serializers.ModelSerializer):
    """Full payment record. All computed/display fields are read-only."""

    party = TenantPrimaryKeyRelatedField(queryset=Party.objects.all())
    invoice = TenantPrimaryKeyRelatedField(
        queryset=Invoice.objects.all(), required=False, allow_null=True
    )

    party_name = serializers.CharField(source="party.name", read_only=True)
    transaction_type_label = serializers.CharField(
        source="get_transaction_type_display", read_only=True
    )
    payment_mode_label = serializers.CharField(source="get_payment_mode_display", read_only=True)

    class Meta:
        model = PaymentRecord
        fields = [
            "id",
            "party",
            "party_name",
            "transaction_type",
            "transaction_type_label",
            "invoice",
            "payment_date",
            "payment_mode",
            "payment_mode_label",
            "amount",
            "reference_number",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "party_name",
            "transaction_type_label",
            "payment_mode_label",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "reference_number": {"required": False, "allow_blank": True},
            "notes": {"required": False, "allow_blank": True},
        }


class PaymentCreateSerializer(serializers.Serializer):
    """Input-only serializer for creating a payment. No computed outputs."""

    party = TenantPrimaryKeyRelatedField(queryset=Party.objects.all())
    invoice = TenantPrimaryKeyRelatedField(
        queryset=Invoice.objects.all(), required=False, allow_null=True
    )
    transaction_type = serializers.ChoiceField(choices=PaymentRecord.TransactionType.choices)
    payment_date = serializers.DateField()
    payment_mode = serializers.ChoiceField(choices=PaymentRecord.PaymentMode.choices)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    reference_number = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_amount(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate(self, attrs):
        invoice = attrs.get("invoice")
        transaction_type = attrs.get("transaction_type")
        if invoice is not None and transaction_type == PaymentRecord.TransactionType.PAYMENT_IN:
            if invoice.status != Invoice.Status.ISSUED:
                raise serializers.ValidationError(
                    {
                        "invoice": (
                            "Payments can only be linked to ISSUED invoices. "
                            f"Invoice {invoice.display_number} is {invoice.get_status_display()}."
                        )
                    }
                )
            if invoice.party_id != attrs["party"].pk:
                raise serializers.ValidationError({"invoice": "The invoice is not for this party."})
        return attrs
