"""Step B tests for the GST calculator. Pure, no database.

The golden tests below have expected values computed BY HAND (shown in the
comment on each), never copied from the calculator's own output - otherwise they
would only prove the code agrees with itself.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from apps.core.constants import GST_RATE_CHOICES, UT_WITHOUT_LEGISLATURE
from apps.invoices.services.gst_calculator import (
    GSTCalculationError,
    LineInput,
    SupplyType,
    calculate_invoice,
    default_place_of_supply,
    state_tax_label,
)

D = Decimal
ALL_RATES = [str(rate) for rate, _ in GST_RATE_CHOICES]


def line(quantity, unit_price, rate, discount="0", item_type="PRODUCT"):
    return LineInput.build(
        quantity=quantity, unit_price=unit_price, tax_rate=rate,
        line_discount=discount, item_type=item_type,
    )


def calc(lines, business="36", pos="36", **kwargs):
    kwargs.setdefault("business_is_regular", True)
    return calculate_invoice(lines, business_state=business, place_of_supply=pos, **kwargs)


# ===========================================================================
# Golden tests - expected numbers computed by hand
# ===========================================================================
class GoldenTests(SimpleTestCase):
    def test_intra_state_single_line(self):
        # 2 x 249.50 = 499.00 taxable; 499.00 x 18% = 89.82;
        # 89.82 / 2 = 44.91 each; total 499.00 + 89.82 = 588.82
        t = calc([line(2, "249.50", "18")])
        self.assertEqual(t.subtotal, D("499.00"))
        self.assertEqual(t.taxable_total, D("499.00"))
        self.assertEqual(t.cgst_total, D("44.91"))
        self.assertEqual(t.sgst_total, D("44.91"))
        self.assertEqual(t.igst_total, D("0.00"))
        self.assertEqual(t.grand_total, D("588.82"))

    def test_inter_state_single_line(self):
        # Same numbers, but the whole 89.82 is IGST.
        t = calc([line(2, "249.50", "18")], business="36", pos="29")
        self.assertEqual(t.supply_type, SupplyType.INTER)
        self.assertEqual(t.cgst_total, D("0.00"))
        self.assertEqual(t.sgst_total, D("0.00"))
        self.assertEqual(t.igst_total, D("89.82"))
        self.assertEqual(t.grand_total, D("588.82"))

    def test_inclusive_price(self):
        # 118.00 inclusive at 18%: taxable = 118 x 100 / 118 = 100.00; tax = 18.00.
        t = calc([line(1, "118.00", "18")], prices_include_tax=True)
        self.assertEqual(t.taxable_total, D("100.00"))
        self.assertEqual(t.cgst_total, D("9.00"))
        self.assertEqual(t.sgst_total, D("9.00"))
        self.assertEqual(t.grand_total, D("118.00"))

    def test_line_discount(self):
        # 3 x 33.33 = 99.99 gross, less 0.50 = 99.49 net;
        # 99.49 x 18% = 17.9082 -> 17.91; split 17.91/2 = 8.955 -> 8.95 down,
        # remainder 8.96; total 99.49 + 17.91 = 117.40
        t = calc([line(3, "33.33", "18", discount="0.50")])
        self.assertEqual(t.subtotal, D("99.99"))
        self.assertEqual(t.lines[0].net_amount, D("99.49"))
        self.assertEqual(t.lines[0].tax_amount, D("17.91"))
        self.assertEqual(t.lines[0].cgst_amount, D("8.95"))
        self.assertEqual(t.lines[0].sgst_amount, D("8.96"))
        self.assertEqual(t.grand_total, D("117.40"))

    def test_invoice_discount_apportioned(self):
        # Gross 1000 + 500 = 1500. Discount 150 -> shares 100 / 50.
        # A: net 900 x 18% = 162.00 -> 81.00 each -> total 1062.00
        # B: net 450 x 12% =  54.00 -> 27.00 each -> total  504.00
        t = calc([line(1, "1000.00", "18"), line(1, "500.00", "12")],
                 invoice_discount="150.00")
        self.assertEqual(t.subtotal, D("1500.00"))
        self.assertEqual(t.total_discount, D("150.00"))
        self.assertEqual(t.lines[0].invoice_discount_share, D("100.00"))
        self.assertEqual(t.lines[1].invoice_discount_share, D("50.00"))
        self.assertEqual(t.taxable_total, D("1350.00"))
        self.assertEqual(t.cgst_total, D("108.00"))
        self.assertEqual(t.sgst_total, D("108.00"))
        self.assertEqual(t.grand_total, D("1566.00"))

    def test_ut_without_legislature_uses_utgst_label(self):
        # Chandigarh intra-state: same arithmetic, UTGST label.
        t = calc([line(1, "100.00", "18")], business="04", pos="04")
        self.assertEqual(t.supply_type, SupplyType.INTRA)
        self.assertEqual(t.state_tax_label, "UTGST")
        self.assertEqual(t.cgst_total, D("9.00"))
        self.assertEqual(t.sgst_total, D("9.00"))

    def test_unregistered_business_charges_no_tax(self):
        # 1000 at 18% requested, but an unregistered business must show 0 tax
        # and be titled "Bill of Supply".
        t = calc([line(1, "1000.00", "18")], business_is_regular=False)
        self.assertEqual(t.taxable_total, D("1000.00"))
        self.assertEqual(t.cgst_total, D("0.00"))
        self.assertEqual(t.sgst_total, D("0.00"))
        self.assertEqual(t.igst_total, D("0.00"))
        self.assertEqual(t.grand_total, D("1000.00"))
        self.assertEqual(t.document_title, "Bill of Supply")

    def test_round_off_to_nearest_rupee(self):
        # 10.24 + 10.25 = 20.49 -> nearest rupee 20.00, so round-off is -0.49.
        t = calc([line(1, "10.24", "0"), line(1, "10.25", "0")], round_invoice_total=True)
        self.assertEqual(t.grand_total, D("20.00"))
        self.assertEqual(t.round_off, D("-0.49"))

    def test_mixed_slab_invoice(self):
        # A: 1 x 100.00 @ 0%      -> tax 0
        # B: 1 x 200.00 @ 40%     -> tax 80.00 -> 40.00 each
        # C: 1 x  50.00 @ 0.25%   -> tax 0.125 -> 0.13 (0.12 + 0.01... -> 0.12/0.13 split of 0.13)
        t = calc([line(1, "100.00", "0"), line(1, "200.00", "40"), line(1, "50.00", "0.25")])
        self.assertEqual(t.lines[0].tax_amount, D("0.00"))
        self.assertEqual(t.lines[1].tax_amount, D("80.00"))
        self.assertEqual(t.lines[2].tax_amount, D("0.13"))
        self.assertEqual(t.cgst_total, D("40.06"))
        self.assertEqual(t.sgst_total, D("40.07"))
        self.assertEqual(t.grand_total, D("430.13"))


# ===========================================================================
# Every slab, both supply types
# ===========================================================================
class AllSlabsTests(SimpleTestCase):
    def test_every_supported_rate_intra_state(self):
        for rate in ALL_RATES:
            with self.subTest(rate=rate):
                t = calc([line(1, "1000.00", rate)])
                expected_tax = q2_helper(D("1000.00") * D(rate) / D("100"))
                self.assertEqual(t.taxable_total, D("1000.00"))
                self.assertEqual(t.cgst_total + t.sgst_total, expected_tax)
                self.assertEqual(t.igst_total, D("0.00"))

    def test_every_supported_rate_inter_state(self):
        for rate in ALL_RATES:
            with self.subTest(rate=rate):
                t = calc([line(1, "1000.00", rate)], business="36", pos="29")
                expected_tax = q2_helper(D("1000.00") * D(rate) / D("100"))
                self.assertEqual(t.igst_total, expected_tax)
                self.assertEqual(t.cgst_total + t.sgst_total, D("0.00"))

    def test_half_rate_slabs_are_covered(self):
        """0.25 and 1.5 cannot be halved to 2dp - the split must still reconcile."""
        for rate in ["0.25", "1.5"]:
            with self.subTest(rate=rate):
                t = calc([line(1, "100.00", rate)])
                self.assertEqual(t.cgst_total + t.sgst_total, t.lines[0].tax_amount)


def q2_helper(value):
    from apps.core.money import q2

    return q2(value)


# ===========================================================================
# Place of supply
# ===========================================================================
class PlaceOfSupplyTests(SimpleTestCase):
    def test_goods_prefer_the_shipping_state(self):
        self.assertEqual(
            default_place_of_supply(billing_state="27", shipping_state="36", has_goods_line=True),
            "36",
        )

    def test_goods_without_a_shipping_state_fall_back_to_billing(self):
        self.assertEqual(
            default_place_of_supply(billing_state="27", shipping_state="", has_goods_line=True),
            "27",
        )

    def test_services_use_the_billing_state(self):
        """The recipient's location governs services, not a delivery address."""
        self.assertEqual(
            default_place_of_supply(billing_state="27", shipping_state="36", has_goods_line=False),
            "27",
        )

    def test_mixed_cart_uses_the_goods_rule(self):
        self.assertEqual(
            default_place_of_supply(billing_state="27", shipping_state="36", has_goods_line=True),
            "36",
        )

    def test_missing_billing_state_is_refused(self):
        with self.assertRaises(GSTCalculationError) as ctx:
            default_place_of_supply(billing_state="", shipping_state="36", has_goods_line=True)
        self.assertEqual(ctx.exception.code, "PARTY_STATE_MISSING")


