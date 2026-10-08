"""Serializers for the Parties app."""

import re

from rest_framework import serializers

from apps.accounts.services import get_business
from apps.core.constants import GST_STATE_CHOICES

from .models import Party


class PartySerializer(serializers.ModelSerializer):
    """Full serializer for Party create/update/retrieve."""

    # Computed fields
    display_state = serializers.CharField(source="get_display_state", read_only=True)
    is_registered = serializers.BooleanField(read_only=True)
    billing_address_lines = serializers.SerializerMethodField()
    shipping_address_lines = serializers.SerializerMethodField()

    # Fields that should be stored uppercase
    UPPERCASE_FIELDS = ("gstin", "pan")

    class Meta:
        model = Party
        fields = [
            "id",
            "name",
            "party_type",
            "mobile",
            "email",
            "gstin",
            "pan",
            "state_code",
            "billing_address",
            "billing_city",
            "billing_pincode",
            "shipping_address",
            "shipping_city",
            "shipping_pincode",
            "shipping_state_code",
            "opening_balance",
            "balance_type",
            "is_active",
            "display_state",
            "is_registered",
            "billing_address_lines",
            "shipping_address_lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            # Not required at the API boundary so a GSTIN-only payload can have its
            # state derived below. validate() still guarantees a state is present.
            "state_code": {"required": False},
        }

    def get_billing_address_lines(self, obj: Party) -> list[str]:
        return obj.get_billing_address_lines()

    def get_shipping_address_lines(self, obj: Party) -> list[str]:
        return obj.get_shipping_address_lines()

    def to_internal_value(self, data):
        """Clean raw input BEFORE field validators run (uppercase GSTIN/PAN)."""
        data = data.copy() if hasattr(data, "copy") else dict(data)
        for field in self.UPPERCASE_FIELDS:
            if isinstance(data.get(field), str):
                data[field] = data[field].strip().upper()
        return super().to_internal_value(data)

    def validate_gstin(self, value: str) -> str:
        """Validate GSTIN format and cross-check it against the selected state."""
        if not value:
            return value

        # Format validation (regex validator also runs, but we need custom logic)
        if not re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", value):
            raise serializers.ValidationError(
                "Enter a valid 15-character GSTIN (e.g., 29ABCCS2942R1ZR)."
            )

        # Cross-validate the GSTIN's leading state code with the chosen state.
        # (The PAN comparison lives in validate() so the error is reported on the
        # PAN field rather than on the GSTIN field.)
        state_code = self.initial_data.get("state_code") or (
            self.instance.state_code if self.instance else None
        )
        if state_code and value[:2] != state_code:
            raise serializers.ValidationError(
                f"GSTIN state code ({value[:2]}) must match selected state ({state_code})."
            )

        return value

    def validate_pan(self, value: str) -> str:
        """Validate PAN format."""
        if not value:
            return value
        if not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", value):
            raise serializers.ValidationError(
                "Enter a valid 10-character PAN (e.g., ABCCS2942R)."
            )
        return value

    def validate_state_code(self, value: str) -> str:
        """Validate state code exists in GST_STATE_CHOICES."""
        valid_codes = [code for code, _ in GST_STATE_CHOICES]
        if value and value not in valid_codes:
            raise serializers.ValidationError("Invalid GST state code.")
        return value

    def validate_mobile(self, value: str) -> str:
        """Validate mobile number format."""
        if not re.match(r"^[6-9][0-9]{9}$", value):
            raise serializers.ValidationError(
                "Enter a valid 10-digit Indian mobile number starting with 6-9."
            )
        return value

    def validate(self, attrs: dict) -> dict:
        """Cross-field validation."""
        # Get business from context using the shared helper (handles users without profiles)
        request = self.context.get("request")
        business = get_business(request.user) if request else None

        # GSTIN/PAN: fall back to the stored values so a PATCH of just the name
        # still runs the same cross-checks.
        gstin = attrs.get("gstin") or (self.instance.gstin if self.instance else "")
        pan = attrs.get("pan") or (self.instance.pan if self.instance else "")

        # Resolve the state: explicit value > derived from the GSTIN > already stored.
        if "state_code" in attrs:
            resolved_state = attrs["state_code"]
        elif "gstin" in attrs and gstin:
            resolved_state = gstin[:2]
        elif self.instance:
            resolved_state = self.instance.state_code
        elif gstin:
            resolved_state = gstin[:2]
        else:
            resolved_state = ""

        # A state is mandatory: it decides CGST+SGST vs IGST on every invoice.
        if not resolved_state:
            raise serializers.ValidationError(
                {"state_code": "Select the party's state."}
            )
        attrs["state_code"] = resolved_state

        # A shipping state only makes sense alongside a shipping address. Without
        # this check a stray shipping_state_code would silently change place of
        # supply on an invoice while the address still shows the billing state.
        shipping_state = attrs.get("shipping_state_code") or (
            self.instance.shipping_state_code if self.instance else ""
        )
        shipping_address = attrs.get("shipping_address", None)
        if shipping_address is None and self.instance:
            shipping_address = self.instance.shipping_address
        has_shipping_address = bool((shipping_address or "").strip())
        if shipping_state and not has_shipping_address:
            raise serializers.ValidationError(
                {
                    "shipping_state_code": (
                        "Enter a shipping address, or clear the shipping state "
                        "to ship to the billing state."
                    )
                }
            )
        if has_shipping_address and not shipping_state:
            # Blank means "same as billing", which is the intended default.
            attrs["shipping_state_code"] = ""

        # If GSTIN provided but PAN not, auto-fill from GSTIN
        if gstin and "pan" not in attrs:
            attrs["pan"] = gstin[2:12]

        # Cross-check the PAN against the GSTIN it must match.
        if gstin and pan and gstin[2:12] != pan.upper():
            raise serializers.ValidationError(
                {"pan": "PAN must match characters 3-12 of the GSTIN."}
            )

        # Check for duplicate GSTIN (per business)
        if gstin and business:
            qs = Party.objects.filter(business=business, gstin=gstin)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"gstin": "A party with this GSTIN already exists for your business."})

        # Check for duplicate PAN (per business)
        if pan and business:
            qs = Party.objects.filter(business=business, pan=pan)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"pan": "A party with this PAN already exists for your business."})

        return attrs


class PartyListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""

    display_state = serializers.CharField(source="get_display_state", read_only=True)
    is_registered = serializers.BooleanField(read_only=True)

    class Meta:
        model = Party
        fields = [
            "id",
            "name",
            "party_type",
            "mobile",
            "email",
            "gstin",
            "state_code",
            "display_state",
            "is_registered",
            "opening_balance",   # new
            "balance_type",
            "is_active",
            "created_at",
        ]