"""
Step 2.1.2: `stock_plan` is pure, so these are `SimpleTestCase` - no database,
no fixtures, no transaction wrapping, and the whole file runs in milliseconds.

That is the payoff of keeping the planning logic free of Django: the part most
likely to be wrong (what quantity moves, and is there enough) is provable in
isolation.

The float test is the one worth keeping. Stock is counted in pieces, KG and
litres; `float` cannot represent 0.001 exactly, and a float sneaking into this
module is how a ledger ends up disagreeing with its own cache by a paisa-sized
amount nobody can explain.
"""

from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.inventory.services.stock_plan import (
    POLICY_ALLOW,
    POLICY_BLOCK,
    MovementRequest,
    Shortage,
    StockLine,
    StockPlanError,
    check_sufficiency,
    plan_reversal,
    plan_sale,
)


def line(line_id, item_id, quantity):
    return StockLine(line_id=line_id, item_id=item_id, quantity=Decimal(str(quantity)))


def raw_line(line_id, item_id, quantity):
    """
    A StockLine with the quantity left exactly as given.

    The `line()` helper coerces through `Decimal(str(...))`, which would hide the
    very inputs these tests are checking: a float arrives as a clean Decimal and
    a non-numeric string blows up in the *helper* rather than in the planner.
    """
    return StockLine(line_id=line_id, item_id=item_id, quantity=quantity)


class NoDjangoDependencyTests(SimpleTestCase):
    def test_the_module_imports_nothing_from_the_models(self):
        """
        The rule that keeps this file fast and its tests honest. Checked by
        reading the source rather than trusting a comment.
        """
        import apps.inventory.services.stock_plan as module

        source = __import__("inspect").getsource(module)
        for forbidden in ("from apps.inventory.models", "django.db", "Item.objects"):
            self.assertNotIn(
                forbidden, source, f"{forbidden} must not appear in a pure module"
            )


class PlanSaleTests(SimpleTestCase):
    def test_one_line_produces_one_negative_movement(self):
        plan = plan_sale([line(1, 10, "2.500")])
        self.assertEqual(len(plan.movements), 1)
        self.assertEqual(plan.movements[0].quantity_change, Decimal("-2.500"))
        self.assertEqual(plan.movements[0].item_id, 10)

    def test_the_same_item_on_three_lines_is_totalled(self):
        """Decision 38: sufficiency is per item, not per line."""
        plan = plan_sale([line(1, 10, "4"), line(2, 10, "4"), line(3, 10, "4")])
        self.assertEqual(len(plan.movements), 3, "each line still moves its own quantity")
        self.assertEqual(plan.per_item_total[10], Decimal("12.000"))

    def test_movement_order_is_by_item_then_line(self):
        """
        Deterministic, because the service locks items in ascending id. Two
        invoices touching the same items in different orders would deadlock if the
        plan order were not stable.
        """
        plan = plan_sale(
            [
                line(30, 7, "1"),
                line(10, 9, "1"),
                line(20, 7, "1"),
            ]
        )
        self.assertEqual(
            [(m.item_id, m.line_id) for m in plan.movements],
            [(7, 20), (7, 30), (9, 10)],
        )

    def test_the_plan_is_stable_across_input_orderings(self):
        a = plan_sale([line(1, 5, "1"), line(2, 5, "2")])
        b = plan_sale([line(2, 5, "2"), line(1, 5, "1")])
        self.assertEqual(a.movements, b.movements)

    def test_three_decimal_quantities_stay_exact(self):
        """0.001 must not drift to 0.000 or 0.002."""
        plan = plan_sale([line(1, 1, "0.001"), line(2, 2, "0.002")])
        self.assertEqual(plan.per_item_total[1], Decimal("0.001"))
        self.assertEqual(plan.per_item_total[2], Decimal("0.002"))

    def test_quantities_are_quantised_to_three_places(self):
        plan = plan_sale([line(1, 1, "2")])
        self.assertEqual(plan.movements[0].quantity_change, Decimal("-2.000"))

    def test_an_empty_plan_is_refused(self):
        with self.assertRaises(StockPlanError):
            plan_sale([])

    def test_none_is_refused(self):
        with self.assertRaises(StockPlanError):
            plan_sale(None)

    def test_zero_quantity_is_refused(self):
        with self.assertRaises(StockPlanError):
            plan_sale([line(1, 1, "0")])

    def test_negative_quantity_is_refused(self):
        with self.assertRaises(StockPlanError):
            plan_sale([line(1, 1, "-1")])

    def test_more_than_three_decimals_is_refused(self):
        """
        Refused rather than rounded: silently rounding 0.0005 down to 0 would
        lose a real movement, and rounding up would invent one.
        """
        with self.assertRaises(StockPlanError):
            plan_sale([line(1, 1, "0.0005")])

    def test_a_float_is_refused_outright(self):
        """0.1 + 0.2 != 0.3 in binary floating point; stock must never use it."""
        with self.assertRaises(StockPlanError):
            plan_sale([raw_line(1, 1, 0.1)])

    def test_non_numeric_quantity_is_refused(self):
        with self.assertRaises(StockPlanError):
            plan_sale([raw_line(1, 1, "three")])

    def test_none_quantity_is_refused(self):
        with self.assertRaises(StockPlanError):
            plan_sale([raw_line(1, 1, None)])


