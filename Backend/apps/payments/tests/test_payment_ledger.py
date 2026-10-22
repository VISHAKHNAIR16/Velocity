"""
Payment ledger tests (3.1).

Covers: invoice paid_amount / balance_due / payment_status recalculation after
each payment, running balance correctness, party statement integration.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework import status

from apps.invoices.models import Invoice

from .base import (
    PAYMENT_LIST_URL,
    api_client_for,
    make_issued_invoice,
    make_party,
    make_user,
)


class PaymentLedgerTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.invoice = make_issued_invoice(self.user, self.party, "1000.00")

    def post_payment(self, amount, date="2026-01-20", invoice=None, **overrides):
        payload = {
            "party": self.party.pk,
            "transaction_type": "PAYMENT_IN",
            "payment_date": date,
            "payment_mode": "UPI",
            "amount": str(amount),
            "invoice": invoice.pk if invoice else self.invoice.pk,
        }
        payload.update(overrides)
        return self.client_.post(PAYMENT_LIST_URL, payload, format="json")

    def test_two_partial_payments_then_full(self):
        """paid_amount accumulates; status goes UNPAID -> PARTIAL -> PAID."""
        # Start: UNPAID
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.UNPAID)
        self.assertEqual(self.invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("1000.00"))

        # First partial: 400 -> PARTIAL, balance_due 600
        response = self.post_payment(Decimal("400.00"))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("400.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("600.00"))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.PARTIAL)

        # Second partial: 300 more -> PARTIAL, balance_due 300
        response = self.post_payment(Decimal("300.00"), date="2026-01-21")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("700.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("300.00"))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.PARTIAL)

        # Final: 300 -> PAID, balance_due 0
        response = self.post_payment(Decimal("300.00"), date="2026-01-22")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("1000.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("0.00"))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.PAID)

    def test_overpayment_marks_paid(self):
        """Paying more than grand_total caps balance_due at 0 and marks PAID."""
        response = self.post_payment(Decimal("1500.00"))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("1500.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("0.00"))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.PAID)

    def test_payment_without_invoice_does_not_touch_any_invoice(self):
        """PAYMENT_IN with no invoice link is recorded but recalculates nothing."""
        response = self.client_.post(
            PAYMENT_LIST_URL,
            {
                "party": self.party.pk,
                "transaction_type": "PAYMENT_IN",
                "payment_date": "2026-01-20",
                "payment_mode": "CASH",
                "amount": "100.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.UNPAID)

    def test_payment_out_does_not_affect_invoice(self):
        """PAYMENT_OUT to a supplier never touches a sales invoice."""
        supplier = make_party(self.user, "Supplier Co", party_type="SUPPLIER")
        response = self.client_.post(
            PAYMENT_LIST_URL,
            {
                "party": supplier.pk,
                "transaction_type": "PAYMENT_OUT",
                "payment_date": "2026-01-20",
                "payment_mode": "BANK_TRANSFER",
                "amount": "500.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.UNPAID)
        self.assertEqual(self.invoice.paid_amount, Decimal("0.00"))