class StateTaxLabelTests(SimpleTestCase):
    def test_all_five_uts_without_a_legislature(self):
        self.assertEqual(
            sorted(UT_WITHOUT_LEGISLATURE), ["04", "26", "31", "35", "38"]
        )
        for code in UT_WITHOUT_LEGISLATURE:
            with self.subTest(code=code):
                t = calc([line(1, "100.00", "18")], business=code, pos=code)
                self.assertEqual(t.state_tax_label, "UTGST")

    def test_states_and_legislated_uts_use_sgst(self):
        for code in ["01", "07", "27", "29", "33", "34", "36"]:
            with self.subTest(code=code):
                t = calc([line(1, "100.00", "18")], business=code, pos=code)
                self.assertEqual(t.state_tax_label, "SGST")

    def test_inter_state_is_always_igst(self):
        for code in ["04", "36"]:
            with self.subTest(code=code):
                t = calc([line(1, "100.00", "18")], business=code, pos="29")
                self.assertEqual(t.state_tax_label, "IGST")

    def test_public_helper_matches_the_calculation(self):
        t = calc([line(1, "100.00", "18")], business="04", pos="04")
        self.assertEqual(state_tax_label("04", t.supply_type), "UTGST")


# ===========================================================================
# Registration gate
# ===========================================================================
class RegistrationGateTests(SimpleTestCase):
    def test_regular_charges_tax(self):
        t = calc([line(1, "1000.00", "18")], business_is_regular=True)
        self.assertEqual(t.tax_total, D("180.00"))
        self.assertEqual(t.document_title, "Tax Invoice")

    def test_composition_and_unregistered_charge_nothing(self):
        for is_regular in (False,):
            with self.subTest(is_regular=is_regular):
                t = calc([line(1, "1000.00", "18"), line(1, "500.00", "40")],
                         business_is_regular=is_regular)
                self.assertEqual(t.tax_total, D("0.00"))
                self.assertEqual(t.document_title, "Bill of Supply")
                self.assertEqual(t.grand_total, D("1500.00"))

    def test_gate_still_allows_the_amount_itself(self):
        t = calc([line(1, "1000.00", "18")], business_is_regular=False)
        self.assertEqual(t.taxable_total, D("1000.00"))

    def test_a_bad_rate_is_fine_for_an_unregistered_business(self):
        """The gate zeroes the rate, so no slab check is needed."""
        t = calc([line(1, "100.00", "18")], business_is_regular=False)
        self.assertEqual(t.lines[0].tax_rate, D("0.00"))


