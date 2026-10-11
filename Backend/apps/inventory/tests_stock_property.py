"""
Property test for the stock ledger (tasks.md 2.1.4).

The invariant under test:

    for every tracked item, at every moment:
        Item.current_stock == SUM(StockMovement.quantity_change)

and under `BLOCK`, a tracked item's balance is never below zero.

Why a property test and not more examples
------------------------------------------
The concurrency tests prove that two specific races behave. They cannot show that
*no* sequence of operations breaks the ledger - there are too many orderings, and
the interesting ones are the ones nobody thought to write down. So this drives a
seeded random sequence of issues, cancels and adjustments and checks the
invariant after every single step. When it fails it prints the seed, and the same
seed replays exactly, because the RNG is seeded per test rather than drawn from
`random` global state.

Seeding is the whole trick. Without it a failure is a ghost: it happens once, on
your machine, in a CI run nobody can reproduce. With it, one line reproduces the
exact sequence.
"""

import random
from decimal import Decimal

from django.test import TestCase

from apps.inventory.models_stock import AdjustmentReason, MovementType, StockMovement
from apps.inventory.services.stock import adjust_stock, ledger_balance
from apps.invoices.models import Invoice, InvoiceCounter
from apps.invoices.tests.base import (
    INVOICE_LIST_URL,
    api_client_for,
    line_payload,
    make_item,
    make_party,
    make_user,
)
from apps.invoices.tests.test_issue_api import cancel_url, issue_url

SEEDS = [1, 7, 42, 1234, 98765]


