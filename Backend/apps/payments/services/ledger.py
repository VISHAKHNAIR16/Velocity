"""
Payment ledger service.

`record_payment()` is the single write path for all payments. It runs inside
`@transaction.atomic` so the payment row and the linked invoice's
`paid_amount` / `balance_due` / `payment_status` always commit together.
"""

from decimal import Decimal

from django.db import transaction

from apps.invoices.models import Invoice
from apps.parties.models import Party

from ..errors import PaymentConflict, PaymentError
from ..models import PaymentRecord


def record_payment(
    *,
    business,
    party: Party,
    transaction_type: str,
    payment_date,
    payment_mode: str,
    amount: Decimal,
    invoice: Invoice | None = None,
    reference_number: str = "",
    notes: str = "",
) -> PaymentRecord:
    """
    Create a PaymentRecord and, when linked to an ISSUED invoice, recalculate
    that invoice's `paid_amount`, `balance_due` and `payment_status`.

    Returns the created PaymentRecord.

    Raises:
        PaymentConflict: invoice is linked but not ISSUED.
        PaymentError: party and invoice belong to different businesses.
    """
    # --- Gate: invoice must belong to the same business as the party -------
    if invoice is not None:
        if invoice.business_id != business.pk:
            raise PaymentError(
                code="INVOICE_WRONG_BUSINESS",
                message=("The invoice belongs to a different business than this payment."),
                field="invoice",
            )
        if invoice.party_id != party.pk:
            raise PaymentError(
                code="INVOICE_WRONG_PARTY",
                message=("The invoice is not for this party."),
                field="invoice",
            )
        if invoice.status != Invoice.Status.ISSUED:
            raise PaymentConflict(
                code="INVOICE_NOT_ISSUED",
                message=(
                    "Payments can only be linked to ISSUED invoices. "
                    f"Invoice {invoice.display_number} is {invoice.get_status_display()}."
                ),
                field="invoice",
            )

    with transaction.atomic():
        payment = PaymentRecord.objects.create(
            business=business,
            party=party,
            transaction_type=transaction_type,
            payment_date=payment_date,
            payment_mode=payment_mode,
            amount=amount,
            invoice=invoice,
            reference_number=reference_number,
            notes=notes,
        )

        # --- Recalculate the linked invoice's payment state ----------------
        if invoice is not None and transaction_type == PaymentRecord.TransactionType.PAYMENT_IN:
            _recalculate_invoice_payment(invoice)

    return payment


def _recalculate_invoice_payment(invoice: Invoice) -> None:
    """
    Sum all PAYMENT_IN payments for this invoice and persist
    `paid_amount`, `balance_due` and `payment_status`.

    Called inside `record_payment()`'s transaction, so nothing is visible until
    the whole payment + recalculation commits.
    """
    from decimal import Decimal as _Decimal

    total_paid = invoice.payments.filter(
        transaction_type=PaymentRecord.TransactionType.PAYMENT_IN
    ).aggregate(total=models_sum("amount"))["total"] or _Decimal("0.00")

    invoice.paid_amount = total_paid
    invoice.balance_due = max(invoice.grand_total - total_paid, _Decimal("0.00"))

    if invoice.balance_due <= 0:
        invoice.payment_status = Invoice.PaymentStatus.PAID
    elif total_paid > 0:
        invoice.payment_status = Invoice.PaymentStatus.PARTIAL
    else:
        invoice.payment_status = Invoice.PaymentStatus.UNPAID

    invoice.save(update_fields=["paid_amount", "balance_due", "payment_status", "updated_at"])


def models_sum(field_name: str):
    """Tiny helper so `_recalculate_invoice_payment` doesn't import Sum at module
    top level (keeps the module importable without Django ORM context in tests)."""
    from django.db.models import Sum

    return Sum(field_name)
