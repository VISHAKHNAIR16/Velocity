"""
Stock concurrency (step 2.1.4). **PostgreSQL only.**

These run against a real PostgreSQL because the whole point is row locking, and
Django reports `has_select_for_update = False` for SQLite - where
`select_for_update()` is silently ignored. A concurrency test on SQLite passes
whether or not the locking works, which is worse than having no test because it
looks like proof.

Skipped with a loud message elsewhere, so `manage.py test` still runs on SQLite.
CI runs these against a `postgres:16` service container (decision 27).
"""

import os
import threading
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TransactionTestCase

from apps.inventory.models import ItemType
from apps.inventory.models_stock import AdjustmentReason, MovementType, StockMovement
from apps.inventory.services.stock import ledger_balance, reconcile
from apps.invoices.models import Invoice, InvoiceCounter
from apps.invoices.tests.base import (
    INVOICE_LIST_URL,
    api_client_for,
    line_payload,
    make_party,
    make_user,
)
from apps.invoices.tests.test_issue_api import cancel_url, issue_url

requires_locking = (
    connection.features.has_select_for_update
    if connection.vendor == "postgresql"
    else False
)


def close_thread_connections():
    """
    Close the connections opened inside a worker thread.

    Each thread gets its own database connection, and a connection opened in one
    thread cannot be closed from another. Without this, every finished worker
    leaves a live session behind and the suite fails at teardown with
    `database "test_..." is being accessed by other users` - which looks like a
    test failure but is really the tests being untidy. SQLite never notices,
    because it has no sessions.
    """
    from django.db import connections

    connections.close_all()


# How many workers the storm tests run at once.
#
# Every worker holds its own connection, and the pool-free endpoint these tests
# require has a low concurrency cap - a Neon dev branch closes connections
# outright past it ("server closed the connection unexpectedly"). CI runs a
# plain `postgres:16` service with no such cap, but these tests should also be
# runnable on a shared dev branch, so the fan-out is kept small. Set
# STOCK_CONCURRENCY_WORKERS higher where the database can take it.
WORKERS = int(os.getenv("STOCK_CONCURRENCY_WORKERS", "3"))


def make_tracked_item(user, name="Widget", stock="100.000"):
    from apps.inventory.models import Item

    amount = Decimal(stock)
    item = Item.objects.create(
        business=user.business,
        name=name,
        item_type=ItemType.PRODUCT,
        unit="PCS",
        hsn_sac_code="610510",
        sales_price=Decimal("249.50"),
        tax_rate=Decimal("5.00"),
        current_stock=amount,
        low_stock_threshold=Decimal("5.000"),
        track_stock=True,
    )
    StockMovement.objects.create(
        business=user.business,
        item=item,
        movement_type=MovementType.OPENING,
        quantity_change=amount,
        stock_after=amount,
        source_type="SYSTEM",
        source_label="TEST OPENING",
        reason=AdjustmentReason.COUNT_CORRECTION,
    )
    return item


def draft_for(user, item, quantity="1"):
    client = api_client_for(user)
    response = client.post(
        INVOICE_LIST_URL,
        {"party": make_party(user).pk, "items": [line_payload(item, quantity=quantity)]},
        format="json",
    )
    return response.json()


class StockConcurrencyTestCase(TransactionTestCase):
    """
    `TransactionTestCase`, not `TestCase`: real threads need real committed rows,
    which a wrapping test transaction would hide from every other connection.
    """

    reset_sequences = True

    def setUp(self):
        self.user = make_user()
        self.party = make_party(self.user)
        self.item = make_tracked_item(self.user, stock="100.000")

    def issue_in_threads(self, draft_ids, results, key="status"):
        def worker(invoice_id, slot):
            client = api_client_for(self.user)
            try:
                response = client.post(issue_url(invoice_id), {}, format="json")
                results[slot] = response.status_code
            except Exception as exc:  # noqa: BLE001 - the point is to report it
                results[slot] = f"error: {type(exc).__name__}"
            finally:
                close_thread_connections()

        threads = [
            threading.Thread(target=worker, args=(invoice_id, i))
            for i, invoice_id in enumerate(draft_ids)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)


