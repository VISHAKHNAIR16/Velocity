"""
Step B: invariants that must hold for EVERY invoice, over randomised inputs.

These are the tests that protect the tax engine from subtle drift. A specific
bug found while building Step B - dividing by 100 twice in the tax-inclusive
branch - passed every "expected total" check but broke
`taxable == net x 100 / (100 + rate)`. Randomised invariants catch that class
of mistake without needing to imagine the value in advance.

The seed is fixed and printed on failure so any breakage reproduces exactly.
"""

import random
from decimal import Decimal

from django.test import SimpleTestCase

from apps.core.constants import GST_RATE_CHOICES
from apps.core.money import q2
from apps.invoices.services.gst_calculator import (
    GSTCalculationError,
    LineInput,
    SupplyType,
    calculate_invoice,
)

D = Decimal
ZERO = D("0.00")
HUNDRED = D("100")
SEED = 20261008  # fixed so failures reproduce
RATES = [str(rate) for rate, _ in GST_RATE_CHOICES]


def build_random_invoice(rng, *, max_lines=6, inclusive=False, round_off=False):
    """A random but always-valid invoice."""
    line_count = rng.randint(1, max_lines)
    discount_total = ZERO
    lines = []
    for _ in range(line_count):
        quantity = q2(rng.randint(1, 40))
        unit_price = q2(D(rng.randint(1, 5000)) / D(100))
        rate = rng.choice(RATES)
        gross = q2(quantity * unit_price)
        # Keep the line discount strictly below the line amount.
        line_discount = ZERO
        if rng.random() < 0.4 and gross > D("1.00"):
            line_discount = q2(rng.randint(0, int(gross * D("100")) - 1) / D("100"))
        lines.append(
            LineInput.build(
                quantity=quantity,
                unit_price=unit_price,
                tax_rate=rate,
                line_discount=line_discount,
                item_type=rng.choice(["PRODUCT", "SERVICE"]),
            )
        )
        discount_total += line_discount

    # An invoice-level discount is applied AFTER line discounts, so it can never
    # exceed what is left - the sum of the post-line-discount amounts.
    net_total = sum(
        (q2(item.quantity * item.unit_price) - item.line_discount for item in lines), ZERO
    )
    invoice_discount = ZERO
    if rng.random() < 0.5 and net_total > D("1.00"):
        invoice_discount = q2(rng.randint(0, int(net_total * D("100")) - 1) / D("100"))

    business = rng.choice(["36", "27", "29", "04"])
    pos = business if rng.random() < 0.5 else rng.choice(["36", "27", "29", "33"])

    return lines, dict(
        business_state=business,
        place_of_supply=pos,
        business_is_regular=rng.random() < 0.85,
        invoice_discount=invoice_discount,
        prices_include_tax=inclusive,
        round_invoice_total=round_off,
    )


