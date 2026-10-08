"""Invoice numbering (step D): series, financial years and the prefix snapshot."""

from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import BusinessProfile
from apps.invoices.models import InvoiceCounter
from apps.invoices.services.numbering import (
    MAX_NUMBER_LENGTH,
    NumberingError,
    allocate_invoice_number,
    peek_next_number,
)
from apps.invoices.tests.base import make_user


class NumberSeriesTests(TestCase):
    def setUp(self):
        self.business = make_user().business

    def test_first_number_of_a_business(self):
        self.assertEqual(
            allocate_invoice_number(self.business, date(2026, 4, 1)), "INV/26-27/00001"
        )

    def test_numbers_increment_without_gaps(self):
        numbers = [allocate_invoice_number(self.business, date(2026, 5, 1)) for _ in range(5)]
        self.assertEqual(
            numbers,
            [
                "INV/26-27/00001",
                "INV/26-27/00002",
                "INV/26-27/00003",
                "INV/26-27/00004",
                "INV/26-27/00005",
            ],
        )

    def test_number_never_exceeds_the_column_width(self):
        for _ in range(3):
            allocate_invoice_number(self.business, date(2026, 5, 1))
        number = peek_next_number(self.business, date(2026, 5, 1))
        self.assertEqual(number, "INV/26-27/00004")
        self.assertLessEqual(len(number), MAX_NUMBER_LENGTH)
        self.assertLessEqual(len(number), 16)

    def test_prefix_is_snapshotted_per_financial_year(self):
        """
        Editing the prefix mid-year must not rewrite the series. Otherwise the
        FY reads INV/26-27/00001 ... ABC/26-27/00008, which looks like tampering.
        """
        self.assertEqual(allocate_invoice_number(self.business, date(2026, 4, 1)),
                         "INV/26-27/00001")

        self.business.invoice_number_prefix = "ABC"
        self.business.save(update_fields=["invoice_number_prefix"])

        self.assertEqual(
            allocate_invoice_number(self.business, date(2026, 9, 1)),
            "INV/26-27/00002",
            "the FY counter must keep the prefix it was created with",
        )
        # Only a NEW financial year picks up the new prefix.
        self.assertEqual(
            allocate_invoice_number(self.business, date(2027, 4, 1)), "ABC/27-28/00001"
        )

    def test_counter_row_records_the_snapshotted_prefix(self):
        allocate_invoice_number(self.business, date(2026, 4, 1))
        counter = InvoiceCounter.objects.get(
            business=self.business, financial_year="2026-27"
        )
        self.assertEqual(counter.number_prefix, "INV")
        self.assertEqual(counter.last_number, 1)

    def test_a_four_character_prefix_fills_the_column_exactly(self):
        """
        The real boundary. A 4-char prefix is the column's maximum, and it
        produces exactly 16 characters - the width of `invoice_number`:
        4 + '/' + 5 + '/' + 5 = 16. Any narrower prefix leaves headroom.
        """
        self.business.invoice_number_prefix = "VELO"
        self.business.save(update_fields=["invoice_number_prefix"])
        number = allocate_invoice_number(self.business, date(2026, 4, 1))
        self.assertEqual(number, "VELO/26-27/00001")
        self.assertEqual(len(number), MAX_NUMBER_LENGTH)

    def test_the_prefix_column_rejects_more_than_four_characters(self):
        """
        PostgreSQL enforces varchar(4) strictly; SQLite ignores length limits
        entirely, so a test that saves a long prefix would pass on one database
        and error on the other. Validate through the model field instead, which
        behaves identically on both.
        """
        field = BusinessProfile._meta.get_field("invoice_number_prefix")
        self.assertEqual(field.max_length, 4)

        # Both the format rule and the length rule reject it.
        with self.assertRaises(ValidationError):
            field.clean("TOOLONG", self.business)

        # The boundary itself is accepted.
        field.clean("VELO", self.business)

    def test_a_prefix_too_long_for_the_number_is_rejected(self):
        """
        Defence in depth. Unreachable while `invoice_number_prefix` is
        varchar(4) and the series is 5 digits, but it is the guard that stops a
        future change (wider prefix, fewer series digits, longer FY) from
        silently overflowing the column.
        """
        from apps.invoices.services.numbering import _format_number

        with self.assertRaises(NumberingError) as caught:
            _format_number("TOOLONG", date(2026, 4, 1), 1)
        self.assertEqual(caught.exception.code, "PREFIX_TOO_LONG")
        self.assertEqual(caught.exception.status, 400)

    def test_peek_does_not_consume(self):
        self.assertEqual(peek_next_number(self.business, date(2026, 4, 1)), "INV/26-27/00001")
        self.assertEqual(peek_next_number(self.business, date(2026, 4, 1)), "INV/26-27/00001")
        self.assertEqual(allocate_invoice_number(self.business, date(2026, 4, 1)),
                         "INV/26-27/00001")
        self.assertEqual(peek_next_number(self.business, date(2026, 4, 1)), "INV/26-27/00002")

    def test_peek_reflects_the_counter_prefix_not_the_business_prefix(self):
        allocate_invoice_number(self.business, date(2026, 4, 1))
        self.business.invoice_number_prefix = "ABC"
        self.business.save(update_fields=["invoice_number_prefix"])
        self.assertEqual(peek_next_number(self.business, date(2026, 4, 1)), "INV/26-27/00002")


