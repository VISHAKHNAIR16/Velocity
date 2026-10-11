"""
Purchase concurrency (2.2). **PostgreSQL only.**

Skipped loudly on SQLite, where Django advertises
`has_select_for_update = False` and silently drops `SELECT ... FOR UPDATE` - so
a concurrency test there passes whether or not the locking works. That is worse
than having no test, because it looks like proof.

The case that matters
---------------------
Posting a purchase increases stock, and two bills for the same item posted at
once must **both** apply: the ledger is append-only and a second increase is a
real, separate arrival of goods, not a duplicate. So unlike the sale race in
2.1.4 - where exactly one of two invoices must win - here the assertion is that
*both* succeed and the balance rises by the sum. Getting that wrong in either
direction is a real bug: dropping one bill loses goods, applying one twice
invents them.

The cancellation race is the opposite and is the dangerous one: two concurrent
cancels must reverse **once**, or the goods vanish.
"""

import os
import threading
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase

from apps.inventory.models_stock import MovementType, StockMovement
from apps.inventory.services.stock import ledger_balance
from apps.invoices.tests.base import api_client_for, make_user
from apps.purchases.models import Purchase, PurchaseStatus
from apps.purchases.tests_api import PURCHASES_URL, header, line_payload, make_supplier

#: Each worker holds a connection, and the pool-free endpoint these tests need
#: has a low concurrency cap. Kept small so the suite also runs on a shared dev
#: branch; CI's postgres container can take more.
WORKERS = int(os.getenv("STOCK_CONCURRENCY_WORKERS", "3"))

requires_locking = (
    connection.features.has_select_for_update
    if connection.vendor == "postgresql"
    else False
)


def close_thread_connections():
    """
    Close the connections opened inside a worker thread.

    A connection opened in one thread cannot be closed from another, so without
    this every finished worker leaves a live session behind and the suite passes
    but exits non-zero on `database "test_..." is being accessed by other users`.
    """
    from django.db import connections

    connections.close_all()


def make_tracked_item(user, *, name="Purchased Widget", stock="0.000"):
    from apps.inventory.models import Item, ItemType

    amount = Decimal(stock)
    item = Item.objects.create(
        business=user.business,
        name=name,
        item_type=ItemType.PRODUCT,
        unit="PCS",
        hsn_sac_code="610510",
        sales_price=Decimal("249.50"),
        purchase_price=Decimal("90.00"),
        tax_rate=Decimal("18.00"),
        current_stock=amount,
        track_stock=True,
    )
    if amount != Decimal("0.00"):
        StockMovement.objects.create(
            business=user.business,
            item=item,
            movement_type=MovementType.OPENING,
            quantity_change=amount,
            stock_after=amount,
            source_type="SYSTEM",
            source_label="OPENING",
            reason="COUNT_CORRECTION",
        )
    return item


class PurchaseConcurrencyTestCase(TransactionTestCase):
    """
    `TransactionTestCase`, not `TestCase`: real threads need real committed rows,
    which a wrapping test transaction would hide from every other connection.
    """

    reset_sequences = True

    def setUp(self):
        if not requires_locking:
            self.skipTest(
                "PostgreSQL only: SQLite ignores select_for_update(), so this "
                "test would pass without proving anything."
            )
        self.user = make_user()
        self.supplier = make_supplier(self.user)
        self.item = make_tracked_item(self.user)
        self.client_ = api_client_for(self.user)

    # -- helpers --------------------------------------------------------

    def draft(self, bill_number, quantity="10.000"):
        payload = (
            header(self.supplier, bill_number=bill_number)
            | {"items": [line_payload(self.item, quantity=quantity)]}
        )
        response = self.client_.post(PURCHASES_URL, payload, format="json")
        self.assertEqual(response.status_code, 201, response.content[:300])
        return response.json()["id"]

    def post_in_threads(self, purchase_ids, results):
        def worker(purchase_id, slot):
            try:
                client = api_client_for(self.user)
                response = client.post(
                    f"{PURCHASES_URL}{purchase_id}/post/", {}, format="json"
                )
                results[slot] = response.status_code
            except Exception as exc:  # noqa: BLE001 - the point is to report it
                results[slot] = f"error: {type(exc).__name__}"
            finally:
                close_thread_connections()

        threads = [
            threading.Thread(target=worker, args=(pid, i))
            for i, pid in enumerate(purchase_ids)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)


