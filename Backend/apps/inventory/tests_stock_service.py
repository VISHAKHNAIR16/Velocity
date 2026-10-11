"""
Step 2.1.3: the ledger service (SQLite).

The invariant under test throughout:

    Item.current_stock == SUM(StockMovement.quantity_change)

If a test here passes while that is false, the ledger is decorative.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase

from apps.core.errors import StockError
from apps.inventory.models import Item, ItemType
from apps.inventory.models_stock import (
    AdjustmentReason,
    MovementType,
    SourceType,
    StockMovement,
)
from apps.inventory.services.stock import (
    ItemInactive,
    adjust_stock,
    ledger_balance,
    reconcile,
    reserve_sale,
    set_tracking,
)
from apps.invoices.tests.base import (
    api_client_for,
    line_payload,
    make_item,
    make_party,
    make_user,
)


class LedgerTestCase(TestCase):
    def setUp(self):
        self.user = make_user()
        self.business = self.user.business
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        # Built through `tracked_item`, NOT the shared `make_item` helper: a
        # tracked item with no OPENING movement would start with a ledger sum of
        # zero against a cache of 100, and `assertInSync` would (correctly) fail.
        # The migration writes that OPENING in production; the fixture must too.
        self.item = self.tracked_item(name="Cotton Shirt", stock="100.000")

    def tracked_item(self, name="Thing", stock="100.000", **overrides):
        """A tracked product WITH its opening ledger row, like the cutover writes."""
        from decimal import Decimal as D

        stock = D(stock)
        item = Item.objects.create(
            business=self.business,
            name=name,
            item_type=ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=D("249.50"),
            tax_rate=D("5.00"),
            current_stock=stock,
            low_stock_threshold=D("5.000"),
            track_stock=True,
            **overrides,
        )
        if stock != 0:
            StockMovement.objects.create(
                business=self.business,
                item=item,
                movement_type=MovementType.OPENING,
                quantity_change=stock,
                stock_after=stock,
                source_type=SourceType.SYSTEM,
                source_label="TEST OPENING",
                reason=AdjustmentReason.COUNT_CORRECTION,
                note="Opening balance at stock-ledger cutover",
            )
        return item

    def draft_with(self, quantity="2", item=None, **extra):
        item = item or self.item
        return self.client_.post(
            "/api/v1/invoices/",
            {
                "party": self.party.pk,
                "items": [line_payload(item, quantity=quantity, **extra)],
            },
            format="json",
        ).json()

    def assertInSync(self, item):
        """The load-bearing assertion, in one place so every test uses it."""
        self.assertEqual(
            ledger_balance(item),
            item.current_stock or Decimal("0.000"),
            f"item {item.pk} cache {item.current_stock} != ledger {ledger_balance(item)}",
        )

    def sales(self):
        """
        Only SALE rows.

        The fixture writes an OPENING per tracked item, exactly as the cutover
        migration does. Counting *all* movements would make "nothing moved" tests
        pass or fail for the wrong reason.
        """
        return StockMovement.objects.filter(movement_type=MovementType.SALE)

    def run_issue(self, invoice_id):
        """Two-phase reserve -> allocate -> commit, exactly as 2.1.4 will."""
        from apps.invoices.services.numbering import allocate_invoice_number

        with transaction.atomic():
            from apps.invoices.models import Invoice

            invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
            plan = reserve_sale(invoice)
            label = allocate_invoice_number(invoice.business, invoice.invoice_date)
            from apps.inventory.services.stock import commit_sale

            commit_sale(invoice, plan, label, user=self.user)
        return label


class MovementConstraintTests(LedgerTestCase):
    def test_a_zero_movement_is_refused(self):
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                business=self.business,
                item=self.item,
                movement_type=MovementType.ADJUSTMENT,
                quantity_change=Decimal("0.000"),
                stock_after=Decimal("5.000"),
            )

    def test_a_sale_must_reduce_stock(self):
        """Sign is a property of the type, not a judgement call (2.1.1)."""
        with self.assertRaises((ValidationError, IntegrityError)):
            with transaction.atomic():
                StockMovement.objects.create(
                    business=self.business,
                    item=self.item,
                    movement_type=MovementType.SALE,
                    quantity_change=Decimal("5.000"),  # wrong way round
                    stock_after=Decimal("105.000"),
                )

    def test_a_reversal_must_add_stock(self):
        with self.assertRaises((ValidationError, IntegrityError)):
            with transaction.atomic():
                StockMovement.objects.create(
                    business=self.business,
                    item=self.item,
                    movement_type=MovementType.SALE_REVERSAL,
                    quantity_change=Decimal("-5.000"),  # wrong way round
                    stock_after=Decimal("95.000"),
                )

    def test_an_adjustment_may_go_either_way(self):
        for change in ("-3.000", "3.000"):
            with self.subTest(change=change):
                StockMovement.objects.create(
                    business=self.business,
                    item=self.item,
                    movement_type=MovementType.ADJUSTMENT,
                    quantity_change=Decimal(change),
                    stock_after=Decimal("100.000"),
                    reason=AdjustmentReason.COUNT_CORRECTION,
                )

    def test_other_needs_a_note(self):
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(
                business=self.business,
                item=self.item,
                movement_type=MovementType.ADJUSTMENT,
                quantity_change=Decimal("1.000"),
                stock_after=Decimal("101.000"),
                reason=AdjustmentReason.OTHER,
                note="",
            )

    def test_other_with_a_note_is_accepted(self):
        movement = StockMovement.objects.create(
            business=self.business,
            item=self.item,
            movement_type=MovementType.ADJUSTMENT,
            quantity_change=Decimal("1.000"),
            stock_after=Decimal("101.000"),
            reason=AdjustmentReason.OTHER,
            note="Vendor sent two extra",
        )
        self.assertEqual(movement.note, "Vendor sent two extra")

    def test_a_movement_cannot_be_edited(self):
        """Append-only: a correction is a NEW row, never an edit."""
        movement = StockMovement.objects.create(
            business=self.business,
            item=self.item,
            movement_type=MovementType.ADJUSTMENT,
            quantity_change=Decimal("1.000"),
            stock_after=Decimal("101.000"),
            reason=AdjustmentReason.DAMAGED,
        )
        movement.quantity_change = Decimal("99.000")
        with self.assertRaises(ValidationError):
            movement.save()
        movement.refresh_from_db()
        self.assertEqual(movement.quantity_change, Decimal("1.000"))

    def test_a_movement_cannot_be_deleted(self):
        movement = StockMovement.objects.create(
            business=self.business,
            item=self.item,
            movement_type=MovementType.ADJUSTMENT,
            quantity_change=Decimal("1.000"),
            stock_after=Decimal("101.000"),
            reason=AdjustmentReason.DAMAGED,
        )
        with self.assertRaises(ValidationError):
            movement.delete()
        self.assertTrue(StockMovement.objects.filter(pk=movement.pk).exists())

    def test_a_movement_is_not_deletable_through_the_queryset_either(self):
        """`.delete()` on a queryset bypasses the model's `delete()`."""
        StockMovement.objects.create(
            business=self.business,
            item=self.item,
            movement_type=MovementType.ADJUSTMENT,
            quantity_change=Decimal("1.000"),
            stock_after=Decimal("101.000"),
            reason=AdjustmentReason.DAMAGED,
        )
        with self.assertRaises(ValidationError):
            StockMovement.objects.filter(business=self.business).delete()

    def test_the_same_source_line_cannot_move_twice(self):
        """Decision 30: idempotency in the DATABASE, not by hope."""
        for _ in range(2):
            with self.subTest():
                try:
                    with transaction.atomic():
                        StockMovement.objects.create(
                            business=self.business,
                            item=self.item,
                            movement_type=MovementType.SALE,
                            quantity_change=Decimal("-1.000"),
                            stock_after=Decimal("99.000"),
                            source_type=SourceType.INVOICE,
                            source_id=1,
                            source_line_id=42,
                        )
                except IntegrityError:
                    pass  # the second insert is rejected, which is the point
        self.assertEqual(
            StockMovement.objects.filter(source_line_id=42, movement_type=MovementType.SALE).count(),
            1,
            "the ledger recorded the same line twice",
        )


