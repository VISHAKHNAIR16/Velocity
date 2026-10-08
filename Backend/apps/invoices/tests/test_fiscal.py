"""Step B tests for apps/core/fiscal.py - Indian financial year, no database.

The 31 March / 1 April boundary is the whole point of this module: get it wrong
and a 1 April invoice joins the previous year's number series.
"""

from datetime import date, datetime

from django.test import SimpleTestCase

from apps.core.fiscal import (
    financial_year,
    financial_year_short,
    financial_year_start,
    fy_bounds,
    is_in_same_financial_year,
)


class FinancialYearTests(SimpleTestCase):
    def test_the_critical_boundary(self):
        """31 March is the OLD year; 1 April is the NEW one."""
        self.assertEqual(financial_year(date(2026, 3, 31)), "2025-26")
        self.assertEqual(financial_year(date(2026, 4, 1)), "2026-27")

    def test_every_month_of_a_year(self):
        expected = {
            1: "2025-26", 2: "2025-26", 3: "2025-26",
            4: "2026-27", 5: "2026-27", 6: "2026-27", 7: "2026-27",
            8: "2026-27", 9: "2026-27", 10: "2026-27", 11: "2026-27", 12: "2026-27",
        }
        for month, label in expected.items():
            with self.subTest(month=month):
                self.assertEqual(financial_year(date(2026, month, 15)), label)

    def test_first_and_last_day_of_the_year(self):
        self.assertEqual(financial_year(date(2026, 4, 1)), "2026-27")
        self.assertEqual(financial_year(date(2027, 3, 31)), "2026-27")

    def test_leap_day(self):
        """29 Feb 2028 falls in FY 2027-28."""
        self.assertEqual(financial_year(date(2028, 2, 29)), "2027-28")
        self.assertEqual(financial_year(date(2028, 3, 31)), "2027-28")

    def test_short_label_used_in_invoice_numbers(self):
        self.assertEqual(financial_year_short(date(2026, 4, 1)), "26-27")
        self.assertEqual(financial_year_short(date(2027, 3, 31)), "26-27")

    def test_fy_start_date(self):
        self.assertEqual(financial_year_start(date(2026, 3, 31)), date(2025, 4, 1))
        self.assertEqual(financial_year_start(date(2026, 4, 1)), date(2026, 4, 1))

    def test_accepts_a_datetime(self):
        """An invoice created at 00:30 IST on 1 April must land in the NEW year."""
        self.assertEqual(financial_year(datetime(2026, 4, 1, 0, 30)), "2026-27")
        self.assertEqual(financial_year(datetime(2026, 3, 31, 23, 59)), "2025-26")

    def test_rejects_a_non_date(self):
        with self.assertRaises(TypeError):
            financial_year("2026-04-01")


class FyBoundsTests(SimpleTestCase):
    def test_bounds(self):
        self.assertEqual(
            fy_bounds("2026-27"), (date(2026, 4, 1), date(2027, 3, 31))
        )

    def test_accepts_the_short_form(self):
        self.assertEqual(fy_bounds("26-27"), (date(2026, 4, 1), date(2027, 3, 31)))

    def test_round_trips_with_financial_year(self):
        for moment in [date(2026, 4, 1), date(2026, 8, 15), date(2027, 3, 31)]:
            with self.subTest(moment=moment):
                label = financial_year(moment)
                start, end = fy_bounds(label)
                self.assertLessEqual(start, moment)
                self.assertLessEqual(moment, end)

    def test_rejects_non_consecutive_years(self):
        with self.assertRaises(ValueError):
            fy_bounds("2026-28")

    def test_rejects_malformed_labels(self):
        for bad in ["", "2026", "202627", "abcd-efgh"]:
            with self.subTest(label=bad):
                with self.assertRaises(ValueError):
                    fy_bounds(bad)


class SameFinancialYearTests(SimpleTestCase):
    def test_same_year(self):
        self.assertTrue(
            is_in_same_financial_year(date(2026, 4, 1), date(2027, 3, 31))
        )

    def test_different_years(self):
        self.assertFalse(
            is_in_same_financial_year(date(2026, 3, 31), date(2026, 4, 1))
        )