class CheckSufficiencyTests(SimpleTestCase):
    def test_enough_stock_produces_neither_shortage_nor_warning(self):
        shortages, warnings = check_sufficiency({1: Decimal("5")}, {1: Decimal("10")}, POLICY_BLOCK)
        self.assertEqual(shortages, [])
        self.assertEqual(warnings, [])

    def test_exactly_enough_is_not_a_shortage(self):
        """Selling the last unit is normal, not an error."""
        shortages, warnings = check_sufficiency({1: Decimal("10")}, {1: Decimal("10")}, POLICY_BLOCK)
        self.assertEqual(shortages, [])
        self.assertEqual(warnings, [])

    def test_one_thousandth_short_is_a_shortage(self):
        shortages, _ = check_sufficiency(
            {1: Decimal("10.001")}, {1: Decimal("10.000")}, POLICY_BLOCK
        )
        self.assertEqual(len(shortages), 1)
        self.assertEqual(shortages[0].short_by, Decimal("0.001"))

    def test_block_reports_a_shortage_with_the_numbers_a_user_needs(self):
        shortages, warnings = check_sufficiency(
            {7: Decimal("5")}, {7: Decimal("3")}, POLICY_BLOCK
        )
        self.assertEqual(warnings, [], "BLOCK must not emit warnings")
        self.assertEqual(len(shortages), 1)
        self.assertEqual(shortages[0].item_id, 7)
        self.assertEqual(shortages[0].requested, Decimal("5"))
        self.assertEqual(shortages[0].available, Decimal("3"))
        self.assertEqual(shortages[0].short_by, Decimal("2"))

    def test_allow_reports_a_warning_with_the_resulting_balance(self):
        shortages, warnings = check_sufficiency(
            {7: Decimal("5")}, {7: Decimal("3")}, POLICY_ALLOW
        )
        self.assertEqual(shortages, [], "ALLOW must not raise shortages")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].resulting_balance, Decimal("-2.000"))

    def test_allow_does_not_warn_when_stock_is_sufficient(self):
        _, warnings = check_sufficiency(
            {7: Decimal("5")}, {7: Decimal("5")}, POLICY_ALLOW
        )
        self.assertEqual(warnings, [])

    def test_one_shortage_among_several_is_reported_on_its_own(self):
        shortages, _ = check_sufficiency(
            {1: Decimal("1"), 2: Decimal("99"), 3: Decimal("2")},
            {1: Decimal("5"), 2: Decimal("1"), 3: Decimal("5")},
            POLICY_BLOCK,
        )
        self.assertEqual([s.item_id for s in shortages], [2])

    def test_a_null_balance_is_treated_as_zero(self):
        """NULL means untracked; the caller filters those, but must not crash."""
        shortages, _ = check_sufficiency({1: Decimal("1")}, {1: None}, POLICY_BLOCK)
        self.assertEqual(len(shortages), 1)
        self.assertEqual(shortages[0].available, Decimal("0"))

    def test_an_item_missing_from_the_balance_map_is_treated_as_zero(self):
        shortages, _ = check_sufficiency({1: Decimal("1")}, {}, POLICY_BLOCK)
        self.assertEqual(len(shortages), 1)

    def test_an_unknown_policy_is_refused(self):
        with self.assertRaises(StockPlanError):
            check_sufficiency({1: Decimal("1")}, {1: Decimal("1")}, "MAYBE")

    def test_several_lines_of_one_item_are_checked_as_the_sum(self):
        """The case decision 38 exists for: 3 x 4 against stock 10 each."""
        plan = plan_sale([line(1, 5, "4"), line(2, 5, "4"), line(3, 5, "4")])
        shortages, _ = check_sufficiency(
            plan.per_item_total, {5: Decimal("10")}, POLICY_BLOCK
        )
        self.assertEqual(len(shortages), 1)
        self.assertEqual(shortages[0].short_by, Decimal("2.000"))