class InvariantTests(SimpleTestCase):
    """Every invariant, over many randomised invoices."""

    ITERATIONS = 300

    def _each_random_invoice(self, **flags):
        rng = random.Random(SEED)
        for index in range(self.ITERATIONS):
            lines, kwargs = build_random_invoice(rng, **flags)
            yield index, lines, kwargs

    def _calculate(self, lines, kwargs):
        return calculate_invoice(lines, **kwargs)

    # ---------- the headline invariants ----------
    def test_line_totals_sum_to_the_grand_total(self):
        for index, lines, kwargs in self._each_random_invoice():
            t = self._calculate(lines, kwargs)
            total = sum((r.total_amount for r in t.lines), ZERO)
            self.assertEqual(
                total, t.grand_total,
                f"iteration {index} (seed {SEED}): line totals must equal grand total",
            )

    def test_cgst_plus_sgst_plus_igst_equals_the_sum_of_line_tax(self):
        for index, lines, kwargs in self._each_random_invoice():
            t = self._calculate(lines, kwargs)
            line_tax = sum((r.tax_amount for r in t.lines), ZERO)
            self.assertEqual(
                t.tax_total, line_tax,
                f"iteration {index} (seed {SEED}): invoice tax must equal sum of line tax",
            )

    def test_per_line_halves_always_re_add_to_the_line_tax(self):
        for index, lines, kwargs in self._each_random_invoice():
            t = self._calculate(lines, kwargs)
            for r in t.lines:
                self.assertEqual(
                    r.cgst_amount + r.sgst_amount + r.igst_amount,
                    r.tax_amount,
                    f"iteration {index} (seed {SEED}): line {r.index} halves must re-add",
                )

    def test_apportioned_shares_sum_exactly_to_the_invoice_discount(self):
        for index, lines, kwargs in self._each_random_invoice():
            t = self._calculate(lines, kwargs)
            shares = sum((r.invoice_discount_share for r in t.lines), ZERO)
            self.assertEqual(
                shares, q2(kwargs["invoice_discount"]),
                f"iteration {index} (seed {SEED}): discount shares must sum exactly",
            )

    def test_supply_type_decides_which_head_carries_the_tax(self):
        for index, lines, kwargs in self._each_random_invoice():
            t = self._calculate(lines, kwargs)
            if t.supply_type == SupplyType.INTRA:
                self.assertEqual(t.igst_total, ZERO, f"iteration {index} (seed {SEED})")
            else:
                self.assertEqual(t.cgst_total + t.sgst_total, ZERO,
                                 f"iteration {index} (seed {SEED})")

    def test_taxable_plus_tax_equals_the_line_total(self):
        for index, lines, kwargs in self._each_random_invoice():
            t = self._calculate(lines, kwargs)
            for r in t.lines:
                self.assertEqual(
                    r.taxable_value + r.tax_amount, r.total_amount,
                    f"iteration {index} (seed {SEED}): taxable + tax must equal line total",
                )

    def test_net_is_gross_less_both_discounts(self):
        for index, lines, kwargs in self._each_random_invoice():
            t = self._calculate(lines, kwargs)
            for r in t.lines:
                self.assertEqual(
                    r.gross_amount - r.line_discount - r.invoice_discount_share,
                    r.net_amount,
                    f"iteration {index} (seed {SEED}): net must be gross less discounts",
                )

    # ---------- the tax formulas themselves ----------
    def test_exclusive_tax_equals_taxable_times_rate(self):
        for index, lines, kwargs in self._each_random_invoice(inclusive=False):
            t = self._calculate(lines, kwargs)
            for r in t.lines:
                self.assertEqual(
                    r.tax_amount, q2(r.taxable_value * r.tax_rate / HUNDRED),
                    f"iteration {index} (seed {SEED}): tax must be taxable x rate",
                )

    def test_inclusive_tax_is_extracted_from_the_price(self):
        """
        The bug this catches: dividing by 100 twice gave taxable 0.01 instead of
        100.00 on a 118.00 inclusive line, which still "balanced" but printed a
        nonsense taxable value and a 117.99 tax.
        """
        for index, lines, kwargs in self._each_random_invoice(inclusive=True):
            t = self._calculate(lines, kwargs)
            for r in t.lines:
                self.assertEqual(
                    r.taxable_value,
                    q2(r.net_amount * HUNDRED / (HUNDRED + r.tax_rate)),
                    f"iteration {index} (seed {SEED}): inclusive taxable must be "
                    f"net x 100 / (100 + rate)",
                )
                self.assertEqual(
                    r.net_amount,
                    r.taxable_value + r.tax_amount,
                    f"iteration {index} (seed {SEED}): inclusive net must split into taxable + tax",
                )

    def test_inclusive_line_total_equals_the_price_the_customer_quoted(self):
        for index, lines, kwargs in self._each_random_invoice(inclusive=True):
            t = self._calculate(lines, kwargs)
            for r in t.lines:
                self.assertEqual(
                    r.total_amount, r.net_amount,
                    f"iteration {index} (seed {SEED}): inclusive total must equal net exactly",
                )

    # ---------- round off ----------
    def test_grand_total_minus_round_off_equals_the_sum_of_lines(self):
        for index, lines, kwargs in self._each_random_invoice(round_off=True):
            t = self._calculate(lines, kwargs)
            sum_lines = sum((r.total_amount for r in t.lines), ZERO)
            self.assertEqual(
                t.grand_total - t.round_off, sum_lines,
                f"iteration {index} (seed {SEED}): grand - round_off must equal line sum",
            )

    def test_round_off_never_exceeds_half_a_rupee(self):
        for index, lines, kwargs in self._each_random_invoice(round_off=True):
            t = self._calculate(lines, kwargs)
            self.assertLessEqual(
                abs(t.round_off), D("0.50"),
                f"iteration {index} (seed {SEED}): round-off must be within a half rupee",
            )

    def test_round_off_never_changes_taxable_values_or_tax(self):
        """Presentational only: turning it on must not move a single tax figure."""
        rng = random.Random(SEED)
        for index in range(self.ITERATIONS):
            lines, kwargs = build_random_invoice(rng)
            off = self._calculate(lines, {**kwargs, "round_invoice_total": False})
            on = self._calculate(lines, {**kwargs, "round_invoice_total": True})
            self.assertEqual(off.taxable_total, on.taxable_total,
                             f"iteration {index} (seed {SEED})")
            self.assertEqual(off.tax_total, on.tax_total, f"iteration {index} (seed {SEED})")
            self.assertEqual(
                [r.taxable_value for r in off.lines],
                [r.taxable_value for r in on.lines],
                f"iteration {index} (seed {SEED})",
            )

    def test_round_off_defaults_to_off(self):
        rng = random.Random(SEED)
        lines, kwargs = build_random_invoice(rng)
        t = self._calculate(lines, kwargs)
        self.assertEqual(t.round_off, ZERO)
        sum_lines = sum((r.total_amount for r in t.lines), ZERO)
        self.assertEqual(t.grand_total, sum_lines)

    # ---------- the calculator never raises on valid input ----------
    def test_valid_random_invoices_never_raise(self):
        for index, lines, kwargs in self._each_random_invoice(inclusive=True, round_off=True):
            try:
                self._calculate(lines, kwargs)
            except GSTCalculationError as exc:  # pragma: no cover - failure path
                self.fail(f"iteration {index} (seed {SEED}) unexpectedly refused: {exc.code}")

    def test_generation_is_deterministic_for_a_given_seed(self):
        first = [build_random_invoice(random.Random(SEED)) for _ in range(1)]
        second = [build_random_invoice(random.Random(SEED)) for _ in range(1)]
        self.assertEqual(str(first), str(second))