"""Custom user model: log in with email instead of a username."""

import os
import uuid

from django.conf import settings
from django.contrib.auth.models import (  # pyright: ignore[reportMissingModuleSource]
    AbstractUser,
    BaseUserManager,
)
from django.core.validators import RegexValidator
from django.db import models  # pyright: ignore[reportMissingModuleSource]

from .constants import GST_STATE_CHOICES


class UserManager(BaseUserManager):
    """Creates users and superusers identified by email."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        # Lowercase so "Raj@X.com" and "raj@x.com" are the same account.
        user = self.model(email=self.normalize_email(email).lower(), **extra_fields)
        user.set_password(password)  # hashes the password; never stores plain text
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not (extra_fields["is_staff"] and extra_fields["is_superuser"]):
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Application user. Business details live in BusinessProfile (next step)."""

    username = None  # removed: email is the login identifier
    email = models.EmailField("email address", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []  # email + password are always asked for

    objects = UserManager()

    def save(self, *args, **kwargs):
        self.email = self.email.lower()  # keep emails consistent even via the admin
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.email


def logo_upload_path(instance: "BusinessProfile", filename: str) -> str:
    """Store logos as business_logos/<business id>/<random>.<ext> (unique, no user filename)."""
    extension = os.path.splitext(filename)[1].lower()
    return f"business_logos/{instance.pk}/{uuid.uuid4().hex}{extension}"


# ---------------------------------------------------------------------------
# Format validators (reused by the API because ModelSerializer applies them)
# ---------------------------------------------------------------------------
gstin_validator = RegexValidator(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$",
    "Enter a valid 15-character GSTIN, e.g. 36ABCCS2942R1ZR.",
)
pan_validator = RegexValidator(
    r"^[A-Z]{5}[0-9]{4}[A-Z]$", "Enter a valid 10-character PAN, e.g. ABCCS2942R."
)
ifsc_validator = RegexValidator(r"^[A-Z]{4}0[A-Z0-9]{6}$", "Enter a valid 11-character IFSC code.")
pincode_validator = RegexValidator(r"^[1-9][0-9]{5}$", "Enter a valid 6-digit pincode.")
mobile_validator = RegexValidator(
    r"^[6-9][0-9]{9}$", "Enter a valid 10-digit Indian mobile number."
)
phone_validator = RegexValidator(r"^[0-9]{6,15}$", "Enter digits only (6 to 15 digits).")
account_number_validator = RegexValidator(
    r"^[0-9]{9,18}$", "Account number must be 9 to 18 digits."
)
# Invoice number prefix: short and unambiguous. GST Rule 46 caps the whole
# invoice number at 16 characters, and the prefix is part of it, so keep it to
# 1-4 characters of letters, digits or a hyphen.
invoice_prefix_validator = RegexValidator(
    r"^[A-Z0-9-]{1,4}$",
    "Prefix must be 1-4 characters using A-Z, 0-9 or a hyphen (e.g. INV).",
)


class HsnRequirement(models.TextChoices):
    """
    How strictly HSN/SAC codes are demanded when issuing an invoice.

    GST law requires HSN on a B2B invoice only when its value exceeds Rs 5,000.
    `STRICT` requires it on every line regardless, which over-complies: an
    under-complied tax invoice cannot be fixed after the fact, while an extra
    HSN code is harmless. `STATUTORY` implements the threshold literally.
    """

    STRICT = "STRICT", "Strict - every line"
    STATUTORY = "STATUTORY", "Statutory - above Rs 5,000 to a GSTIN holder"


class BusinessProfile(models.Model):
    """
    The business a user bills from. Every other business record (Party, Item,
    Invoice...) will link to this model, which keeps each tenant's data isolated.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="business"
    )

    # --- Identity (matches the profile screen) ---
    trade_name = models.CharField("trade / brand name", max_length=150)
    company_name = models.CharField("legal company name", max_length=200)
    owner_name = models.CharField(max_length=150, blank=True)

    # --- Contact ---
    phone = models.CharField(max_length=10, blank=True, validators=[mobile_validator])
    alternate_phone = models.CharField(max_length=15, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)

    # --- Tax identity ---
    gstin = models.CharField("GSTIN", max_length=15, blank=True, validators=[gstin_validator])
    pan = models.CharField("PAN", max_length=10, blank=True, validators=[pan_validator])

    # --- Address. state_code decides CGST+SGST vs IGST on every invoice. ---
    address_line = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state_code = models.CharField(max_length=2, blank=True, choices=GST_STATE_CHOICES)
    pincode = models.CharField(max_length=6, blank=True, validators=[pincode_validator])

    # --- Logo (upload API and Cloudinary come in a later step) ---
    logo = models.ImageField(upload_to=logo_upload_path, blank=True)

    # --- Bank details (printed on invoices) ---
    bank_account_name = models.CharField(max_length=150, blank=True)
    bank_account_number = models.CharField(
        max_length=18, blank=True, validators=[account_number_validator]
    )
    bank_ifsc = models.CharField(
        "IFSC code", max_length=11, blank=True, validators=[ifsc_validator]
    )
    bank_name = models.CharField(max_length=100, blank=True)
    bank_branch = models.CharField(max_length=100, blank=True)

    # --- Invoice preferences (used by the 1.4 invoicing engine) ---
    class GstRegistrationType(models.TextChoices):
        REGULAR = "REGULAR", "Regular"
        COMPOSITION = "COMPOSITION", "Composition"
        UNREGISTERED = "UNREGISTERED", "Unregistered"

    gst_registration_type = models.CharField(
        max_length=15,
        choices=GstRegistrationType.choices,
        default=GstRegistrationType.UNREGISTERED,
        help_text=(
            "Only 'Regular' businesses may charge GST on an invoice. Composition "
            "and Unregistered businesses must show tax as zero and use the title "
            "'Bill of Supply'."
        ),
    )
    round_invoice_total = models.BooleanField(
        default=False,
        help_text=(
            "Round the invoice total to the nearest rupee and show a 'Round Off' "
            "line. Presentation only: taxable values and tax amounts never change."
        ),
    )
    invoice_number_prefix = models.CharField(
        max_length=4,
        default="INV",
        validators=[invoice_prefix_validator],
        help_text="1-4 letters/digits used at the start of the invoice number, e.g. INV.",
    )
    hsn_requirement = models.CharField(
        max_length=10,
        choices=HsnRequirement.choices,
        default=HsnRequirement.STRICT,
        help_text=(
            "STRICT asks for an HSN/SAC code on every line of every tax invoice. "
            "STATUTORY only requires it above Rs 5,000 to a GSTIN holder, which "
            "is what the law actually says. STRICT over-complies on purpose - "
            "confirm with your CA before switching."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.trade_name