class ReserveGuardTests(TransactionTestCase):
    """
    `reserve_sale` must refuse to run outside a transaction.

    A separate class because `TestCase` wraps every test in one, which would hide
    the guard entirely - the assertion would pass for the wrong reason.
    """

    def test_reserve_says_it_must_be_in_a_transaction(self):
        from apps.invoices.models import Invoice
        from apps.invoices.tests.base import (
            api_client_for,
            line_payload,
            make_item,
            make_party,
            make_user,
        )

        user = make_user()
        client = api_client_for(user)
        party = make_party(user)
        item = make_item(user)
        draft = client.post(
            "/api/v1/invoices/",
            {"party": party.pk, "items": [line_payload(item)]},
            format="json",
        ).json()

        with self.assertRaises(AssertionError) as ctx:
            reserve_sale(Invoice.objects.get(pk=draft["id"]))
        self.assertIn("transaction", str(ctx.exception))


class ReserveAndCommitTests(LedgerTestCase):
    def test_a_sale_deducts_and_stays_in_sync(self):
        draft = self.draft_with(quantity="2")
        before = self.item.current_stock
        self.run_issue(draft["id"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, before - Decimal("2.000"))
        self.assertInSync(self.item)

    def test_the_movement_carries_the_invoice_number_as_its_label(self):
        draft = self.draft_with(quantity="2")
        label = self.run_issue(draft["id"])
        movement = StockMovement.objects.get(source_line_id__isnull=False)
        self.assertEqual(movement.source_label, label)
        self.assertEqual(movement.source_type, SourceType.INVOICE)

    def test_the_balance_is_recorded_on_each_row(self):
        """A history view must not have to sum the ledger to show a running total."""
        draft = self.draft_with(quantity="3")
        self.run_issue(draft["id"])
        movement = self.sales().get()
        self.item.refresh_from_db()
        self.assertEqual(
            movement.stock_after,
            self.item.current_stock,
            "stock_after must be the item's balance after this row",
        )

    def test_several_lines_of_one_item_chain_correctly(self):
        from decimal import Decimal as D

        item = self.tracked_item(name="Multi", stock="100.000")
        draft = self.client_.post(
            "/api/v1/invoices/",
            {
                "party": self.party.pk,
                "items": [
                    line_payload(item, quantity="2"),
                    line_payload(item, quantity="3"),
                ],
            },
            format="json",
        ).json()
        self.run_issue(draft["id"])
        item.refresh_from_db()
        self.assertEqual(item.current_stock, D("95.000"))
        self.assertInSync(item)
        rows = list(
            StockMovement.objects.filter(item=item).order_by("id").values_list("stock_after", flat=True)
        )
        # Includes the fixture's OPENING row, exactly as production would have one.
        self.assertEqual(rows, [D("100.000"), D("98.000"), D("95.000")], "running balance must chain")

    def test_a_service_line_does_not_move_stock(self):
        from decimal import Decimal as D

        service = Item.objects.create(
            business=self.business,
            name="Consulting",
            item_type=ItemType.SERVICE,
            unit="HRS",
            hsn_sac_code="998311",
            service_description="Advisory",
            sales_price=D("500.00"),
            tax_rate=D("18.00"),
            current_stock=None,
            track_stock=False,
        )
        draft = self.draft_with(quantity="5", item=service)
        self.run_issue(draft["id"])
        self.assertEqual(self.sales().filter(item=service).count(), 0)

    def test_a_free_text_line_does_not_move_stock(self):
        draft = self.client_.post(
            "/api/v1/invoices/",
            {
                "party": self.party.pk,
                "items": [
                    {
                        "item_name": "Transport",
                        "item_type": "SERVICE",
                        "hsn_sac_code": "996511",
                        "unit": "NOS",
                        "service_description": "Freight",
                        "quantity": "1",
                        "unit_price": "80.00",
                        "tax_rate": "18.00",
                    }
                ],
            },
            format="json",
        ).json()
        self.run_issue(draft["id"])
        self.assertEqual(self.sales().count(), 0, "a free-text line has no stock")

    def test_an_untracked_product_does_not_move_stock(self):
        from decimal import Decimal as D

        untracked = Item.objects.create(
            business=self.business,
            name="Untracked widget",
            item_type=ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=D("10.00"),
            tax_rate=D("18.00"),
            current_stock=None,
            track_stock=False,
        )
        draft = self.draft_with(quantity="3", item=untracked)
        self.run_issue(draft["id"])
        self.assertEqual(self.sales().filter(item=untracked).count(), 0)

    def test_an_inactive_item_is_refused(self):
        from apps.inventory.services.stock import reserve_sale as reserve
        from apps.invoices.models import Invoice

        self.item.is_active = False
        self.item.save(update_fields=["is_active"])
        draft = self.draft_with(quantity="1")
        with self.assertRaises(ItemInactive):
            with transaction.atomic():
                reserve(Invoice.objects.get(pk=draft["id"]))

    def test_reserve_writes_nothing_itself(self):
        """Phase 1 is check-only; the whole point is that a failure never burns a number."""
        from apps.invoices.models import Invoice

        draft = self.draft_with(quantity="2")
        with transaction.atomic():
            reserve_sale(Invoice.objects.get(pk=draft["id"]))
        self.assertEqual(self.sales().count(), 0, "reserve must write nothing")
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))


