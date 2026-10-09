"""Serializers: registration and business profile."""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from .models import BusinessProfile
from .services import get_or_create_walk_in_party

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    """Creates a user plus their (initially minimal) business profile."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    trade_name = serializers.CharField(max_length=150)

    def validate_email(self, value: str) -> str:
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value)  # runs the validators configured in settings.py
        return value

    @transaction.atomic
    def create(self, validated_data: dict) -> "User":
        user = User.objects.create_user(
            email=validated_data["email"], password=validated_data["password"]
        )
        business = BusinessProfile.objects.create(
            user=user,
            trade_name=validated_data["trade_name"],
            company_name=validated_data["trade_name"],  # user can change it on the profile page
            email=user.email,
        )
        # Cash-sale customer, created up front so the billing form always has a
        # usable counter party. Its state is filled in once the business sets one
        # (a brand-new profile has no state yet).
        get_or_create_walk_in_party(business)
        return user


class BusinessProfileSerializer(serializers.ModelSerializer):
    """Read and update the logged-in user's business profile."""

    # True once the details needed for GST invoicing are filled in.
    is_complete = serializers.SerializerMethodField()

    # Fields typed in lower case that must be stored in upper case.
    UPPERCASE_FIELDS = ("gstin", "pan", "bank_ifsc")

    class Meta:
        model = BusinessProfile
        fields = [
            "id",
            "trade_name",
            "company_name",
            "owner_name",
            "phone",
            "alternate_phone",
            "email",
            "website",
            "gstin",
            "pan",
            "address_line",
            "city",
            "state_code",
            "pincode",
            "logo",
            "bank_account_name",
            "bank_account_number",
            "bank_ifsc",
            "bank_name",
            "bank_branch",
            "gst_registration_type",
            "round_invoice_total",
            "invoice_number_prefix",
            "hsn_min_digits",
            "is_complete",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "logo", "created_at", "updated_at"]

    def get_is_complete(self, obj: BusinessProfile) -> bool:
        return bool(obj.state_code and obj.address_line and obj.city and obj.pincode)

    def to_internal_value(self, data):
        """Clean raw input BEFORE field validators run (so regexes see upper case)."""
        data = data.copy() if hasattr(data, "copy") else dict(data)
        for field in self.UPPERCASE_FIELDS:
            if isinstance(data.get(field), str):
                data[field] = data[field].strip().upper()
        website = data.get("website")
        if isinstance(website, str) and website.strip() and "://" not in website:
            data["website"] = "https://" + website.strip()
        prefix = data.get("invoice_number_prefix")
        if isinstance(prefix, str):
            data["invoice_number_prefix"] = prefix.strip().upper()
        return super().to_internal_value(data)

    def validate_invoice_number_prefix(self, value: str) -> str:
        """Trim and uppercase, then let the regex report anything unusable."""
        value = (value or "").strip().upper()
        if not value:
            raise serializers.ValidationError("Enter an invoice prefix, e.g. INV.")
        return value

    def validate(self, attrs: dict) -> dict:
        """Cross-field check: a GSTIN embeds the state code and the PAN."""

        def current(name: str) -> str:
            # On partial updates, fall back to the value already saved.
            if name in attrs:
                return attrs[name]
            return getattr(self.instance, name, "") if self.instance else ""

        gstin, state_code, pan = current("gstin"), current("state_code"), current("pan")
        errors = {}
        if gstin and state_code and gstin[:2] != state_code:
            errors["gstin"] = "The first 2 digits of the GSTIN must match the selected state code."
        if gstin and pan and gstin[2:12] != pan:
            errors["pan"] = "PAN must match characters 3-12 of the GSTIN."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class LogoUploadSerializer(serializers.Serializer):
    """Validates an uploaded logo: a real image, an allowed type, and a size limit."""

    MAX_BYTES = 2 * 1024 * 1024  # 2 MB
    ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}

    logo = serializers.ImageField()  # opened with Pillow, so non-images are rejected

    def validate_logo(self, file):
        if file.size > self.MAX_BYTES:
            raise serializers.ValidationError("Logo must be 2 MB or smaller.")
        # Pillow reports the real format, whatever the filename or browser claims.
        image_format = getattr(getattr(file, "image", None), "format", None)
        if image_format not in self.ALLOWED_FORMATS:
            raise serializers.ValidationError("Logo must be a PNG, JPG or WebP image.")
        return file