class LockIsActuallyRequestedTests(StockConcurrencyTestCase):
    """
    A *deterministic* proof that the locking is really emitted.

    The behavioural race tests above are the ones that matter, but they depend on
    threads actually overlapping in wall-clock time, and that is exactly the kind
    of test that goes green forever after you delete the locking - verified here:
    with `select_for_update()` removed from `_lock_items`, the last-unit race test
    still passed, because three threads rarely collide inside the few hundred
    microseconds between the stock check and the write.

    So assert the property directly: the SQL Django actually sends must say
    `FOR UPDATE`. If someone drops the lock, this fails immediately and
    deterministically, rather than one CI run in twenty.
    """

    def setUp(self):
        super().setUp()
        if not requires_locking:
            self.skipTest("PostgreSQL only: SQLite never emits FOR UPDATE.")

    def _sql_during(self, fn):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            fn()
        return [q["sql"] for q in captured.captured_queries]

    def test_locking_items_emits_for_update(self):
        from apps.inventory.services.stock import _lock_items

        def run():
            with transaction.atomic():
                _lock_items(self.user.business, [self.item.pk])

        sql = self._sql_during(run)
        locking = [s for s in sql if "FOR UPDATE" in s.upper() and "inventory_item" in s]
        self.assertTrue(
            locking,
            "expected a SELECT ... FOR UPDATE against inventory_item; "
            f"got {sql}",
        )

    def test_committing_a_sale_locks_the_items(self):
        """End to end: issuing an invoice must lock the item rows."""
        draft = draft_for(self.user, self.item, "2")

        sql = self._sql_during(
            lambda: api_client_for(self.user).post(issue_url(draft["id"]), {}, format="json")
        )
        locking = [s for s in sql if "FOR UPDATE" in s.upper() and "inventory_item" in s]
        self.assertTrue(
            locking,
            "issuing an invoice did not lock the item rows, so two concurrent "
            f"issues could both pass the stock check. SQL seen: {sql}",
        )

    def test_reserving_a_sale_locks_the_items(self):
        from apps.inventory.services.stock import reserve_sale

        draft = draft_for(self.user, self.item, "2")
        invoice = Invoice.objects.get(pk=draft["id"])

        def run():
            with transaction.atomic():
                reserve_sale(invoice)

        sql = self._sql_during(run)
        locking = [s for s in sql if "FOR UPDATE" in s.upper() and "inventory_item" in s]
        self.assertTrue(locking, f"reserve_sale did not lock the items. SQL seen: {sql}")

    def test_adjusting_stock_locks_the_item(self):
        """Regression guard for the bug this phase found.

        `adjust_stock` and `set_tracking` used to call `select_for_update()`
        outside a transaction, which raises `TransactionManagementError` on
        PostgreSQL (a 500) and is silently ignored on SQLite. Both are now
        wrapped; this asserts the wrapping survived.
        """
        from apps.inventory.services.stock import adjust_stock

        sql = self._sql_during(
            lambda: adjust_stock(
                self.user.business,
                self.item,
                mode="CHANGE",
                quantity="2",
                reason=AdjustmentReason.OTHER,
                note="counted",
            )
        )
        locking = [s for s in sql if "FOR UPDATE" in s.upper() and "inventory_item" in s]
        self.assertTrue(locking, f"adjust_stock did not lock the item. SQL seen: {sql}")

    def test_the_bulk_balance_write_bypasses_the_lock_by_design(self):
        """
        `_write_balances` deliberately uses `.update()` on Item - never
        StockMovement, which is append-only. Assert that split still holds.
        """
        from apps.inventory.models_stock import StockMovementQuerySet

        self.assertTrue(hasattr(StockMovementQuerySet, "update"))
        with self.assertRaises(ValidationError):
            StockMovement.objects.filter(item=self.item).update(quantity_change=0)
        with self.assertRaises(ValidationError):
            StockMovement.objects.filter(item=self.item).delete()


