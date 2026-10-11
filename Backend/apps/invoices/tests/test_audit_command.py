"""
The auditor must be able to FAIL.

A checker that only ever prints "OK" is indistinguishable from one that is not
checking anything. Every test here corrupts a field deliberately and asserts the
right code is reported - so a future change that quietly narrows a check is
caught.
"""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.invoices.tests.base import (
    api_client_for,
    line_payload,
    make_item,
    make_party,
    make_user,
)
from apps.invoices.tests.test_issue_api import issue_url


class AuditInvoiceTests(TestCase):
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
        response = self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.invoice_id = self.draft["id"]

    # -- helpers ------------------------------------------------------------
    def audit(self, **options):
        """Run the command; return (exit_code, combined_output)."""
        out, err = StringIO(), StringIO()
        code = 0
        try:
            call_command("audit_invoices", stdout=out, stderr=err, **options)
        except SystemExit as exc:
            code = exc.code or 0
        return code, out.getvalue() + err.getvalue()

    def corrupt(self, **fields):
        from apps.invoices.models import Invoice

        Invoice.objects.filter(pk=self.invoice_id).update(**fields)

    def line(self):
        from apps.invoices.models import InvoiceItem

        return InvoiceItem.objects.get(invoice_id=self.invoice_id)

    # -- the clean case -----------------------------------------------------
    def test_a_clean_dataset_passes(self):
        code, output = self.audit()
        self.assertEqual(code, 0, output)
        self.assertIn("OK", output)

    def test_a_clean_dataset_reports_nothing(self):
        code, output = self.audit()
        self.assertEqual(code, 0, output)
        self.assertNotIn("finding", output.lower())

    # -- money --------------------------------------------------------------
    def test_a_tampered_grand_total_is_caught(self):
        self.corrupt(grand_total=Decimal("1.00"))
        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertIn("MONEY_MISMATCH", output)
        self.assertIn("grand_total", output)

    def test_a_tampered_line_total_is_caught(self):
        from apps.invoices.models import InvoiceItem

        InvoiceItem.objects.filter(invoice_id=self.invoice_id).update(
            taxable_value=Decimal("999.00")
        )
        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertIn("LINE_SUM_MISMATCH", output)

    def test_lines_that_do_not_sum_to_the_invoice_are_caught(self):
        """Invoice total still right, one line wrong: only a sum check finds it."""
        from apps.invoices.models import Invoice, InvoiceItem

        line = InvoiceItem.objects.get(invoice_id=self.invoice_id)
        InvoiceItem.objects.filter(pk=line.pk).update(total_amount=line.total_amount)
        # Move 1 rupee of taxable value onto the line without the invoice seeing it.
        Invoice.objects.filter(pk=self.invoice_id).update(
            taxable_total=line.taxable_value - Decimal("1.00")
        )
        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertTrue(
            "MONEY_MISMATCH" in output or "LINE_SUM_MISMATCH" in output, output
        )

    def test_line_tax_arithmetic_is_checked(self):
        """
        Corrupting one tax column trips two independent checks: the line no
        longer sums to the invoice, and taxable + tax no longer equals the line
        total. Either alone is enough to fail the audit.
        """
        from apps.invoices.models import InvoiceItem

        InvoiceItem.objects.filter(invoice_id=self.invoice_id).update(
            cgst_amount=Decimal("7.77")
        )
        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertIn("LINE_SUM_MISMATCH", output)
        self.assertIn("LINE_TAX_ARITHMETIC", output)

    # -- round-off ----------------------------------------------------------
    def test_an_impossible_round_off_is_caught(self):
        self.corrupt(round_off=Decimal("4.00"))
        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertIn("ROUND_OFF", output)

    def test_a_round_off_that_does_not_reconcile_is_caught(self):
        self.corrupt(round_off=Decimal("0.25"))
        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertIn("ROUND_OFF", output)

    # -- supply type --------------------------------------------------------
    def test_an_inconsistent_supply_type_is_caught(self):
        """Stored INTRA while the states say inter - a wrong tax type."""
        from apps.invoices.models import Invoice

        Invoice.objects.filter(pk=self.invoice_id).update(place_of_supply="29")
        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertIn("SUPPLY_TYPE_INCONSISTENT", output)

    def test_a_missing_place_of_supply_is_caught(self):
        """
        Reported as a recalculation refusal rather than the supply-type finding:
        with no place of supply the calculator cannot even run, and that is the
        more precise diagnosis. The audit still fails, which is the point.
        """
        self.corrupt(place_of_supply="")
        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertTrue(
            "RECALC_REFUSED" in output or "PLACE_OF_SUPPLY_UNRESOLVED" in output, output
        )

    def test_tax_on_a_bill_of_supply_is_caught(self):
        from apps.invoices.models import Invoice

        Invoice.objects.filter(pk=self.invoice_id).update(document_title="Bill of Supply")
        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertIn("TAX_ON_BILL_OF_SUPPLY", output)

    # -- number series ------------------------------------------------------
    def test_a_gap_in_the_series_is_caught(self):
        """Consume 00002 without an invoice existing for it."""
        from apps.invoices.models import Invoice

        second = self.client_.post(
            "/api/v1/invoices/",
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()
        self.client_.post(issue_url(second["id"]), {}, format="json")
        Invoice.objects.filter(pk=second["id"]).delete()

        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertTrue(
            "GAP_IN_NUMBER_SERIES" in output or "COUNTER_DISAGREES" in output, output
        )

    def test_a_counter_that_disagrees_is_caught(self):
        from apps.invoices.models import InvoiceCounter

        InvoiceCounter.objects.update(last_number=99)
        code, output = self.audit()
        self.assertEqual(code, 1, output)
        self.assertIn("COUNTER_DISAGREES", output)

    def test_a_cancelled_number_is_not_a_gap(self):
        """
        The number is never freed, so cancelling leaves the series intact. A
        checker that reported this as a gap would cry wolf on every cancellation.
        """
        self.client_.post(
            f"/api/v1/invoices/{self.invoice_id}/cancel/",
            {"reason": "Audit test"},
            format="json",
        )
        code, output = self.audit()
        self.assertEqual(code, 0, output)

    # -- scoping ------------------------------------------------------------
    def test_it_can_be_limited_to_one_business(self):
        code, output = self.audit(business=self.user.business.id)
        self.assertEqual(code, 0, output)
        self.assertIn("OK", output)

    def test_it_can_be_limited_to_one_status(self):
        code, output = self.audit(status="CANCELLED")
        self.assertEqual(code, 0, output)
        self.assertIn("0 issued invoice(s)", output)

    def test_drafts_are_not_audited(self):
        """
        A draft is editable and may hold half-finished figures. Auditing it would
        report noise on every run.
        """
        fresh = self.client_.post(
            "/api/v1/invoices/",
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()
        self.assertEqual(fresh["status"], "DRAFT")
        code, output = self.audit()
        self.assertEqual(code, 0, output)

    def test_it_can_be_limited_to_one_financial_year(self):
        code, output = self.audit(fy="2026-27")
        self.assertEqual(code, 0, output)


class AuditIsNotDestructiveTests(TestCase):
    """The auditor only reads. It must never repair anything silently."""

    def setUp(self):
        self.user = make_user()
        client_ = api_client_for(self.user)
        party = make_party(self.user)
        item = make_item(self.user)
        draft = client_.post(
            "/api/v1/invoices/",
            {"party": party.pk, "items": [line_payload(item)]},
            format="json",
        ).json()
        client_.post(issue_url(draft["id"]), {}, format="json")

    def test_running_it_twice_reports_the_same_thing(self):
        out = StringIO()
        for _ in range(2):
            call_command("audit_invoices", stdout=out, stderr=out)
        text = out.getvalue()
        self.assertEqual(text.count("OK:"), 2)
        self.assertNotIn("finding", text.lower())