class PlanReversalTests(SimpleTestCase):
    @staticmethod
    def sale(source_line_id, item_id, quantity="-2.500"):
        return SimpleNamespace(
            source_line_id=source_line_id,
            item_id=item_id,
            quantity_change=Decimal(quantity),
        )

    @staticmethod
    def reversal(source_line_id, item_id):
        return SimpleNamespace(
            source_line_id=source_line_id,
            item_id=item_id,
            quantity_change=Decimal("2.500"),
        )

    def test_a_sale_is_reversed_with_a_positive_quantity(self):
        requests = plan_reversal([self.sale(11, 5)], [])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].quantity_change, Decimal("2.500"))
        self.assertEqual(requests[0].source_line_id, 11)

    def test_an_already_reversed_sale_is_skipped(self):
        """
        Otherwise a double cancellation would add the same units twice - the
        single most expensive bug this module could have.
        """
        requests = plan_reversal([self.sale(11, 5)], [self.reversal(11, 5)])
        self.assertEqual(requests, [])

    def test_reversing_nothing_is_a_no_op(self):
        """
        Decision 35: an invoice issued BEFORE the ledger existed has no SALE rows.
        "Adding back" for it would increase stock that was never removed.
        """
        self.assertEqual(plan_reversal([], []), [])

    def test_a_sale_without_a_source_line_cannot_be_reversed(self):
        """No line to trace it back to, so reversing would be a guess."""
        orphan = SimpleNamespace(source_line_id=None, item_id=5, quantity_change=Decimal("-1"))
        self.assertEqual(plan_reversal([orphan], []), [])

    def test_only_the_unreversed_ones_come_back(self):
        requests = plan_reversal(
            [self.sale(11, 5), self.sale(12, 5), self.sale(13, 5)],
            [self.reversal(12, 5)],
        )
        self.assertEqual([r.source_line_id for r in requests], [11, 13])

    def test_reversal_order_is_deterministic(self):
        requests = plan_reversal(
            [self.sale(3, 9), self.sale(1, 5), self.sale(2, 5)], []
        )
        self.assertEqual(
            [(r.item_id, r.source_line_id) for r in requests],
            [(5, 1), (5, 2), (9, 3)],
        )

    def test_reversal_is_never_a_duplicate_of_the_sale_sign(self):
        requests = plan_reversal([self.sale(11, 5, "-4")], [])
        self.assertEqual(requests[0].quantity_change, Decimal("4.000"))
        self.assertGreater(requests[0].quantity_change, 0)


class PlanIsPureTests(SimpleTestCase):
    def test_planning_does_not_mutate_its_input(self):
        lines = [line(1, 5, "2"), line(2, 6, "3")]
        snapshot = [(item.line_id, item.item_id, item.quantity) for item in lines]
        plan_sale(lines)
        self.assertEqual(
            [(item.line_id, item.item_id, item.quantity) for item in lines], snapshot
        )

    def test_planning_twice_gives_the_same_answer(self):
        lines = [line(1, 5, "2"), line(2, 5, "3")]
        self.assertEqual(plan_sale(lines).movements, plan_sale(lines).movements)

    def test_movement_request_is_immutable(self):
        """A frozen dataclass cannot be edited by accident between plan and write."""
        import dataclasses

        request = MovementRequest(line_id=1, item_id=2, quantity_change=Decimal("-1"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            request.quantity_change = Decimal("-2")

    def test_stock_line_is_immutable(self):
        import dataclasses

        item = line(1, 2, "3")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            item.quantity = Decimal("9")

    def test_shortage_short_by_is_correct_for_a_shortfall(self):
        self.assertEqual(
            Shortage(item_id=1, requested=Decimal("7"), available=Decimal("2")).short_by,
            Decimal("5"),
        )