class StockInvariantPropertyTests(TestCase):
    """
    `TestCase`, not `TransactionTestCase`: this asserts on invariants, not on
    locking, so it runs anywhere - including the fast SQLite suite - and a
    failure here is a logic bug rather than a timing artefact.
    """

    def setUp(self):
        self.user = make_user()
        self.party = make_party(self.user)
        self.item = make_item(self.user, code="PROP-1", name="Prop", track_stock=True)
        StockMovement.objects.create(
            business=self.user.business,
            item=self.item,
            movement_type=MovementType.OPENING,
            quantity_change=Decimal("100.000"),
            stock_after=Decimal("100.000"),
            source_type="SYSTEM",
            source_label="OPENING",
            reason=AdjustmentReason.COUNT_CORRECTION,
        )
        self.item.refresh_from_db()

    def new_fixture(self, label):
        """
        A fresh business + item for one seed.

        Deliberately a new business per seed rather than re-running `setUp()`:
        re-calling `setUp()` would create a second user with the same email and
        trip the uniqueness constraint, and if that ever stopped being unique the
        test would quietly start sharing state between seeds.
        """
        user = make_user(f"prop{label}@stock.test")
        item = make_item(user, code=f"PROP-{label}", name=f"Prop {label}", track_stock=True)
        StockMovement.objects.create(
            business=user.business,
            item=item,
            movement_type=MovementType.OPENING,
            quantity_change=Decimal("100.000"),
            stock_after=Decimal("100.000"),
            source_type="SYSTEM",
            source_label="OPENING",
            reason=AdjustmentReason.COUNT_CORRECTION,
        )
        return user, make_party(user), item

    # -- helpers ---------------------------------------------------------

    def assert_invariant(self, business, step, seed, extra=""):
        """
        Check the ledger against the cache for every tracked item.

        Checked as equality rather than as a difference, so a drift of 0.001 -
        which a shopkeeper would never notice but which is exactly the kind of
        error that compounds over a year - fails here.
        """
        from apps.inventory.models import Item

        for item in Item.objects.filter(business=business, track_stock=True):
            expected = ledger_balance(item)
            self.assertEqual(
                item.current_stock,
                expected,
                f"seed={seed} step={step} {extra}: {item.name} cache="
                f"{item.current_stock} ledger={expected}",
            )

    def new_draft(self, party, item, quantity):
        client = api_client_for(self.user)
        response = client.post(
            INVOICE_LIST_URL,
            {
                "party": party.pk,
                "items": [line_payload(item, quantity=str(quantity))],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content[:300])
        return response.json()["id"]

    def do_issue(self, invoice_id):
        return api_client_for(self.user).post(issue_url(invoice_id), {}, format="json")

    def do_cancel(self, invoice_id):
        return api_client_for(self.user).post(
            cancel_url(invoice_id), {"reason": "property test"}, format="json"
        )

    # -- the property ----------------------------------------------------

    def test_random_operations_keep_the_ledger_and_cache_in_step(self):
        """
        15 random operations from a seeded RNG. Every step re-checks the
        invariant and, under BLOCK, that the balance never went below zero.

        Run for five seeds. One seed can pass by luck - the point of several is
        that a genuine logic bug shows up in at least one of them, while a
        correct implementation passes all five.
        """
        for seed in SEEDS:
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                self.user, party, self.item = self.new_fixture(seed)
                business = self.user.business

                business.negative_stock_policy = "BLOCK"
                business.save(update_fields=["negative_stock_policy"])

                issued: list[int] = []
                lowest_seen = Decimal("0")

                for step in range(15):
                    action = rng.choices(
                        ["issue", "cancel", "adjust"],
                        weights=[5, 2, 3],
                    )[0]

                    if action == "issue":
                        quantity = rng.randint(1, 30)
                        invoice_id = self.new_draft(party, self.item, quantity)
                        response = self.do_issue(invoice_id)

                        if response.status_code == 201:
                            issued.append(invoice_id)
                        else:
                            # Under BLOCK a refusal must be a clean no-op. "It was
                            # refused" and "it was refused *cleanly*" are different
                            # claims, so assert the cleanliness here too.
                            self.assertEqual(
                                response.json()["error"],
                                "INSUFFICIENT_STOCK",
                                f"seed={seed} step={step}: unexpected refusal "
                                f"{response.content[:200]}",
                            )
                            self.assertEqual(
                                Invoice.objects.get(pk=invoice_id).status,
                                Invoice.Status.DRAFT,
                                f"seed={seed} step={step}: a refused issue left the "
                                "invoice out of DRAFT",
                            )

                    elif action == "cancel" and issued:
                        invoice_id = rng.choice(issued)
                        response = self.do_cancel(invoice_id)
                        self.assertEqual(response.status_code, 200, response.content[:200])
                        issued.remove(invoice_id)

                    elif action == "adjust":
                        quantity = rng.randint(-10, 10)
                        if quantity == 0:
                            continue
                        adjust_stock(
                            business,
                            self.item,
                            mode="CHANGE",
                            quantity=str(quantity),
                            reason=AdjustmentReason.OTHER,
                            note=f"seed {seed} step {step}",
                        )

                    self.item.refresh_from_db()
                    self.assert_invariant(business, step, seed)
                    lowest_seen = min(lowest_seen, self.item.current_stock)

                self.assertEqual(
                    lowest_seen,
                    Decimal("0"),
                    f"seed={seed}: the balance went negative under BLOCK "
                    f"(lowest {lowest_seen})",
                )

    def test_cancelling_everything_returns_to_the_opening_balance(self):
        """
        Issue N, cancel all N, and the balance must be exactly where it started.

        This is the property a shopkeeper notices first: sell 10 and hand them 10
        back, and the shelf count must not have moved. It also catches
        double-reversal and missed-reversal, which the invariant check alone
        cannot, because a symmetric double-reversal still leaves
        `cache == ledger` - it just leaves both wrong.
        """
        rng = random.Random(SEEDS[2])
        opening = self.item.current_stock

        issued = []
        for _ in range(6):
            invoice_id = self.new_draft(self.party, self.item, rng.randint(1, 5))
            response = self.do_issue(invoice_id)
            if response.status_code == 201:
                issued.append(invoice_id)

        self.assertTrue(issued, "no invoice issued, so this test proves nothing")

        for invoice_id in issued:
            self.assertEqual(
                self.do_cancel(invoice_id).status_code, 200, "cancel failed"
            )

        self.item.refresh_from_db()
        self.assertEqual(
            self.item.current_stock,
            opening,
            "selling and cancelling everything must leave the balance untouched",
        )
        self.assert_invariant(
            self.user.business, "after full round trip", SEEDS[2]
        )

    def test_a_rejected_issue_changes_absolutely_nothing(self):
        """
        The property that makes the two-phase design worth having.

        On a BLOCK refusal: no movement, no change to the cache, no invoice number
        consumed, invoice still DRAFT. Checked by snapshotting everything before
        and comparing after - asserting each fact separately tends to miss one.
        """
        self.user.business.negative_stock_policy = "BLOCK"
        self.user.business.save(update_fields=["negative_stock_policy"])

        invoice_id = self.new_draft(self.party, self.item, 500)  # far more than 100 in stock

        # The counter is read with `first()` rather than `get()`: on a fresh
        # business no counter row exists yet, and "no counter was created" is a
        # legitimate way for the number to go un-consumed. `get()` would raise
        # DoesNotExist and turn a passing assertion into an error.
        before = {
            "stock": self.item.current_stock,
            "movements": StockMovement.objects.filter(item=self.item).count(),
            "counter": InvoiceCounter.objects.filter(
                business=self.user.business
            ).values_list("last_number", flat=True).first(),
        }

        response = self.do_issue(invoice_id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "INSUFFICIENT_STOCK")

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, before["stock"], "stock moved")
        self.assertEqual(
            StockMovement.objects.filter(item=self.item).count(),
            before["movements"],
            "a refused issue wrote a movement",
        )
        self.assertEqual(
            InvoiceCounter.objects.filter(
                business=self.user.business
            ).values_list("last_number", flat=True).first(),
            before["counter"],
            "a refused issue burned an invoice number",
        )
        self.assertEqual(
            Invoice.objects.get(pk=invoice_id).status,
            Invoice.Status.DRAFT,
            "a refused issue advanced the invoice",
        )

    def test_the_ledger_grows_and_never_shrinks(self):
        """
        The ledger is append-only in the literal sense: cancelling adds a
        reversing movement rather than deleting the original. Asserting the row
        count only ever increases catches a future "optimisation" that deletes
        rows to keep the table small - which would silently destroy the audit
        trail that `reconcile_stock` and `audit_invoices` depend on.
        """
        rng = random.Random(SEEDS[4])
        counts = []
        for _ in range(5):
            invoice_id = self.new_draft(self.party, self.item, rng.randint(1, 5))
            if self.do_issue(invoice_id).status_code == 201:
                counts.append(StockMovement.objects.filter(item=self.item).count())
                self.do_cancel(invoice_id)
                counts.append(StockMovement.objects.filter(item=self.item).count())

        self.assertTrue(counts)
        self.assertEqual(
            counts,
            sorted(counts),
            f"the ledger shrank at some point: {counts}",
        )
        self.assertEqual(
            StockMovement.objects.filter(
                item=self.item, movement_type=MovementType.SALE
            ).count(),
            StockMovement.objects.filter(
                item=self.item, movement_type=MovementType.SALE_REVERSAL
            ).count(),
            "every sale needs exactly one reversal; the ledger keeps both",
        )