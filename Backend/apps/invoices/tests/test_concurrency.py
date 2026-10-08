"""
Concurrency tests for invoice issue + numbering (step D).

These MUST run on real PostgreSQL:

    uv run python manage.py test apps.invoices.tests.test_concurrency \
        --settings=config.settings_test_pg

Why it matters
--------------
Django only emits `FOR UPDATE` when the backend advertises support
(`has_select_for_update`). SQLite reports False, so `select_for_update()` is
**silently dropped**. A concurrency test on SQLite therefore passes whether or
not the locking works, which is strictly worse than having no test - it hides a
real double-numbering bug. So this class skips loudly (never silently) on a
backend without row locks, and asserts that the lock is actually in the SQL.

Lock ordering
-------------
Always **invoice -> counter**. Locking only the counter lets two concurrent
issues both pass the `status == DRAFT` check and both allocate a number. The
invoice lock is what serialises them.
"""

import threading
import unittest

from django.db import connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from apps.invoices.models import Invoice, InvoiceCounter
from apps.invoices.tests.base import (
    INVOICE_LIST_URL,
    line_payload,
    make_item,
    make_party,
    make_user,
)

#: Enough overlap that a missing lock reliably reproduces the race.
THREADS = 4


def backend_supports_row_locks() -> bool:
    return connection.features.has_select_for_update