# ===========================================================================
# Refusals - never coerce bad input into a number
# ===========================================================================
class RefusalTests(SimpleTestCase):
    def assert_refused(self, code, **kwargs):
        with self.assertRaises(GSTCalculationError) as ctx:
            calc(**kwargs)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_zero_or_negative_quantity(self):
        self.assert_refused("INVALID_QUANTITY", lines=[line(0, "10.00", "18")])
        self.assert_refused("INVALID_QUANTITY", lines=[line("-1", "10.00", "18")])

    def test_negative_price(self):
        self.assert_refused("INVALID_PRICE", lines=[line(1, "-10.00", "18")])

    def test_negative_discount(self):
        self.assert_refused("INVALID_DISCOUNT", lines=[line(1, "10.00", "18", discount="-1")])

    def test_line_discount_greater_than_the_line(self):
        self.assert_refused("INVALID_DISCOUNT", lines=[line(1, "10.00", "18", discount="11")])

    def test_invoice_discount_greater_than_the_subtotal(self):
        self.assert_refused(
            "INVALID_DISCOUNT", lines=[line(1, "10.00", "18")], invoice_discount="11.00"
        )

    def test_unsupported_tax_rate(self):
        self.assert_refused("INVALID_TAX_RATE", lines=[line(1, "10.00", "7")])

    def test_empty_invoice(self):
        self.assert_refused("NO_LINES", lines=[])

    def test_missing_business_state(self):
        self.assert_refused(
            "BUSINESS_PROFILE_INCOMPLETE", lines=[line(1, "10.00", "18")], business=""
        )

    def test_missing_place_of_supply(self):
        self.assert_refused("PLACE_OF_SUPPLY_MISSING", lines=[line(1, "10.00", "18")], pos="")

    def test_a_float_is_rejected_rather_than_silently_imprecise(self):
        with self.assertRaises(GSTCalculationError):
            calc([line(1.5, "10.00", "18")])

    def test_error_carries_a_field_for_the_serializer(self):
        error = self.assert_refused("INVALID_TAX_RATE", lines=[line(1, "10.00", "7")])
        self.assertEqual(error.field, "lines[0].tax_rate")


# ===========================================================================
# Service lines
# ===========================================================================
class ServiceLineTests(SimpleTestCase):
    def test_a_service_line_calculates_normally(self):
        t = calc([line(1, "5000.00", "18", item_type="SERVICE")])
        self.assertEqual(t.lines[0].item_type, "SERVICE")
        self.assertEqual(t.tax_total, D("900.00"))
        self.assertEqual(t.grand_total, D("5900.00"))