"""
Tests for `audit_purchases`.

The command's whole product is its exit code, so a test that only checks the
happy path proves nothing. Every test here either plants a defect or asserts
that a clean bill stays clean.
"""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.purchases.models import Purchase, PurchaseItem, PurchaseStatus
from apps.purchases.tests_api import header, line_payload, make_supplier

PURCHASES_URL = "/api/v1/purchases/"


def run_audit(*args):
    out = StringIO()
    try:
        call_command("audit_purchases", *args, stdout=out, stderr=out)
        return 0, out.getvalue()
    except SystemExit as exc:
        return exc.code, out.getvalue()


class AuditFixture(TestCase):
    def setUp(self):
        from apps.invoices.tests.base import api_client_for, make_user
        from apps.purchases.tests_api import make_tracked_item

        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.supplier = make_supplier(self.user)
        self.item = make_tracked_item(self.user)

    def post_bill(self, **overrides):
        items = overrides.pop("items", [line_payload(self.item)])
        supplier = overrides.pop("supplier", self.supplier)
        payload = header(supplier) | overrides | {"items": items}
        response = self.client_.post(PURCHASES_URL, payload, format="json")
        self.assertEqual(response.status_code, 201, response.content[:300])
        purchase_id = response.json()["id"]
        posted = self.client_.post(f"{PURCHASES_URL}{purchase_id}/post/", {}, format="json")
        self.assertIn(posted.status_code, (200, 201), posted.content[:300])
        return Purchase.objects.get(pk=purchase_id)


class CleanDataTests(AuditFixture):
    def test_a_correct_posted_bill_passes(self):
        self.post_bill()
        code, output = run_audit()
        self.assertEqual(code, 0, output)
        self.assertIn("agree", output)

    def test_drafts_are_skipped(self):
        """
        A draft is a working document being edited, so it is expected not to
        add up yet. Auditing drafts would report noise on every half-finished bill.
        """
        self.client_.post(
            PURCHASES_URL,
            header(self.supplier) | {"items": [line_payload(self.item)]},
            format="json",
        )
        code, output = run_audit()
        self.assertEqual(code, 0, output)
        self.assertIn("0 purchase bill", output)


class DetectionTests(AuditFixture):
    def test_a_header_that_disagrees_with_its_lines_is_caught(self):
        purchase = self.post_bill()
        # Bypass the serializer: this is the "how did that get in" scenario.
        Purchase.objects.filter(pk=purchase.pk).update(taxable_total=Decimal("500.00"))

        code, output = run_audit()
        self.assertEqual(code, 1, output)
        self.assertIn("LINE_SUM_MISMATCH", output)
        self.assertIn("SUP-001", output)

    def test_a_line_total_that_disagrees_with_the_calculator_is_reported(self):
        """
        The case the `*_computed` columns exist for. The bill is internally
        consistent but does not match the arithmetic, which is a fact about the
        supplier worth knowing about months later.
        """
        self.post_bill(
            grand_total="1300.00",
            items=[line_payload(self.item, total_amount="1300.00")],
        )
        code, output = run_audit()
        self.assertEqual(code, 1, output)
        self.assertIn("DIVERGENCE", output)

    def test_tax_on_an_unregistered_suppliers_bill_is_caught(self):
        supplier = make_supplier(
            self.user, name="Cash Supplier", gstin="", state="36"
        )
        self.post_bill(
            supplier=supplier,
            items=[line_payload(self.item, igst_amount="180.00")],
        )
        code, output = run_audit()
        self.assertEqual(code, 1, output)
        self.assertIn("TAX_ON_UNREGISTERED_BILL", output)

    def test_a_gap_in_the_suppliers_numbering_is_not_a_finding(self):
        """
        The distinction that makes this command trustworthy.

        `audit_invoices` flags gaps in our own series, because we allocate those
        numbers. Purchase numbers belong to the supplier: SUP-001 then SUP-900 is
        an ordinary fortnight, not a bug. Reporting it would train the user to
        ignore every line this command prints.

        Duplicates are the opposite and *are* checked - but they are prevented by
        a unique constraint (see the API tests), so they cannot be constructed
        here without fighting that constraint.
        """
        self.post_bill()
        # A second bill numbered far away from the first, and a large gap in the
        # ledger: SUP-001 then SUP-900.
        gap = Purchase(
            business=self.user.business,
            supplier=self.supplier,
            bill_number="SUP-900",
            bill_date=timezone.localdate(),
            supplier_name="Supplier Ltd",
            supplier_gstin=self.supplier.gstin,
            origin_state_code="27",
            place_of_supply=self.user.business.state_code,
            status=PurchaseStatus.POSTED,
            posted_at=timezone.now(),
            subtotal=Decimal("100.00"),
            taxable_total=Decimal("100.00"),
            igst_total=Decimal("18.00"),
            grand_total=Decimal("118.00"),
        )
        gap.save()
        # Needs a line: an empty posted bill is a genuine (different) finding,
        # and would drown out the one this test is about.
        PurchaseItem.objects.create(
            business=self.user.business,
            purchase=gap,
            item=self.item,
            item_name=self.item.name,
            item_type=self.item.item_type,
            hsn_sac_code="610510",
            unit="PCS",
            quantity=Decimal("1.000"),
            unit_price=Decimal("100.00"),
            tax_rate=Decimal("18.00"),
            taxable_value=Decimal("100.00"),
            igst_amount=Decimal("18.00"),
            total_amount=Decimal("118.00"),
        )
        self.assertIsNotNone(gap.pk)

        code, output = run_audit()
        self.assertEqual(code, 0, output)
        self.assertNotIn("GAP", output)
        self.assertNotIn("DUPLICATE_BILL_NUMBER", output)
        self.assertIn("2 purchase bill", output)


class FilterTests(AuditFixture):
    def test_the_business_filter_narrows_the_run(self):
        self.post_bill()
        code, output = run_audit("--business", self.user.business.pk)
        self.assertEqual(code, 0, output)
        self.assertIn("1 purchase bill", output)

    def test_a_different_business_reports_nothing(self):
        self.post_bill()
        code, output = run_audit("--business", 999999)
        self.assertEqual(code, 0, output)
        self.assertIn("0 purchase bill", output)

    def test_the_fy_filter_narrows_the_run(self):
        self.post_bill()
        code, output = run_audit("--fy", financial_year_of(today()))
        self.assertEqual(code, 0, output)
        self.assertIn("1 purchase bill", output)


def today():
    return timezone.localdate()


def financial_year_of(value) -> str:
    from apps.core.fiscal import financial_year

    return financial_year(value)