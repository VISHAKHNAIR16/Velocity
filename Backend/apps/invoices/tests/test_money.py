"""Step B tests for apps/core/money.py - pure, no database.

`SimpleTestCase` is used deliberately: it forbids database access, so these
tests prove the money helpers are pure and cannot quietly start hitting the DB.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from apps.core.money import (
    ROUND_OFF_CAVEAT,
    amount_in_words,
    apportion_pro_rata,
    integer_to_words,
    q2,
    q2_down,
    q3,
    split_amount_intra,
)

D = Decimal


class QuantiseTests(SimpleTestCase):
    def test_q2_rounds_half_up(self):
        # These are exactly the values where JS toFixed(2) disagrees.
        self.assertEqual(q2("1.005"), D("1.01"))
        self.assertEqual(q2("2.675"), D("2.68"))
        self.assertEqual(q2("0.145"), D("0.15"))
        self.assertEqual(q2("8.835"), D("8.84"))

    def test_q2_keeps_two_places(self):
        self.assertEqual(q2("10"), D("10.00"))
        self.assertEqual(q2("10.5"), D("10.50"))
        self.assertEqual(q2("10.567"), D("10.57"))

    def test_q2_down_never_rounds_up(self):
        self.assertEqual(q2_down("1.009"), D("1.00"))
        self.assertEqual(q2_down("1.005"), D("1.00"))
        self.assertEqual(q2_down("1.0"), D("1.00"))

    def test_q3_keeps_three_places_for_stock(self):
        self.assertEqual(q3("1.5"), D("1.500"))
        self.assertEqual(q3("10.5005"), D("10.501"))
        self.assertEqual(q3("0.0004"), D("0.000"))

    def test_accepts_decimal_int_and_str(self):
        self.assertEqual(q2(D("2.675")), D("2.68"))
        self.assertEqual(q2(3), D("3.00"))
        self.assertEqual(q2("2.675"), D("2.68"))

    def test_refuses_a_float(self):
        """A float has already lost precision before we see it."""
        with self.assertRaises(TypeError):
            q2(2.675)


class SplitAmountIntraTests(SimpleTestCase):
    """The split must always re-add to the tax, and must never over-charge."""

    def test_halves_always_re_add(self):
        for rate in ["0", "0.25", "1.5", "3", "5", "12", "18", "28", "40"]:
            tax = q2(D("1234.56") * D(rate) / D("100"))
            cgst, sgst = split_amount_intra(tax)
            self.assertEqual(cgst + sgst, tax, f"rate {rate}% tax {tax}")

    def test_odd_rates_that_cannot_be_halved(self):
        # 0.25% of 100 = 0.25 tax; half is 0.125 which is not 2dp.
        self.assertEqual(split_amount_intra(D("0.25")), (D("0.12"), D("0.13")))
        self.assertEqual(split_amount_intra(D("1.50")), (D("0.75"), D("0.75")))

    def test_documented_paisa_case(self):
        """10.55 @ 5% -> tax 0.53. Rate-splitting would give 0.52."""
        tax = q2(D("10.55") * D("5") / D("100"))
        self.assertEqual(tax, D("0.53"))
        self.assertEqual(split_amount_intra(tax), (D("0.26"), D("0.27")))

    def test_sgst_takes_the_remainder_so_we_never_overcharge(self):
        for tax in ["0.01", "0.03", "0.25", "0.53", "6.01", "17.91", "94.75"]:
            cgst, sgst = split_amount_intra(D(tax))
            self.assertLessEqual(cgst, sgst)
            self.assertEqual(cgst + sgst, D(tax))

    def test_zero(self):
        self.assertEqual(split_amount_intra(D("0.00")), (D("0.00"), D("0.00")))


class ApportionProRataTests(SimpleTestCase):
    def test_shares_always_sum_exactly_to_the_total(self):
        cases = [
            ("150.00", [1000, 500]),
            ("100.00", [1, 1, 1]),
            ("10.00", [1] * 7),
            ("0.07", [3, 3, 1]),
            ("99.99", [7, 11, 13]),
            ("0.01", [1, 1, 1, 1]),
            ("12345.67", [3, 5, 7, 11]),
            ("5.00", [5]),
        ]
        for total, weights in cases:
            with self.subTest(total=total, weights=weights):
                parts = apportion_pro_rata(total, weights)
                self.assertEqual(len(parts), len(weights))
                self.assertEqual(sum(parts, D("0.00")), D(total))

    def test_documented_examples(self):
        self.assertEqual(apportion_pro_rata("150.00", [1000, 500]), [D("100.00"), D("50.00")])
        self.assertEqual(
            apportion_pro_rata("100.00", [1, 1, 1]), [D("33.34"), D("33.33"), D("33.33")]
        )

    def test_all_zero_weights_still_sum_exactly(self):
        parts = apportion_pro_rata("10.00", [0, 0, 0])
        self.assertEqual(sum(parts, D("0.00")), D("10.00"))
        self.assertEqual(parts, [D("3.34"), D("3.33"), D("3.33")])

    def test_is_deterministic(self):
        """Same input must always give the same split (ties break by position)."""
        first = apportion_pro_rata("100.00", [1, 1, 1])
        for _ in range(5):
            self.assertEqual(apportion_pro_rata("100.00", [1, 1, 1]), first)

    def test_zero_total_and_empty_weights(self):
        self.assertEqual(apportion_pro_rata("0.00", [1, 2]), [D("0.00"), D("0.00")])
        self.assertEqual(apportion_pro_rata("10.00", []), [])

    def test_is_proportional_where_it_can_be(self):
        parts = apportion_pro_rata("1000.00", [1, 3])
        self.assertEqual(parts, [D("250.00"), D("750.00")])


class AmountInWordsTests(SimpleTestCase):
    def test_documented_example(self):
        self.assertEqual(
            amount_in_words("123456.78"),
            "Rupees One Lakh Twenty Three Thousand Four Hundred Fifty Six "
            "and Seventy Eight Paise Only",
        )

    def test_zero_and_paise_only(self):
        self.assertEqual(amount_in_words("0"), "Rupees Zero Only")
        self.assertEqual(amount_in_words("0.05"), "Rupees Zero and Five Paise Only")
        self.assertEqual(amount_in_words("0.99"), "Rupees Zero and Ninety Nine Paise Only")

    def test_whole_amounts_omit_the_paise_clause(self):
        self.assertEqual(amount_in_words("100.00"), "Rupees One Hundred Only")
        self.assertEqual(amount_in_words("1.00"), "Rupees One Only")

    def test_lakh_and_crore_grouping(self):
        self.assertEqual(amount_in_words("100000.00"), "Rupees One Lakh Only")
        self.assertEqual(amount_in_words("10000000.00"), "Rupees One Crore Only")
        self.assertEqual(
            amount_in_words("12345678.50"),
            "Rupees One Crore Twenty Three Lakh Forty Five Thousand Six Hundred "
            "Seventy Eight and Fifty Paise Only",
        )

    def test_max_two_dp_field_value(self):
        words = amount_in_words("99999999.99")
        self.assertIn("Nine Crore", words)
        self.assertIn("Ninety Nine Paise Only", words)

    def test_negative(self):
        self.assertTrue(amount_in_words("-100.00").startswith("Minus Rupees"))

    def test_teens_and_tens_spelling(self):
        self.assertEqual(integer_to_words(19), "Nineteen")
        self.assertEqual(integer_to_words(20), "Twenty")
        self.assertEqual(integer_to_words(21), "Twenty One")
        self.assertEqual(integer_to_words(99), "Ninety Nine")
        self.assertEqual(integer_to_words(100), "One Hundred")
        self.assertEqual(integer_to_words(101), "One Hundred One")
        self.assertEqual(integer_to_words(0), "Zero")

    def test_hundreds_are_not_repeated_with_zeros(self):
        self.assertEqual(integer_to_words(200), "Two Hundred")
        self.assertEqual(integer_to_words(105), "One Hundred Five")


class RoundOffCaveatTests(SimpleTestCase):
    def test_caveat_warns_about_gstr1(self):
        self.assertIn("GSTR-1", ROUND_OFF_CAVEAT)
        self.assertIn("never include the round-off as taxable value", ROUND_OFF_CAVEAT)
        self.assertIn("audited", ROUND_OFF_CAVEAT)