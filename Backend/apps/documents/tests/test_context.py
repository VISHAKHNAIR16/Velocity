"""
The snapshot-only guarantee (2.3).

The rule under test: **the PDF is built from the invoice alone.** If a customer
is renamed, an item's rate changes, or the business fixes a mistyped bank
account, no document that has already been issued may change as a result.

That rule is easy to state and easy to break the first time somebody adds a
"helpful" live lookup to a template. So it is tested by *doing the mutation and
re-rendering*, not by reading the source and asserting there is no ORM call in
it.
"""

from django.template.loader import render_to_string

from apps.documents.services.context import build_invoice_context
from apps.documents.services.renderer import TEMPLATES
from apps.documents.tests.base import DocumentBase


class SnapshotOnlyTests(DocumentBase):
    def setUp(self):
        super().setUp()
        self.add_bank_details()
        self.invoice = self.issued_invoice()

    def _render(self, key="classic") -> str:
        return render_to_string(TEMPLATES[key].filename, build_invoice_context(self.invoice))

    def test_a_party_rename_cannot_reach_the_pdf(self):
        before = self._render()

        self.party.name = "Completely Different Buyer Ltd"
        self.party.save(update_fields=["name"])

        self.invoice.refresh_from_db()
        self.assertEqual(before, self._render(), "a live party edit leaked into the PDF")

    def test_an_item_rate_change_cannot_reach_the_pdf(self):
        before = self._render()

        self.item.tax_rate = "28.00"
        self.item.purchase_price = "999.00"
        self.item.save(update_fields=["tax_rate", "purchase_price"])

        self.invoice.refresh_from_db()
        self.assertEqual(before, self._render(), "a live item edit leaked into the PDF")

    def test_a_bank_correction_cannot_reach_an_already_issued_pdf(self):
        """
        The case the snapshot exists for.

        Correcting a mistyped account number is normal and necessary. It must
        not reach back and rewrite the copy a customer already holds.
        """
        profile = self.user.business
        self.assertTrue(
            self.invoice.bank_account_number, "precondition: the letterhead was snapshotted"
        )
        before = self._render()

        profile.bank_account_number = "99999999999999"
        profile.save(update_fields=["bank_account_number"])

        self.invoice.refresh_from_db()
        after = self._render()
        self.assertEqual(before, after, "the corrected bank number rewrote a historical invoice")
        self.assertIn("50100234567890", before)
        self.assertNotIn("99999999999999", after)

    def test_the_pdf_reads_the_stored_totals_not_the_calculator(self):
        """
        The invoice's own figures win, always.

        The `*_computed` mirror columns exist for auditing. A tax invoice prints
        what was issued; if the calculator would disagree today, because a rate
        changed after the fact, the issued figure is still the correct one.

        Asserted on the context rather than the rendered HTML, because matching
        a formatted Decimal inside markup is brittle in a way that makes the
        test fail for the wrong reason.
        """
        ctx = build_invoice_context(self.invoice)
        self.assertEqual(ctx["totals"]["grand_total"], self.invoice.grand_total)
        self.assertEqual(ctx["totals"]["taxable_total"], self.invoice.taxable_total)
        self.assertEqual(
            ctx["totals"]["tax_breakup"]["amount"],
            self.invoice.igst_total if ctx["supply"]["tax_label"] == "IGST" else self.invoice.sgst_total,
        )
        # No computed value is ever exposed to a template.
        self.assertNotIn("computed", {k for k in ctx["totals"]})


class ContextShapeTests(DocumentBase):
    """The context is the whole contract between the model and the templates."""

    def setUp(self):
        super().setUp()
        self.add_bank_details()
        self.invoice = self.issued_invoice()
        self.ctx = build_invoice_context(self.invoice)

    def test_it_carries_the_amount_in_words(self):
        self.assertTrue(self.ctx["totals"]["in_words"].startswith("Rupees "))

    def test_it_labels_the_tax_column_from_the_snapshotted_place_of_supply(self):
        self.assertIn(self.ctx["supply"]["tax_label"], {"SGST", "UTGST", "IGST"})

    def test_it_names_the_place_of_supply(self):
        self.assertTrue(self.ctx["supply"]["place_of_supply_name"])

    def test_it_prints_the_reverse_charge_constant(self):
        self.assertEqual(self.ctx["meta"]["reverse_charge_line"], "Tax payable on reverse charge: No")

    def test_bank_can_be_absent_without_breaking_the_context(self):
        self.invoice.bank_account_number = ""
        self.invoice.bank_ifsc = ""
        ctx = build_invoice_context(self.invoice)
        self.assertFalse(ctx["bank"]["has_any"])

    def test_a_missing_signature_is_flagged_rather_than_guessed(self):
        self.assertFalse(self.ctx["supplier"]["has_signature"])
        self.invoice.business_signature_url = "https://example.test/sig.png"
        self.assertTrue(build_invoice_context(self.invoice)["supplier"]["has_signature"])