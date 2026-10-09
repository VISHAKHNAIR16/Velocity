"""Party (Customer/Vendor) models for the GST Billing platform."""

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.accounts.models import (
    gstin_validator,
    mobile_validator,
    pan_validator,
    pincode_validator,
)
from apps.core.constants import GST_STATE_CHOICES
from apps.core.models import TenantModel


class Party(TenantModel):
    """
    A party (customer or vendor) belonging to a business.
    Inherits multi-tenancy via TenantModel (business FK + timestamps).
    """

    class PartyType(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        SUPPLIER = "SUPPLIER", "Supplier"
        BOTH = "BOTH", "Both"

    class BalanceType(models.TextChoices):
        CREDIT = "CREDIT", "Credit (Party owes us)"
        DEBIT = "DEBIT", "Debit (We owe party)"

    # --- Identity ---
    name = models.CharField(max_length=200)
    party_type = models.CharField(
        max_length=10,
        choices=PartyType.choices,
        default=PartyType.CUSTOMER,
        db_index=True,
    )
    mobile = models.CharField(max_length=10, validators=[mobile_validator])
    email = models.EmailField(blank=True)

    # --- Tax Identity ---
    gstin = models.CharField(
        "GSTIN",
        max_length=15,
        blank=True,
        validators=[gstin_validator],
        help_text="15-character GSTIN. Leave blank for unregistered parties.",
    )
    pan = models.CharField(
        "PAN",
        max_length=10,
        blank=True,
        validators=[pan_validator],
        help_text="10-character PAN. Auto-filled from GSTIN if provided.",
    )

    # --- State (crucial for CGST+SGST vs IGST determination) ---
    state_code = models.CharField(
        max_length=2,
        choices=GST_STATE_CHOICES,
        db_index=True,
        help_text="2-digit GST state code. Determines intra/inter-state taxation.",
    )

    # --- Is this the walk-in counter customer? (decision 21) ---
    # A flag, not a name lookup. Decision 20 fixed the "walk-in can't be billed"
    # bug by letting *every* blank-state party fall back to the business state,
    # which meant a real customer who left their state blank silently got
    # CGST+SGST instead of IGST. Only a party explicitly flagged here may borrow
    # the business state; anyone else with a blank state is refused.
    #
    # The state is deliberately never stored for a walk-in (see the data
    # migration in parties/0006): it is resolved from the business at invoice
    # time, so it can never go stale when the business moves state.
    is_walk_in = models.BooleanField(
        default=False,
        help_text=(
            "The business's walk-in / cash customer. At most one per business. "
            "Its place of supply is resolved from the business at invoice time."
        ),
    )

    # --- Billing Address ---
    billing_address = models.TextField(blank=True)
    billing_city = models.CharField(max_length=100, blank=True)
    billing_pincode = models.CharField(
        max_length=6, blank=True, validators=[pincode_validator]
    )

    # --- Shipping Address (optional, defaults to billing if empty) ---
    shipping_address = models.TextField(blank=True)
    shipping_city = models.CharField(max_length=100, blank=True)
    shipping_pincode = models.CharField(
        max_length=6, blank=True, validators=[pincode_validator]
    )
    shipping_state_code = models.CharField(
        max_length=2,
        blank=True,
        choices=GST_STATE_CHOICES,
        help_text=(
            "Set only when goods are shipped to a different state from the billing "
            "address. It decides place of supply for goods on an invoice "
            "(intra-state CGST+SGST vs inter-state IGST). Leave blank to ship "
            "to the billing state."
        ),
    )

    # --- Opening Balance (for ledger carry-forward) ---
    opening_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(Decimal("9999999999.99"))],
        help_text="Opening balance as of the start date. Positive number only.",
    )
    balance_type = models.CharField(
        max_length=6,
        choices=BalanceType.choices,
        default=BalanceType.CREDIT,
        help_text="CREDIT = Party owes us (receivable). DEBIT = We owe party (payable).",
    )

    # --- Status ---
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Soft delete flag. Inactive parties are hidden from dropdowns but preserved for historical records.",
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["business", "party_type", "is_active"]),
            models.Index(fields=["business", "name"]),
            models.Index(fields=["business", "gstin"]),
            models.Index(fields=["business", "mobile"]),
        ]
        constraints = [
            # Conditional on both a non-blank value AND still being active, so a
            # soft-deleted party's GSTIN/PAN can be reused while the row is kept
            # for historic invoices. (Relaxing only - this cannot break existing
            # rows, since the old constraint applied to every row.)
            models.UniqueConstraint(
                fields=["business", "gstin"],
                condition=models.Q(gstin__gt="", is_active=True),
                name="unique_gstin_per_business",
            ),
            models.UniqueConstraint(
                fields=["business", "pan"],
                condition=models.Q(pan__gt="", is_active=True),
                name="unique_pan_per_business",
            ),
            # At most ONE walk-in per business (decision 21). Partial on
            # `is_walk_in`, so every normal customer is unaffected.
            models.UniqueConstraint(
                fields=["business"],
                condition=models.Q(is_walk_in=True),
                name="one_walk_in_per_business",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_party_type_display()})"

    def get_display_state(self) -> str:
        """Return human-readable state name from code."""
        return dict(GST_STATE_CHOICES).get(self.state_code, self.state_code)

    def get_shipping_address_lines(self) -> list[str]:
        """Return shipping address lines, falling back to billing if empty."""
        if self.shipping_address or self.shipping_city or self.shipping_pincode:
            return [
                self.shipping_address,
                self.shipping_city,
                self.shipping_pincode,
            ]
        return [
            self.billing_address,
            self.billing_city,
            self.billing_pincode,
        ]

    def get_billing_address_lines(self) -> list[str]:
        """Return billing address lines."""
        return [
            self.billing_address,
            self.billing_city,
            self.billing_pincode,
        ]

    @property
    def is_registered(self) -> bool:
        """True if party has a valid GSTIN (registered under GST)."""
        return bool(self.gstin)

    @property
    def opening_balance_signed(self) -> Decimal:
        """Credit (party owes us) is positive, debit (we owe party) is negative."""
        if self.balance_type == self.BalanceType.CREDIT:
            return self.opening_balance
        return -self.opening_balance