class NegativeStockPolicyTests(LedgerTestCase):
    def block(self):
        self.business.negative_stock_policy = "BLOCK"
        self.business.save(update_fields=["negative_stock_policy"])

    def test_block_refuses_a_sale_that_exceeds_stock(self):
        from apps.inventory.services.stock import InsufficientStock

        self.block()
        draft = self.draft_with(quantity="500")
        with self.assertRaises(InsufficientStock) as ctx:
            with transaction.atomic():
                from apps.invoices.models import Invoice

                reserve_sale(Invoice.objects.get(pk=draft["id"]))
        self.assertEqual(len(ctx.exception.shortages), 1)
        self.assertEqual(ctx.exception.shortages[0].requested, Decimal("500.000"))

    def test_block_allows_a_sale_within_stock(self):
        self.block()
        draft = self.draft_with(quantity="5")
        self.run_issue(draft["id"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("95.000"))

    def test_block_changes_nothing_when_it_refuses(self):
        """The plan's requirement: stock, counter and movements all untouched."""
        from apps.inventory.services.stock import InsufficientStock
        from apps.invoices.models import Invoice

        self.block()
        draft = self.draft_with(quantity="500")
        with self.assertRaises(InsufficientStock):
            with transaction.atomic():
                reserve_sale(Invoice.objects.get(pk=draft["id"]))
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))
        self.assertEqual(self.sales().count(), 0, "a refused sale must move nothing")

    def test_allow_goes_negative_and_warns(self):
        """ALLOW must not raise; it reports what will happen and lets the sale stand."""
        from apps.invoices.models import Invoice

        draft = self.draft_with(quantity="500")
        with transaction.atomic():
            plan = reserve_sale(Invoice.objects.get(pk=draft["id"]))
        self.assertEqual(len(plan.warnings), 1)
        self.assertEqual(plan.warnings[0].item_id, self.item.pk)
        self.assertEqual(plan.warnings[0].resulting_balance, Decimal("-400.000"))

    def test_allow_actually_sells_and_records_the_negative_balance(self):
        draft = self.draft_with(quantity="500")
        self.run_issue(draft["id"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("-400.000"))
        self.assertInSync(self.item)

    def test_the_default_policy_is_allow(self):
        """Decision 33: most shops' opening stock is wrong on day one."""
        from apps.accounts.models import BusinessProfile

        field = BusinessProfile._meta.get_field("negative_stock_policy")
        self.assertEqual(field.default, "ALLOW")


class ReversalTests(LedgerTestCase):
    def test_cancelling_a_draft_restores_nothing(self):
        """There was no SALE, so there is nothing to give back."""
        from apps.inventory.services.stock import reverse_sale
        from apps.invoices.models import Invoice

        draft = self.draft_with(quantity="2")
        invoice = Invoice.objects.get(pk=draft["id"])
        self.assertEqual(reverse_sale(invoice), [])

    def test_a_sale_is_restored_exactly_once(self):
        from apps.inventory.services.stock import reverse_sale
        from apps.invoices.models import Invoice

        draft = self.draft_with(quantity="4")
        self.run_issue(draft["id"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("96.000"))

        invoice = Invoice.objects.get(pk=draft["id"])
        reversals = reverse_sale(invoice)
        self.assertEqual(len(reversals), 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))
        self.assertInSync(self.item)

    def test_reversing_twice_does_not_double_restore(self):
        from apps.inventory.services.stock import reverse_sale
        from apps.invoices.models import Invoice

        draft = self.draft_with(quantity="4")
        self.run_issue(draft["id"])
        invoice = Invoice.objects.get(pk=draft["id"])

        reverse_sale(invoice)
        self.assertEqual(len(reverse_sale(invoice)), 0, "a second reversal must be a no-op")
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))
        self.assertInSync(self.item)

    def test_a_pre_cutover_invoice_does_not_add_stock_that_was_never_removed(self):
        """
        Decision 35. An invoice issued before the ledger existed has no SALE row;
        "restoring" it would INCREASE stock.
        """
        from apps.inventory.services.stock import reverse_sale
        from apps.invoices.models import Invoice

        draft = self.draft_with(quantity="4")
        invoice = Invoice.objects.get(pk=draft["id"])
        invoice.status = Invoice.Status.ISSUED
        invoice.invoice_number = "INV/25-26/00001"
        invoice.issued_at = __import__("django.utils.timezone", fromlist=["timezone"]).now()
        invoice.save()

        before = self.item.current_stock
        self.assertEqual(reverse_sale(invoice), [])
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, before)


