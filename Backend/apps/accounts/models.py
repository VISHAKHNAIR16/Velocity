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


class UserSession(models.Model):
    """
    One logged-in device/browser, so multiple simultaneous logins are visible
    and revocable.

    **The gap this fills.** SimpleJWT issues a refresh token valid for 7 days.
    Without the blacklist app there was no way to invalidate one: "logout" only
    deleted the token from the browser, so a stolen or borrowed refresh token
    stayed usable for a week with no way to cut it off, and the user had no way
    to see that they were signed in on three devices at once.

    A session row records the refresh token's `jti`, so revoking a session means
    blacklisting exactly that one token and leaving other devices signed in.

    Access tokens (30 minutes) cannot be revoked - they are stateless and already
    issued. Blacklisting the refresh token stops the session being *renewed*, so
    the practical worst case after a revoke is up to one access-token lifetime.
    That is the same trade-off every JWT deployment makes; it is stated here so
    nobody assumes revocation is instant.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    #: The refresh token's unique id. SimpleJWT stores the token itself in
    #: `OutstandingToken`; we only keep the id, so no secret sits in our table.
    jti = models.CharField(max_length=255, unique=True)

    #: Human-readable, e.g. "Chrome on Windows". Derived from the User-Agent at
    #: login; never trusted for authorisation, only shown back to the user.
    device = models.CharField(max_length=150, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_used_at"]
        indexes = [
            models.Index(fields=["user", "revoked_at"]),
            models.Index(fields=["user", "-last_used_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}: {self.device or 'unknown device'}"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


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
    RETIRED by decision 22 - kept only so the removal migration can name the old
    values, and so old migrations that referenced them keep importing.

    The old `STATUTORY` mode encoded a per-invoice Rs 5,000 threshold, which is
    not an HSN rule: the digit count required depends on the business's own
    annual turnover, not on an invoice's value or its recipient. See
    `BusinessProfile.hsn_min_digits`.
    """

    STRICT = "STRICT", "Strict - every line"
    STATUTORY = "STATUTORY", "Statutory - above Rs 5,000 to a GSTIN holder"


class HsnMinDigits(models.IntegerChoices):
    """
    How many digits of HSN/SAC this business must report (decision 22).

    The digits required scale with annual turnover, because GSTR-1 wants detail
    proportional to how big the taxpayer is:

        up to Rs 5 crore  -> 4 digits
        above Rs 5 crore  -> 6 digits

    `HSN_4` is the default so an existing business never becomes stricter without
    a deliberate choice. The direction of the risk is not symmetric: a
    superfluous digit is harmless, a missing one is an under-complied return that
    cannot be fixed after filing.
    """

    HSN_4 = 4, "4 digits (turnover up to Rs 5 crore)"
    HSN_6 = 6, "6 digits (turnover above Rs 5 crore)"


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
    hsn_min_digits = models.PositiveSmallIntegerField(
        "HSN/SAC digits required",
        choices=HsnMinDigits.choices,
        default=HsnMinDigits.HSN_4,
        help_text=(
            "How many digits of the HSN/SAC code your GSTR-1 needs. 4 digits up to "
            "Rs 5 crore annual turnover, 6 digits above. Every line of every tax "
            "invoice must meet this minimum. Confirm the figure with your CA."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.trade_name
