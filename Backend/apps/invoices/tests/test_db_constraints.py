"""
Decision 26: the database enforces what the service layer promises.

Until 2.0.7 the lifecycle and non-negativity rules lived only in the service
layer and the admin. One shell session, a bulk `update()`, or a future endpoint
could produce an "issued" invoice with no number - a document neither the books
nor the customer could reconcile.

Each test here deliberately tries to write the bad row **through the ORM**, so
it proves the database refuses rather than that a serializer does.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.invoices.models import Invoice, InvoiceCounter, InvoiceItem, InvoiceStatus
from apps.invoices.services.issue import is_number_race
from apps.invoices.tests.base import (
    api_client_for,
    line_payload,
    make_item,
    make_party,
    make_user,
)
from apps.invoices.tests.test_issue_api import issue_url


class ConstraintTestCase(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)
        self.draft = self.client_.post(
            "/api/v1/invoices/",
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()

    def assertRefused(self, callable_, *args, **kwargs):
        """The write must raise IntegrityError, and nothing may be left behind."""
        with self.assertRaises(IntegrityError) as ctx:
            with transaction.atomic():
                callable_(*args, **kwargs)
        return str(ctx.exception)

    def issue(self):
        response = self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201, response.content[:300])
        return response.json()


class InvoiceLifecycleConstraintTests(ConstraintTestCase):
    def test_an_issued_invoice_must_carry_a_number(self):
        message = self.assertRefused(
            Invoice.objects.filter(pk=self.draft["id"]).update,
            status=InvoiceStatus.ISSUED,
            issued_at=timezone.now(),
            invoice_number="",
        )
        self.assertIn("issued_invoice_has_number_and_timestamp", message)

    def test_an_issued_invoice_must_carry_a_timestamp(self):
        message = self.assertRefused(
            Invoice.objects.filter(pk=self.draft["id"]).update,
            status=InvoiceStatus.ISSUED,
            issued_at=None,
            invoice_number="INV/26-27/00001",
        )
        self.assertIn("issued_invoice_has_number_and_timestamp", message)

    def test_a_draft_must_not_carry_a_number(self):
        """
        The mirror image, and the one that was inverted when first written: it
        is the DRAFT that is constrained, not the issued invoice.
        """
        message = self.assertRefused(
            Invoice.objects.filter(pk=self.draft["id"]).update,
            invoice_number="INV/26-27/00001",
        )
        self.assertIn("draft_invoice_has_no_number", message)

    def test_a_properly_issued_invoice_is_accepted(self):
        """The constraints must not block the normal path."""
        issued = self.issue()
        self.assertTrue(issued["invoice_number"])
        record = Invoice.objects.get(pk=issued["id"])
        self.assertEqual(record.status, InvoiceStatus.ISSUED)
        self.assertIsNotNone(record.issued_at)

    def test_a_cancelled_invoice_must_carry_a_timestamp_and_reason(self):
        issued = self.issue()
        message = self.assertRefused(
            Invoice.objects.filter(pk=issued["id"]).update,
            status=InvoiceStatus.CANCELLED,
            cancelled_at=None,
            cancellation_reason="",
        )
        self.assertIn("cancelled_invoice_has_timestamp_and_reason", message)

    def test_a_cancelled_invoice_without_a_reason_is_refused(self):
        issued = self.issue()
        message = self.assertRefused(
            Invoice.objects.filter(pk=issued["id"]).update,
            status=InvoiceStatus.CANCELLED,
            cancelled_at=timezone.now(),
            cancellation_reason="",
        )
        self.assertIn("cancelled_invoice_has_timestamp_and_reason", message)

    def test_a_proper_cancellation_is_accepted(self):
        issued = self.issue()
        cancelled = self.client_.post(
            f"/api/v1/invoices/{issued['id']}/cancel/",
            {"reason": "Wrong address"},
            format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.content[:300])


class MoneyConstraintTests(ConstraintTestCase):
    def test_a_negative_grand_total_is_refused(self):
        message = self.assertRefused(
            Invoice.objects.filter(pk=self.draft["id"]).update, grand_total=Decimal("-1.00")
        )
        self.assertIn("invoice_grand_total_not_negative", message)

    def test_a_negative_subtotal_is_refused(self):
        message = self.assertRefused(
            Invoice.objects.filter(pk=self.draft["id"]).update, subtotal=Decimal("-0.01")
        )
        self.assertIn("invoice_subtotal_not_negative", message)

    def test_a_negative_total_discount_is_refused(self):
        message = self.assertRefused(
            Invoice.objects.filter(pk=self.draft["id"]).update,
            total_discount=Decimal("-0.01"),
        )
        self.assertIn("invoice_total_discount_not_negative", message)

    def test_zero_is_allowed(self):
        """`>= 0`, not `> 0`: a zero discount is the normal case."""
        Invoice.objects.filter(pk=self.draft["id"]).update(
            grand_total=Decimal("0.00"), subtotal=Decimal("0.00"), total_discount=Decimal("0.00")
        )
        record = Invoice.objects.get(pk=self.draft["id"])
        self.assertEqual(record.grand_total, Decimal("0.00"))


class LineConstraintTests(ConstraintTestCase):
    def line(self):
        return InvoiceItem.objects.get(invoice_id=self.draft["id"])

    def test_a_zero_quantity_line_is_refused(self):
        """A zero-quantity line contributes nothing but still prints."""
        message = self.assertRefused(
            InvoiceItem.objects.filter(invoice_id=self.draft["id"]).update,
            quantity=Decimal("0.000"),
        )
        self.assertIn("invoice_item_quantity_positive", message)

    def test_a_negative_quantity_line_is_refused(self):
        message = self.assertRefused(
            InvoiceItem.objects.filter(invoice_id=self.draft["id"]).update,
            quantity=Decimal("-1.000"),
        )
        self.assertIn("invoice_item_quantity_positive", message)

    def test_a_negative_unit_price_is_refused(self):
        message = self.assertRefused(
            InvoiceItem.objects.filter(invoice_id=self.draft["id"]).update,
            unit_price=Decimal("-5.00"),
        )
        self.assertIn("invoice_item_unit_price_not_negative", message)

    def test_a_negative_line_discount_is_refused(self):
        message = self.assertRefused(
            InvoiceItem.objects.filter(invoice_id=self.draft["id"]).update,
            line_discount=Decimal("-5.00"),
        )
        self.assertIn("invoice_item_line_discount_not_negative", message)

    def test_a_zero_price_is_allowed(self):
        """A free-of-charge line is legitimate; only negatives are refused."""
        InvoiceItem.objects.filter(invoice_id=self.draft["id"]).update(
            unit_price=Decimal("0.00"), line_discount=Decimal("0.00")
        )
        self.assertEqual(self.line().unit_price, Decimal("0.00"))

    def test_a_negative_computed_total_is_refused(self):
        message = self.assertRefused(
            InvoiceItem.objects.filter(invoice_id=self.draft["id"]).update,
            total_amount=Decimal("-1.00"),
        )
        self.assertIn("invoice_item_total_not_negative", message)


class CounterConstraintTests(TestCase):
    def test_a_negative_counter_is_refused(self):
        """A negative counter would silently restart a numbering series."""
        from apps.invoices.services.numbering import financial_year

        user = make_user()
        message = ""
        with self.assertRaises(IntegrityError) as ctx:
            with transaction.atomic():
                InvoiceCounter.objects.create(
                    business=user.business,
                    financial_year=financial_year(timezone.localdate()),
                    last_number=-1,
                    number_prefix="INV",
                )
        message = str(ctx.exception)
        self.assertIn("last_number", message)


class IntegrityErrorTranslationTests(ConstraintTestCase):
    """
    A regression with a nasty edge: `issue_invoice` used to translate *every*
    IntegrityError into `NUMBER_RACE` ("that number was just allocated to
    another session - please reload"). Before 2.0.7 there were no other
    constraints, so that was almost right; afterwards it would hide a genuine
    data bug behind a phantom concurrency story.
    """

    def test_the_unique_number_collision_is_a_race(self):
        # PostgreSQL's wording.
        postgres = IntegrityError(
            'duplicate key value violates unique constraint "unique_invoice_number_per_business"'
        )
        self.assertTrue(is_number_race(postgres))

    def test_the_sqlite_wording_is_also_recognised(self):
        """
        SQLite names COLUMNS, not the constraint. Matching only the Postgres
        wording would work in CI and fail on every local SQLite run - the
        "works on my machine" bug in its purest form.
        """
        sqlite = IntegrityError(
            "UNIQUE constraint failed: invoices_invoice.business_id, "
            "invoices_invoice.invoice_number"
        )
        self.assertTrue(is_number_race(sqlite))

    def test_a_check_constraint_failure_is_not_a_race(self):
        exc = IntegrityError("CHECK constraint failed: draft_invoice_has_no_number")
        self.assertFalse(
            is_number_race(exc),
            "a data defect must not be reported as a concurrency problem",
        )

    def test_an_unrelated_integrity_error_is_not_a_race(self):
        self.assertFalse(is_number_race(IntegrityError("FOREIGN KEY constraint failed")))