class ConcurrentPostTests(PurchaseConcurrencyTestCase):
    def test_two_bills_for_the_same_item_both_apply(self):
        """
        Both succeed, and the balance rises by the sum.

        The inverse of the sale race: goods genuinely arrived twice, so a second
        increase is correct. Losing one bill would quietly lose stock.
        """
        first = self.draft("PUR-A")
        second = self.draft("PUR-B")

        results = {}
        self.post_in_threads([first, second], results)

        self.assertEqual(
            sorted(str(v) for v in results.values()),
            ["201", "201"],
            f"both bills must post, got {results}",
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("20.000"))
        self.assertEqual(ledger_balance(self.item), Decimal("20.000"))
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.PURCHASE).count(),
            2,
        )

    def test_the_same_bill_posted_twice_concurrently_increases_stock_once(self):
        """
        The idempotency guarantee, raced.

        Two requests for one bill - a double-click or a retry - must not double
        the stock. This is the case most likely to be got wrong, because the
        status check and the commit are not the same statement.
        """
        purchase_id = self.draft("PUR-A")

        results = {}
        self.post_in_threads([purchase_id, purchase_id], results)

        self.assertEqual(
            sorted(str(v) for v in results.values()),
            ["200", "201"],
            f"one should be the original post and one the idempotent reply, got {results}",
        )

        self.item.refresh_from_db()
        self.assertEqual(
            self.item.current_stock,
            Decimal("10.000"),
            "the same bill posted twice must increase stock once",
        )
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.PURCHASE).count(),
            1,
        )

    def test_several_bills_concurrently_leave_cache_and_ledger_agreeing(self):
        drafts = [self.draft(f"PUR-{i}") for i in range(WORKERS)]
        results = {}
        self.post_in_threads(drafts, results)

        self.assertTrue(
            all(str(v) == "201" for v in results.values()),
            f"every bill should post, got {results}",
        )
        self.item.refresh_from_db()
        self.assertEqual(ledger_balance(self.item), self.item.current_stock)


class ConcurrentCancelTests(PurchaseConcurrencyTestCase):
    def _post_one(self, bill_number="PUR-A", quantity="10.000"):
        purchase_id = self.draft(bill_number, quantity)
        self.client_.post(f"{PURCHASES_URL}{purchase_id}/post/", {}, format="json")
        return purchase_id

    def test_two_cancels_reverse_the_increase_once(self):
        """
        The dangerous race. Cancelling twice removes stock twice, and the goods
        have genuinely not left twice.
        """
        purchase_id = self._post_one()

        results = {}

        def worker(slot):
            try:
                client = api_client_for(self.user)
                results[slot] = client.post(
                    f"{PURCHASES_URL}{purchase_id}/cancel/",
                    {"reason": "returned"},
                    format="json",
                ).status_code
            except Exception as exc:  # noqa: BLE001
                results[slot] = f"error: {type(exc).__name__}"
            finally:
                close_thread_connections()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.item.refresh_from_db()
        self.assertEqual(
            self.item.current_stock,
            Decimal("0.000"),
            f"cancelling twice must return the stock once, got {results}",
        )
        self.assertEqual(
            StockMovement.objects.filter(
                movement_type=MovementType.PURCHASE_REVERSAL
            ).count(),
            1,
        )

    def test_a_cancel_racing_a_post_keeps_the_ledger_consistent(self):
        """
        Post and cancel at the same time. Whichever wins, the ledger and the
        cache must still agree and the bill must be in a legal state - the two
        orders are both acceptable, an inconsistent result is not.
        """
        purchase_id = self._post_one(quantity="5.000")
        results = {}

        def worker(slot):
            try:
                client = api_client_for(self.user)
                if slot == 0:
                    results[slot] = client.post(
                        f"{PURCHASES_URL}{purchase_id}/post/", {}, format="json"
                    ).status_code
                else:
                    results[slot] = client.post(
                        f"{PURCHASES_URL}{purchase_id}/cancel/",
                        {"reason": "race"},
                        format="json",
                    ).status_code
            except Exception as exc:  # noqa: BLE001
                results[slot] = f"error: {type(exc).__name__}"
            finally:
                close_thread_connections()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        purchase = Purchase.objects.get(pk=purchase_id)
        self.assertIn(
            purchase.status,
            (PurchaseStatus.POSTED, PurchaseStatus.CANCELLED),
            f"the bill must end in a legal state, got {purchase.status} ({results})",
        )
        self.item.refresh_from_db()
        self.assertEqual(
            ledger_balance(self.item),
            self.item.current_stock,
            "the cache and the ledger must agree whatever the race decided",
        )