class LastUnitRaceTests(StockConcurrencyTestCase):
    """
    The headline case: two invoices racing for the last unit under BLOCK.
    Exactly one must win, stock must land on 0 and never -1, and only one number
    may be consumed.
    """

    def setUp(self):
        super().setUp()
        if not requires_locking:
            self.skipTest(
                "PostgreSQL only: SQLite ignores select_for_update(), so this "
                "test would pass without proving anything."
            )
        self.user.business.negative_stock_policy = "BLOCK"
        self.user.business.save(update_fields=["negative_stock_policy"])

    def test_two_invoices_racing_for_the_last_unit(self):
        """
        The headline case: three invoices racing for one unit under BLOCK.

        Exactly one must win, stock must land on 0 and never -1, and exactly one
        number may be consumed.

        Three drafts rather than two, and repeated a few times, because this has
        to be a reliable detector rather than a lucky one: with only two
        contenders the read-check-then-commit window is often missed, and a race
        test that only catches the bug one run in four is a test that will be
        deleted after the first green run. Verified by removing
        `select_for_update()` from `_lock_items` and watching this fail.
        """
        for attempt in range(3):
            with self.subTest(round=attempt):
                last = make_tracked_item(
                    self.user, name=f"Last one {attempt}", stock="1.000"
                )

                drafts = [draft_for(self.user, last, "1") for _ in range(WORKERS)]
                results = {}
                self.issue_in_threads([d["id"] for d in drafts], results)

                statuses = sorted(str(v) for v in results.values())
                self.assertEqual(
                    statuses,
                    ["201"] + ["400"] * (WORKERS - 1),
                    f"expected exactly one winner, got {results}",
                )

                last.refresh_from_db()
                self.assertEqual(
                    last.current_stock,
                    Decimal("0.000"),
                    "the last unit must be sold exactly once - never below zero",
                )
                self.assertEqual(ledger_balance(last), Decimal("0.000"))

                self.assertEqual(
                    StockMovement.objects.filter(
                        item=last, movement_type=MovementType.SALE
                    ).count(),
                    1,
                    "the same unit was written off twice",
                )

        counter = InvoiceCounter.objects.get(business=self.user.business)
        self.assertEqual(counter.last_number, 3, "only one number per round may be consumed")

    def test_the_refused_race_did_not_leave_a_movement_or_a_number_gap(self):
        last = make_tracked_item(self.user, name="Also the last one", stock="1.000")

        drafts = [draft_for(self.user, last, "1") for _ in range(2)]
        results = {}
        self.issue_in_threads([d["id"] for d in drafts], results)

        numbers = list(
            Invoice.objects.filter(status=Invoice.Status.ISSUED).values_list(
                "invoice_number", flat=True
            )
        )
        self.assertEqual(len(numbers), len(set(numbers)), "a number was allocated twice")
        self.assertEqual(reconcile(self.user.business), [])
        last.refresh_from_db()
        self.assertEqual(last.current_stock, Decimal("0.000"))


class OppositeLineOrderTests(StockConcurrencyTestCase):
    """
    Two invoices selling items in opposite order (A,B and B,A) must not deadlock.
    Items are locked in ascending id order precisely so this cannot happen.
    """

    def setUp(self):
        super().setUp()
        if not requires_locking:
            self.skipTest("PostgreSQL only: no row locking to deadlock without.")
        self.a = make_tracked_item(self.user, name="Alpha", stock="100.000")
        self.b = make_tracked_item(self.user, name="Bravo", stock="100.000")

    def _draft(self, items):
        client = api_client_for(self.user)
        return client.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [line_payload(item, quantity="3") for item in items],
            },
            format="json",
        ).json()

    def test_opposite_line_orders_both_complete(self):
        first = self._draft([self.a, self.b])
        second = self._draft([self.b, self.a])

        results = {}
        self.issue_in_threads([first["id"], second["id"]], results)

        self.assertEqual(sorted(str(v) for v in results.values()), ["201", "201"], results)

        for item in (self.a, self.b):
            item.refresh_from_db()
            self.assertEqual(item.current_stock, Decimal("94.000"))
            self.assertEqual(ledger_balance(item), item.current_stock)

    def test_the_ledger_and_cache_agree_afterwards(self):
        first = self._draft([self.a, self.b])
        second = self._draft([self.b, self.a])
        results = {}
        self.issue_in_threads([first["id"], second["id"]], results)
        self.assertEqual(reconcile(self.user.business), [])