class AdjustStockTests(LedgerTestCase):
    def test_change_adjusts_by_the_difference(self):
        movement = adjust_stock(
            self.business,
            self.item,
            mode="CHANGE",
            quantity="-5",
            reason=AdjustmentReason.DAMAGED,
            note="Two broken in transit",
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("95.000"))
        self.assertEqual(movement.quantity_change, Decimal("-5.000"))
        self.assertInSync(self.item)

    def test_set_makes_the_balance_equal(self):
        adjust_stock(
            self.business,
            self.item,
            mode="SET",
            quantity="42",
            reason=AdjustmentReason.COUNT_CORRECTION,
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("42.000"))
        self.assertInSync(self.item)

    def test_a_set_that_changes_nothing_writes_nothing(self):
        """A no-op row would violate the not-zero constraint and pollute history."""
        movement = adjust_stock(
            self.business,
            self.item,
            mode="SET",
            quantity="100",
            reason=AdjustmentReason.COUNT_CORRECTION,
        )
        self.assertIsNone(movement)
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.ADJUSTMENT).count(),
            0,
            "a no-op adjustment must not pollute the history",
        )

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(StockError):
            adjust_stock(self.business, self.item, mode="TELEPORT", quantity="1")

    def test_an_item_from_another_business_is_refused(self):
        """Defence in depth: a wrong item id must not corrupt another shop."""
        other = make_user("other@example.com")
        their_item = make_item(other, code="OTHER-1", name="Theirs")
        with self.assertRaises(StockError):
            adjust_stock(
                self.business,
                their_item,
                mode="CHANGE",
                quantity="1",
                reason=AdjustmentReason.OTHER,
                note="should not work",
            )


