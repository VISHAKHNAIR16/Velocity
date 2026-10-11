"""
Tests for `apps.purchases/services/divergence.py`.

`SimpleTestCase`, not `TestCase`: this module is pure, so it needs no database
and runs in milliseconds. That is the same reason `stock_plan.py` can be
exhaustively tested - and the reason the purchased bill's divergence maths is
trustworthy without standing up a test database to check it.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from apps.purchases.services.divergence import (
    TOLERANCE,
    FieldDivergence,
    build_report,
    compare_header,
    compare_lines,
)


class FieldDivergenceTests(SimpleTestCase):
    def test_difference_is_entered_minus_computed(self):
        d = FieldDivergence("grand_total", Decimal("100.50"), Decimal("100.00"))
        self.assertEqual(d.difference, Decimal("0.50"))

    def test_a_signed_difference_keeps_its_sign(self):
        under = FieldDivergence("cgst_total", Decimal("9.00"), Decimal("9.45"))
        self.assertEqual(under.difference, Decimal("-0.45"))
        over = FieldDivergence("cgst_total", Decimal("9.45"), Decimal("9.00"))
        self.assertEqual(over.difference, Decimal("0.45"))

    def test_rounding_noise_is_not_material(self):
        # The whole reason the tolerance exists: a supplier's round-off of a
        # paisa is not something a shopkeeper should be shown a warning about.
        for amount in ("0.00", "0.01", "0.50", "1.00"):
            d = FieldDivergence("grand_total", Decimal(amount), Decimal("0.00"))
            self.assertFalse(
                d.is_material,
                f"Rs {amount} should not be material (tolerance is {TOLERANCE})",
            )

    def test_more_than_a_rupee_is_material(self):
        self.assertTrue(
            FieldDivergence("grand_total", Decimal("1.01"), Decimal("0.00")).is_material
        )


class CompareHeaderTests(SimpleTestCase):
    def test_identical_figures_produce_nothing(self):
        entered = {
            "taxable_total": Decimal("100.00"),
            "cgst_total": Decimal("9.00"),
            "sgst_total": Decimal("9.00"),
            "igst_total": Decimal("0.00"),
            "grand_total": Decimal("118.00"),
        }
        self.assertEqual(compare_header(entered, dict(entered)), [])

    def test_a_single_wrong_figure_is_caught(self):
        entered = dict(
            taxable_total=Decimal("100.00"),
            cgst_total=Decimal("9.00"),
            sgst_total=Decimal("9.00"),
            igst_total=Decimal("0.00"),
            grand_total=Decimal("118.00"),
        )
        computed = dict(entered, grand_total=Decimal("119.00"))
        found = compare_header(entered, computed)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].field, "grand_total")
        self.assertEqual(found[0].difference, Decimal("-1.00"))

    def test_it_works_on_objects_as_well_as_dicts(self):
        class Row:
            taxable_total = Decimal("10.00")
            cgst_total = Decimal("1.00")
            sgst_total = Decimal("1.00")
            igst_total = Decimal("0.00")
            grand_total = Decimal("12.00")

        self.assertEqual(compare_header(Row(), Row()), [])
        computed = Row()
        computed.grand_total = Decimal("13.00")
        self.assertEqual(len(compare_header(Row(), computed)), 1)


class CompareLinesTests(SimpleTestCase):
    def _line(self, **overrides):
        base = {
            "taxable_value": Decimal("100.00"),
            "cgst_amount": Decimal("9.00"),
            "sgst_amount": Decimal("9.00"),
            "igst_amount": Decimal("0.00"),
            "total_amount": Decimal("118.00"),
        }
        base.update(overrides)
        return base

    def test_matching_lines_produce_nothing(self):
        line = self._line()
        self.assertEqual(compare_lines([line], [dict(line)]), [])

    def test_one_bad_line_is_reported_with_its_index(self):
        bad = self._line(total_amount=Decimal("120.00"))
        found = compare_lines([self._line(), bad], [self._line(), self._line()])
        self.assertEqual(len(found), 1)
        index, group = found[0]
        self.assertEqual(index, 1, "the wrong line must be identified by position")
        self.assertEqual(group[0].field, "total_amount")

    def test_a_line_count_mismatch_is_reported_not_padded(self):
        """
        Comparing fewer lines than exist would hide exactly the problem this
        module exists to find, so a length mismatch is itself a finding.
        """
        found = compare_lines([self._line()], [self._line(), self._line()])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1][0].field, "line_count")

    def test_empty_lines_are_fine_when_both_sides_are_empty(self):
        self.assertEqual(compare_lines([], []), [])


class ReportTests(SimpleTestCase):
    """
    `build_report(entered_bill, computed_totals)`.

    The two sides are deliberately different shapes, because that is what the
    service really has: a saved `Purchase` (header fields plus `.items`) on one
    side, an `InvoiceTotals` (same header fields plus `.lines`) on the other.
    Writing the test with one shape for both would pass while hiding a genuine
    mismatch - which is exactly what it did the first time.
    """

    @staticmethod
    def _entered(grand_total, line_total):
        class Line:
            taxable_value = Decimal("100.00")
            cgst_amount = Decimal("9.00")
            sgst_amount = Decimal("9.00")
            igst_amount = Decimal("0.00")

            def __init__(self, total):
                self.total_amount = total

        class Bill:
            taxable_total = Decimal("100.00")
            cgst_total = Decimal("9.00")
            sgst_total = Decimal("9.00")
            igst_total = Decimal("0.00")

            def __init__(self, grand):
                self.grand_total = grand
                self.items = [Line(line_total)]

        return Bill(grand_total)

    @staticmethod
    def _computed(line_total=Decimal("118.00")):
        class Line:
            taxable_value = Decimal("100.00")
            cgst_amount = Decimal("9.00")
            sgst_amount = Decimal("9.00")
            igst_amount = Decimal("0.00")
            total_amount = line_total

        class Totals:
            taxable_total = Decimal("100.00")
            cgst_total = Decimal("9.00")
            sgst_total = Decimal("9.00")
            igst_total = Decimal("0.00")
            grand_total = Decimal("118.00")
            lines = [Line()]

        return Totals()

    def test_a_correct_bill_reports_no_divergence(self):
        report = build_report(self._entered(Decimal("118.00"), Decimal("118.00")), self._computed())
        self.assertFalse(report.has_divergence)
        self.assertEqual(report.max_difference, Decimal("0.00"))
        self.assertIn("agree", report.summary())

    def test_a_material_header_difference_is_surfaced(self):
        entered = self._entered(Decimal("130.00"), Decimal("118.00"))
        report = build_report(entered, self._computed())
        self.assertTrue(report.has_divergence)
        self.assertEqual(report.max_difference, Decimal("12.00"))
        self.assertIn("differ", report.summary())

    def test_rounding_only_does_not_raise_an_alarm(self):
        entered = self._entered(Decimal("118.40"), Decimal("118.00"))
        report = build_report(entered, self._computed())
        self.assertFalse(
            report.has_divergence,
            "a few paise of supplier rounding must not be reported",
        )
        # ...but the gap is still recorded, so a pattern stays visible.
        self.assertEqual(report.max_difference, Decimal("0.40"))

    def test_max_difference_covers_lines_as_well_as_the_header(self):
        entered = self._entered(Decimal("118.00"), Decimal("125.00"))
        report = build_report(entered, self._computed())
        self.assertEqual(report.max_difference, Decimal("7.00"))
        self.assertTrue(report.has_divergence)
        self.assertEqual(report.lines[0][0], 0, "the offending line is identified")

    def test_has_divergence_is_false_below_tolerance_even_with_a_difference(self):
        """The flag is about the threshold, not about equality."""
        entered = self._entered(Decimal("118.30"), Decimal("118.00"))
        report = build_report(entered, self._computed())
        self.assertFalse(report.has_divergence)
        self.assertTrue(report.all_divergences, "the difference is still recorded")