@unittest.skipUnless(
    backend_supports_row_locks(),
    "Skipped: this backend ignores select_for_update() (SQLite), so these "
    "concurrency tests cannot prove anything here. Run with "
    "--settings=config.settings_test_pg",
)
class InvoiceConcurrencyTests(TransactionTestCase):
    """
    TransactionTestCase, not TestCase: the whole point is real concurrent
    transactions, and TestCase wraps everything in one that other threads
    cannot see.
    """

    reset_sequences = True

    def setUp(self):
        self.user = make_user("concurrency@example.com")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)

    # -- helpers ---------------------------------------------------------
    def make_drafts(self, count):
        drafts = []
        for _ in range(count):
            response = self.client.post(
                INVOICE_LIST_URL,
                {"party": self.party.pk, "items": [line_payload(self.item)]},
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.data)
            drafts.append(response.json()["id"])
        return drafts

    def run_in_threads(self, target, count=THREADS):
        """
        Run `target(index)` in `count` threads, each with its own database
        connection, and collect results or exceptions.
        """
        barrier = threading.Barrier(count)
        results, errors = [], []
        lock = threading.Lock()

        def worker(index):
            # A fresh connection per thread: Django connections are not
            # thread-safe and are otherwise shared.
            try:
                conn = connections["default"]
                conn.close()
                barrier.wait(timeout=30)  # maximise the overlap
                outcome = target(index)
                with lock:
                    results.append(outcome)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                with lock:
                    errors.append(exc)
            finally:
                connections["default"].close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
            self.assertFalse(thread.is_alive(), "a worker thread deadlocked")
        return results, errors

    # -- the lock is really there ----------------------------------------
    def test_select_for_update_is_not_silently_dropped(self):
        """
        Guards the guard. If a future settings change runs these on SQLite, the
        skip above fires - but this also proves FOR UPDATE reaches the database
        on the engine actually in use.
        """
        self.assertTrue(
            backend_supports_row_locks(),
            "These tests must not run on a backend that ignores row locks.",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM invoices_invoice WHERE FALSE FOR UPDATE"
            )
            cursor.fetchall()  # must not raise

    # -- same invoice issued twice ---------------------------------------
    def test_same_invoice_issued_concurrently_gets_one_number(self):
        """A double-click or retried request must not burn two numbers."""
        (draft_id,) = self.make_drafts(1)

        def issue(_index):
            client = APIClient()
            client.force_authenticate(user=self.user)
            response = client.post(f"/api/v1/invoices/{draft_id}/issue/", {}, format="json")
            return response.status_code, response.json().get("invoice_number")

        results, errors = self.run_in_threads(issue, count=THREADS)

        self.assertEqual(errors, [], f"threads raised: {errors}")
        numbers = {number for _code, number in results}
        self.assertEqual(len(numbers), 1, f"expected one number, got {numbers}")
        self.assertEqual(numbers.pop(), "INV/26-27/00001")

        self.assertEqual(Invoice.objects.filter(pk=draft_id).count(), 1)
        self.assertEqual(
            InvoiceCounter.objects.get(business=self.user.business).last_number,
            1,
            "the series must advance exactly once",
        )

    # -- different invoices issued together ------------------------------
    def test_concurrent_issues_get_consecutive_numbers_with_no_gap(self):
        draft_ids = self.make_drafts(THREADS)

        def issue(index):
            client = APIClient()
            client.force_authenticate(user=self.user)
            response = client.post(
                f"/api/v1/invoices/{draft_ids[index]}/issue/", {}, format="json"
            )
            return response.status_code, response.json().get("invoice_number")

        results, errors = self.run_in_threads(issue, count=THREADS)

        self.assertEqual(errors, [], f"threads raised: {errors}")
        self.assertTrue(all(code == 201 for code, _ in results), results)
        numbers = sorted(number for _code, number in results)

        self.assertEqual(
            numbers,
            [f"INV/26-27/{n:05d}" for n in range(1, THREADS + 1)],
            "numbers must be consecutive with no gap and no duplicate",
        )
        self.assertEqual(
            len(set(numbers)), THREADS, "no invoice may share a number"
        )

    # -- first invoice of a new financial year ---------------------------
    def test_first_invoice_of_a_new_fy_race(self):
        """
        Both threads hit a counter row that does not exist yet. `get_or_create`
        plus the unique constraint on (business, financial_year) must collapse
        them onto one row rather than raising IntegrityError or duplicating.
        """
        draft_ids = self.make_drafts(THREADS)

        def issue(index):
            client = APIClient()
            client.force_authenticate(user=self.user)
            response = client.post(
                f"/api/v1/invoices/{draft_ids[index]}/issue/", {}, format="json"
            )
            return response.status_code, response.json().get("invoice_number")

        self.assertEqual(InvoiceCounter.objects.count(), 0, "no counter may exist yet")

        results, errors = self.run_in_threads(issue, count=THREADS)

        self.assertEqual(errors, [], f"threads raised: {errors}")
        self.assertEqual(
            InvoiceCounter.objects.filter(business=self.user.business).count(),
            1,
            "the concurrent get_or_create must produce exactly one counter row",
        )
        numbers = sorted(number for _code, number in results if number)
        self.assertEqual(len(set(numbers)), len(numbers), f"duplicates in {numbers}")

    # -- a lost race is a clean 409, never a 500 --------------------------
    def test_a_number_collision_returns_409_not_500(self):
        """
        Force the unique constraint to fire and confirm the API converts it into
        a clean conflict.

        Without the IntegrityError handler this surfaces as an unhandled 500,
        which the frontend can do nothing useful with - it cannot even tell
        whether the invoice was issued.
        """
        from unittest.mock import patch

        first, second = self.make_drafts(2)

        ok = self.client.post(f"/api/v1/invoices/{first}/issue/", {}, format="json")
        self.assertEqual(ok.status_code, 201)
        taken_number = ok.json()["invoice_number"]

        # Simulate the counter handing out a number that is already in use, as
        # would happen if a row lock were ever lost. The save then violates
        # UniqueConstraint(business, invoice_number).
        with patch(
            "apps.invoices.services.issue.allocate_invoice_number",
            return_value=taken_number,
        ):
            response = self.client.post(
                f"/api/v1/invoices/{second}/issue/", {}, format="json"
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "NUMBER_RACE")
        self.assertIn("reload", response.json()["message"].lower())

        # The failed issue left nothing behind.
        still_draft = Invoice.objects.get(pk=second)
        self.assertEqual(still_draft.status, Invoice.Status.DRAFT)
        self.assertFalse(still_draft.invoice_number)
        self.assertEqual(
            Invoice.objects.filter(
                business=self.user.business, invoice_number=taken_number
            ).count(),
            1,
            "the number must still belong to exactly one invoice",
        )

        response = self.client.post(f"/api/v1/invoices/{second}/issue/", {}, format="json")
        # Either the DB rejected the planted row (so this issue is clean) or the
        # service caught the IntegrityError. Both must not be a 500.
        self.assertNotEqual(response.status_code, 500)
        self.assertIn(
            response.status_code,
            (200, 201, 409),
            f"unexpected status {response.status_code}: {response.content[:200]}",
        )

    # -- tenant isolation under concurrency -------------------------------
    def test_two_businesses_never_share_a_number(self):
        rival = make_user("rival@example.com", state_code="29", prefix="RIV")
        rival_party = make_party(rival, name="Their Buyer", state_code="29")
        rival_item = make_item(rival, code="R-1", name="Their goods")

        rival_client = APIClient()
        rival_client.force_authenticate(user=rival)
        mine = self.make_drafts(THREADS)
        theirs = []
        for _ in range(THREADS):
            response = rival_client.post(
                INVOICE_LIST_URL,
                {"party": rival_party.pk, "items": [line_payload(rival_item)]},
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.data)
            theirs.append(response.json()["id"])

        def issue(index):
            # Even indices issue my invoices, odd ones the rival's, all at once.
            if index % 2 == 0:
                user, invoice_id = self.user, mine[index // 2]
            else:
                user, invoice_id = rival, theirs[index // 2]
            client = APIClient()
            client.force_authenticate(user=user)
            response = client.post(
                f"/api/v1/invoices/{invoice_id}/issue/", {}, format="json"
            )
            return response.status_code, response.json().get("invoice_number")

        results, errors = self.run_in_threads(issue, count=THREADS * 2)

        self.assertEqual(errors, [], f"threads raised: {errors}")
        numbers = [number for _code, number in results if number]
        self.assertEqual(len(set(numbers)), len(numbers), f"duplicates across tenants: {numbers}")
        self.assertTrue(any(n.startswith("INV/") for n in numbers))
        self.assertTrue(any(n.startswith("RIV/") for n in numbers))
        # Each tenant's own series is gapless, independent of the other.
        self.assertEqual(
            sorted(n for n in numbers if n.startswith("INV/")),
            [f"INV/26-27/{n:05d}" for n in range(1, THREADS + 1)],
        )