class FinancialYearTests(TestCase):
    """The FY runs 1 April to 31 March, on IST dates."""

    def setUp(self):
        self.business = make_user().business

    def test_rollover_on_first_april(self):
        self.assertEqual(allocate_invoice_number(self.business, date(2026, 3, 31)),
                         "INV/25-26/00001")
        self.assertEqual(allocate_invoice_number(self.business, date(2026, 4, 1)),
                         "INV/26-27/00001")

    def test_rollover_on_first_january(self):
        self.assertEqual(allocate_invoice_number(self.business, date(2027, 3, 31)),
                         "INV/26-27/00001")
        self.assertEqual(allocate_invoice_number(self.business, date(2027, 4, 1)),
                         "INV/27-28/00001")

    def test_each_financial_year_starts_at_one(self):
        for invoice_date in (date(2025, 6, 1), date(2026, 6, 1), date(2027, 6, 1)):
            self.assertTrue(
                allocate_invoice_number(self.business, invoice_date).endswith("/00001")
            )

    def test_one_counter_row_per_business_per_year(self):
        allocate_invoice_number(self.business, date(2026, 4, 1))
        allocate_invoice_number(self.business, date(2026, 12, 31))
        allocate_invoice_number(self.business, date(2027, 1, 1))
        self.assertEqual(InvoiceCounter.objects.filter(business=self.business).count(), 1)
        self.assertEqual(
            InvoiceCounter.objects.get(business=self.business).last_number, 3
        )

    def test_a_new_financial_year_gets_its_own_counter_row(self):
        allocate_invoice_number(self.business, date(2026, 4, 1))
        allocate_invoice_number(self.business, date(2027, 4, 1))
        self.assertEqual(InvoiceCounter.objects.filter(business=self.business).count(), 2)


class PerBusinessIndependenceTests(TestCase):
    def test_two_businesses_number_independently(self):
        first = make_user("first@example.com", prefix="AAA").business
        second = make_user("second@example.com", prefix="BBB").business

        self.assertEqual(allocate_invoice_number(first, date(2026, 4, 1)), "AAA/26-27/00001")
        self.assertEqual(allocate_invoice_number(second, date(2026, 4, 1)), "BBB/26-27/00001")
        self.assertEqual(allocate_invoice_number(first, date(2026, 4, 2)), "AAA/26-27/00002")
        self.assertEqual(allocate_invoice_number(second, date(2026, 4, 2)), "BBB/26-27/00002")

    def test_one_businesss_issue_does_not_advance_anothers_counter(self):
        first = make_user("first@example.com", prefix="AAA").business
        second = make_user("second@example.com", prefix="BBB").business
        for _ in range(5):
            allocate_invoice_number(first, date(2026, 4, 1))
        self.assertEqual(peek_next_number(second, date(2026, 4, 1)), "BBB/26-27/00001")