class SetTrackingTests(LedgerTestCase):
    def test_disabling_tracking_closes_the_ledger_to_zero(self):
        """Decision 40: the invariant must hold in EVERY state, not just the normal one."""
        item = self.tracked_item(name="Closing", stock="12.000")
        Item.objects.filter(pk=item.pk).update(track_stock=True, current_stock=Decimal("12.000"))

        set_tracking(self.business, item, enabled=False)

        item.refresh_from_db()
        self.assertFalse(item.track_stock)
        self.assertIsNone(item.current_stock)
        self.assertEqual(
            ledger_balance(item), Decimal("0.000"), "the ledger must close to zero"
        )

    def test_enabling_tracking_writes_an_opening(self):
        from decimal import Decimal as D

        item = Item.objects.create(
            business=self.business,
            name="Opening later",
            item_type=ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=D("10.00"),
            tax_rate=D("18.00"),
            current_stock=None,
            track_stock=False,
        )
        set_tracking(self.business, item, enabled=True, opening_quantity="7")

        item.refresh_from_db()
        self.assertTrue(item.track_stock)
        self.assertEqual(item.current_stock, D("7.000"))
        self.assertEqual(ledger_balance(item), D("7.000"))

    def test_the_unit_is_not_locked_here(self):
        """Decision 39 is enforced by the API (2.1.4); the service allows it."""
        item = self.tracked_item(name="Unit change", stock="5.000")
        set_tracking(self.business, item, enabled=True, opening_quantity="0")
        item.refresh_from_db()
        self.assertTrue(item.tracks_stock)


class ReconcileTests(LedgerTestCase):
    def test_a_clean_ledger_reports_nothing(self):
        self.assertEqual(reconcile(self.business), [])

    def test_a_hand_corrupted_cache_is_detected(self):
        Item.objects.filter(pk=self.item.pk).update(current_stock=Decimal("999.000"))
        drift = reconcile(self.business)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["cached"], "999.000")
        self.assertEqual(drift[0]["ledger"], "100.000")

    def test_fix_repairs_the_cache_to_the_ledger_without_inventing_a_movement(self):
        """
        The ledger is authoritative, so the cache is corrected and the ledger is
        left alone. Writing a "repair" movement would change the sum it was
        reconciling to and leave the two still disagreeing.
        """
        Item.objects.filter(pk=self.item.pk).update(current_stock=Decimal("999.000"))
        movements_before = StockMovement.objects.count()

        drift = reconcile(self.business, fix=True)

        self.assertTrue(drift[0].get("fixed"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))
        self.assertEqual(
            StockMovement.objects.count(),
            movements_before,
            "a repair must not invent a ledger entry claiming stock moved",
        )
        self.assertInSync(self.item)

    def test_reconciling_twice_is_idempotent(self):
        Item.objects.filter(pk=self.item.pk).update(current_stock=Decimal("999.000"))
        reconcile(self.business, fix=True)
        self.assertEqual(reconcile(self.business), [])

    def test_untracked_items_are_not_reported(self):
        Item.objects.filter(pk=self.item.pk).update(track_stock=False, current_stock=None)
        self.assertEqual(reconcile(self.business), [])
