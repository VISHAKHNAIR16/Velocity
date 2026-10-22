"""
Payment API tests (3.1).

Covers: create PAYMENT_IN / PAYMENT_OUT, reject DRAFT/CANCELLED invoice links,
reject cross-tenant invoices, recalculate invoice paid_amount / balance_due /
payment_status, append-only (no PUT/PATCH/DELETE), tenant isolation.
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework import status

from apps.invoices.models import Invoice

from .base import (
    PAYMENT_LIST_URL,
    api_client_for,
    make_issued_invoice,
    make_party,
    make_user,
)


def payment_url(payment) -> str:
    pk = payment.pk if hasattr(payment, "pk") else payment
    return f"/api/v1/payments/{pk}/"


class PaymentCreateTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.invoice = make_issued_invoice(self.user, self.party, "1000.00")

    def test_create_payment_in_updates_invoice_status(self):
        """PAYMENT_IN against an ISSUED invoice recalculates payment_status."""
        payload = {
            "party": self.party.pk,
            "transaction_type": "PAYMENT_IN",
            "payment_date": "2026-01-20",
            "payment_mode": "UPI",
            "amount": "400.00",
            "invoice": self.invoice.pk,
            "reference_number": "UPI123",
            "notes": "Partial payment",
        }
        response = self.client_.post(PAYMENT_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

        data = response.json()
        self.assertEqual(data["amount"], "400.00")
        self.assertEqual(data["transaction_type"], "PAYMENT_IN")

        # Invoice recalculated
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("400.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("600.00"))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.PARTIAL)

    def test_full_payment_marks_invoice_paid(self):
        """Paying the full grand_total marks the invoice PAID."""
        payload = {
            "party": self.party.pk,
            "transaction_type": "PAYMENT_IN",
            "payment_date": "2026-01-20",
            "payment_mode": "CASH",
            "amount": "1000.00",
            "invoice": self.invoice.pk,
        }
        response = self.client_.post(PAYMENT_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal("1000.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("0.00"))
        self.assertEqual(self.invoice.payment_status, Invoice.PaymentStatus.PAID)

    def test_create_payment_out(self):
        """PAYMENT_OUT to a supplier party does not touch any invoice."""
        supplier = make_party(self.user, "Supplier Co", party_type="SUPPLIER")
        payload = {
            "party": supplier.pk,
            "transaction_type": "PAYMENT_OUT",
            "payment_date": "2026-01-20",
            "payment_mode": "BANK_TRANSFER",
            "amount": "500.00",
            "reference_number": "NEFT456",
        }
        response = self.client_.post(PAYMENT_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        data = response.json()
        self.assertEqual(data["transaction_type"], "PAYMENT_OUT")
        self.assertIsNone(data["invoice"])

    def test_rejects_draft_invoice(self):
        """Payments may not link to a DRAFT invoice."""
        draft = Invoice.objects.create(
            business=self.user.business,
            party=self.party,
            invoice_date="2026-01-15",
            status=Invoice.Status.DRAFT,
            grand_total=Decimal("500.00"),
        )
        payload = {
            "party": self.party.pk,
            "transaction_type": "PAYMENT_IN",
            "payment_date": "2026-01-20",
            "payment_mode": "CASH",
            "amount": "100.00",
            "invoice": draft.pk,
        }
        response = self.client_.post(PAYMENT_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertEqual(response.json()["error"], "VALIDATION_ERROR")

    def test_rejects_cancelled_invoice(self):
        """Payments may not link to a CANCELLED invoice."""
        cancelled = Invoice.objects.create(
            business=self.user.business,
            party=self.party,
            invoice_date="2026-01-15",
            status=Invoice.Status.CANCELLED,
            invoice_number="INV/26-27/99999",
            issued_at=timezone.now(),
            cancelled_at=timezone.now(),
            cancellation_reason="Test cancellation",
            grand_total=Decimal("500.00"),
        )
        payload = {
            "party": self.party.pk,
            "transaction_type": "PAYMENT_IN",
            "payment_date": "2026-01-20",
            "payment_mode": "CASH",
            "amount": "100.00",
            "invoice": cancelled.pk,
        }
        response = self.client_.post(PAYMENT_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertEqual(response.json()["error"], "VALIDATION_ERROR")

    def test_rejects_cross_tenant_invoice(self):
        """An invoice from another business cannot be linked."""
        other = make_user(email="other@example.com", state_code="29")
        other_party = make_party(other, "Other Party", state_code="29")
        other_invoice = make_issued_invoice(other, other_party, "200.00")
        payload = {
            "party": self.party.pk,
            "transaction_type": "PAYMENT_IN",
            "payment_date": "2026-01-20",
            "payment_mode": "CASH",
            "amount": "100.00",
            "invoice": other_invoice.pk,
        }
        response = self.client_.post(PAYMENT_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)

    def test_rejects_invoice_for_different_party(self):
        """The linked invoice must belong to the same party as the payment."""
        other_party = make_party(self.user, "Other Customer")
        other_invoice = make_issued_invoice(self.user, other_party, "200.00")
        payload = {
            "party": self.party.pk,
            "transaction_type": "PAYMENT_IN",
            "payment_date": "2026-01-20",
            "payment_mode": "CASH",
            "amount": "100.00",
            "invoice": other_invoice.pk,
        }
        response = self.client_.post(PAYMENT_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)

    def test_rejects_zero_amount(self):
        """Amount must be greater than zero."""
        payload = {
            "party": self.party.pk,
            "transaction_type": "PAYMENT_IN",
            "payment_date": "2026-01-20",
            "payment_mode": "CASH",
            "amount": "0.00",
        }
        response = self.client_.post(PAYMENT_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)


class PaymentListTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.invoice = make_issued_invoice(self.user, self.party, "1000.00")

    def test_list_is_tenant_scoped(self):
        """Payments from another business never appear."""
        self.client_.post(
            PAYMENT_LIST_URL,
            {
                "party": self.party.pk,
                "transaction_type": "PAYMENT_IN",
                "payment_date": "2026-01-20",
                "payment_mode": "CASH",
                "amount": "100.00",
                "invoice": self.invoice.pk,
            },
            format="json",
        )

        other = make_user(email="other@example.com", state_code="29")
        other_client = api_client_for(other)
        other_party = make_party(other, "Other Party", state_code="29")
        other_invoice = make_issued_invoice(other, other_party, "500.00")
        other_client.post(
            PAYMENT_LIST_URL,
            {
                "party": other_party.pk,
                "transaction_type": "PAYMENT_IN",
                "payment_date": "2026-01-21",
                "payment_mode": "CASH",
                "amount": "500.00",
                "invoice": other_invoice.pk,
            },
            format="json",
        )

        response = self.client_.get(PAYMENT_LIST_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["amount"], "100.00")

    def test_filter_by_transaction_type(self):
        self.client_.post(
            PAYMENT_LIST_URL,
            {
                "party": self.party.pk,
                "transaction_type": "PAYMENT_IN",
                "payment_date": "2026-01-20",
                "payment_mode": "CASH",
                "amount": "100.00",
                "invoice": self.invoice.pk,
            },
            format="json",
        )
        supplier = make_party(self.user, "Supplier", party_type="SUPPLIER")
        self.client_.post(
            PAYMENT_LIST_URL,
            {
                "party": supplier.pk,
                "transaction_type": "PAYMENT_OUT",
                "payment_date": "2026-01-21",
                "payment_mode": "BANK_TRANSFER",
                "amount": "200.00",
            },
            format="json",
        )

        response = self.client_.get(PAYMENT_LIST_URL + "?transaction_type=PAYMENT_IN")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["transaction_type"], "PAYMENT_IN")

    def test_filter_by_date_range(self):
        self.client_.post(
            PAYMENT_LIST_URL,
            {
                "party": self.party.pk,
                "transaction_type": "PAYMENT_IN",
                "payment_date": "2026-01-20",
                "payment_mode": "CASH",
                "amount": "100.00",
                "invoice": self.invoice.pk,
            },
            format="json",
        )
        self.client_.post(
            PAYMENT_LIST_URL,
            {
                "party": self.party.pk,
                "transaction_type": "PAYMENT_IN",
                "payment_date": "2026-02-20",
                "payment_mode": "CASH",
                "amount": "200.00",
                "invoice": self.invoice.pk,
            },
            format="json",
        )

        response = self.client_.get(PAYMENT_LIST_URL + "?date_from=2026-02-01&date_to=2026-02-28")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["payment_date"], "2026-02-20")


class PaymentAppendOnlyTests(TestCase):
    """Payments cannot be edited or deleted after creation (3.1 decision)."""

    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.invoice = make_issued_invoice(self.user, self.party, "1000.00")
        self.payment = self.client_.post(
            PAYMENT_LIST_URL,
            {
                "party": self.party.pk,
                "transaction_type": "PAYMENT_IN",
                "payment_date": "2026-01-20",
                "payment_mode": "CASH",
                "amount": "300.00",
                "invoice": self.invoice.pk,
            },
            format="json",
        ).json()

    def test_put_is_refused(self):
        response = self.client_.put(
            payment_url(self.payment["id"]),
            {
                "party": self.party.pk,
                "transaction_type": "PAYMENT_IN",
                "payment_date": "2026-01-20",
                "payment_mode": "CASH",
                "amount": "500.00",
                "invoice": self.invoice.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_patch_is_refused(self):
        response = self.client_.patch(
            payment_url(self.payment["id"]),
            {
                "notes": "tampered",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_delete_is_refused(self):
        response = self.client_.delete(payment_url(self.payment["id"]))
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
