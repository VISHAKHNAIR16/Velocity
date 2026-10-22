"""
Payment models for the Velocity GST Billing platform.

A PaymentRecord is money moving between the business and a party:
- PAYMENT_IN: customer pays the business (against an invoice, or on account).
- PAYMENT_OUT: business pays a supplier (against a purchase bill, or on account).

Design rules:

1. **Append-only.** A payment is never edited or deleted after creation.
   Corrections are made by posting a reversing payment (PAYMENT_OUT for a
   PAYMENT_IN that was entered in error, and vice versa). This mirrors the
   append-only StockMovement ledger (2.1) and keeps the audit trail intact.

2. **Server-computed invoice state.** `paid_amount`, `balance_due` and
   `payment_status` on the linked Invoice are recalculated by the service
   layer, never accepted from the client. They are read_only on the serializer.

3. **Tenancy.** Every payment belongs to exactly one business, enforced by
   TenantModel and TenantPrimaryKeyRelatedField.

4. **Party can be CUSTOMER or SUPPLIER.** party_type is not constrained here;
   the UI decides which parties appear in the "receive payment" vs "make
   payment" pickers.
"""

from decimal import Decimal

from django.db import models

from apps.core.models import TenantModel
from apps.invoices.models import Invoice
from apps.parties.models import Party

ZERO = Decimal("0.00")


class PaymentRecord(TenantModel):
    """
    One payment received from or made to a party.

    `invoice` is optional: a payment can be on account (advance against a
    future invoice, or a payment against a statement balance with no single
    invoice). When linked to an ISSUED invoice, `record_payment()` recalculates
    that invoice's `paid_amount`, `balance_due` and `payment_status`.
    """

    class TransactionType(models.TextChoices):
        PAYMENT_IN = "PAYMENT_IN", "Payment In"
        PAYMENT_OUT = "PAYMENT_OUT", "Payment Out"

    class PaymentMode(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
        UPI = "UPI", "UPI"
        CHEQUE = "CHEQUE", "Cheque"

    #: Back-compat alias. `PaymentRecord.TransactionType.PAYMENT_IN` is used
    #: across the codebase.
    TransactionType = TransactionType

    #: Back-compat alias. `PaymentRecord.PaymentMode.UPI` is used across the
    #: codebase.
    PaymentMode = PaymentMode

    party = models.ForeignKey(
        Party,
        on_delete=models.PROTECT,
        related_name="payments",
        help_text="Customer (PAYMENT_IN) or supplier (PAYMENT_OUT).",
    )
    transaction_type = models.CharField(
        max_length=12,
        choices=TransactionType.choices,
        db_index=True,
    )
    invoice = models.ForeignKey(
        Invoice,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payments",
        help_text="Optional. Only ISSUED invoices may be linked.",
    )
    payment_date = models.DateField(db_index=True)
    payment_mode = models.CharField(
        max_length=20,
        choices=PaymentMode.choices,
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Amount paid. Must be greater than zero.",
    )
    reference_number = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="UPI reference, cheque number, or bank reference.",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-payment_date", "-created_at"]
        indexes = [
            models.Index(fields=["business", "party", "-payment_date"]),
            models.Index(fields=["business", "transaction_type", "-payment_date"]),
            models.Index(fields=["business", "invoice"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="payment_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.get_transaction_type_display()} "
            f"{self.amount} from {self.party.name} on {self.payment_date}"
        )

    @property
    def is_in(self) -> bool:
        return self.transaction_type == self.TransactionType.PAYMENT_IN

    @property
    def is_out(self) -> bool:
        return self.transaction_type == self.TransactionType.PAYMENT_OUT