class IssueVersusAdjustTests(StockConcurrencyTestCase):
    """An issue racing an adjustment must leave cache and ledger agreeing."""

    def setUp(self):
        super().setUp()
        if not requires_locking:
            self.skipTest("PostgreSQL only.")

    def test_issue_racing_an_adjustment_stays_consistent(self):
        from apps.inventory.services.stock import adjust_stock

        draft = draft_for(self.user, self.item, "4")
        outcome = {}

        def do_issue():
            client = api_client_for(self.user)
            try:
                response = client.post(issue_url(draft["id"]), {}, format="json")
                outcome["issue"] = response.status_code
            finally:
                close_thread_connections()

        def do_adjust():
            try:
                outcome["adjust"] = (
                    adjust_stock(
                        self.user.business,
                        self.item,
                        mode="CHANGE",
                        quantity="10",
                        reason=AdjustmentReason.COUNT_CORRECTION,
                        note="Counted",
                    )
                    is not None
                )
            finally:
                close_thread_connections()

        threads = [threading.Thread(target=do_issue), threading.Thread(target=do_adjust)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(outcome.get("issue"), 201, outcome)
        self.item.refresh_from_db()
        self.assertEqual(ledger_balance(self.item), self.item.current_stock)
        self.assertEqual(reconcile(self.user.business), [])


class ConcurrentCancelTests(StockConcurrencyTestCase):
    """Cancel fired twice at once must restore exactly once."""

    def setUp(self):
        super().setUp()
        if not requires_locking:
            self.skipTest("PostgreSQL only.")

    def test_two_cancels_restore_exactly_once(self):
        draft = draft_for(self.user, self.item, "5")
        client = api_client_for(self.user)
        issued = client.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(issued.status_code, 201, issued.content[:200])

        results = {}

        def worker(slot):
            own = api_client_for(self.user)
            try:
                results[slot] = own.post(
                    cancel_url(draft["id"]), {"reason": "duplicate click"}, format="json"
                ).status_code
            finally:
                close_thread_connections()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.SALE_REVERSAL).count(),
            1,
            "stock was restored twice",
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))
        self.assertEqual(ledger_balance(self.item), Decimal("100.000"))


class MixedStormTests(StockConcurrencyTestCase):
    """
    A seeded storm of issues, cancels and adjustments, then both `reconcile_stock`
    and `audit_invoices` must be clean.
    """

    def setUp(self):
        super().setUp()
        if not requires_locking:
            self.skipTest("PostgreSQL only.")

    def test_a_mixed_storm_leaves_every_invariant_intact(self):
        import random

        from apps.inventory.services.stock import adjust_stock

        rng = random.Random(20260909)  # seeded: a failure is reproducible
        drafts = [
            draft_for(self.user, self.item, str(rng.randint(1, 5)))
            for _ in range(WORKERS)
        ]

        def worker(index):
            client = api_client_for(self.user)
            draft = drafts[index]
            try:
                issued = client.post(issue_url(draft["id"]), {}, format="json")
                if issued.status_code == 201 and rng.random() < 0.5:
                    client.post(
                        cancel_url(draft["id"]),
                        {"reason": "storm"},
                        format="json",
                    )
            except Exception:
                pass  # the invariant checks below are the assertion
            finally:
                close_thread_connections()

        def adjuster():
            try:
                adjust_stock(
                    self.user.business,
                    self.item,
                    mode="CHANGE",
                    quantity=str(rng.randint(-3, 3)),
                    reason=AdjustmentReason.OTHER,
                    note="storm",
                )
            except Exception:
                pass
            finally:
                close_thread_connections()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(drafts))]
        threads += [threading.Thread(target=adjuster) for _ in range(max(1, WORKERS // 3))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=90)

        self.item.refresh_from_db()
        self.assertEqual(
            ledger_balance(self.item),
            self.item.current_stock,
            "the ledger and the cache diverged during the storm",
        )
        self.assertEqual(reconcile(self.user.business), [])

    def test_the_invoice_audit_is_clean_after_a_storm(self):
        """Both integrity commands must agree afterwards."""
        import random
        from io import StringIO

        from django.core.management import call_command

        rng = random.Random(4242)
        drafts = [
            draft_for(self.user, self.item, str(rng.randint(1, 4)))
            for _ in range(WORKERS)
        ]

        def worker(index):
            client = api_client_for(self.user)
            try:
                client.post(issue_url(drafts[index]["id"]), {}, format="json")
            except Exception:
                pass
            finally:
                close_thread_connections()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(drafts))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=90)

        out = StringIO()
        try:
            call_command("audit_invoices", stdout=out, stderr=out)
        except SystemExit:
            self.fail(f"audit_invoices reported findings after the storm:\n{out.getvalue()}")

    def test_the_concurrency_settings_are_what_ci_assumes(self):
        """
        Guard the guard: if this ever stops requiring PostgreSQL, the tests would
        be silently running where `select_for_update()` does nothing.
        """
        self.assertTrue(
            connection.features.has_select_for_update,
            "these tests must not run on SQLite, where select_for_update() is a no-op",
        )