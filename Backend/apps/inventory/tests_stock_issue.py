"""
Step 2.1.4: the ledger wired into issue/cancel, over the public API.

The plan's central requirement for this step:

    "**`BLOCK` failure leaves everything unchanged** - invoice still `DRAFT`,
    `InvoiceCounter.last_number` unchanged, stock unchanged, zero movements."

That is the property worth the most tests, because the two-phase design exists
precisely so a failed stock check cannot burn an invoice number.
"""

from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase
from django.urls import NoReverseMatch

from apps.inventory.models import Item, ItemType
from apps.inventory.models_stock import AdjustmentReason, MovementType, StockMovement
from apps.inventory.services.stock import ledger_balance, reconcile
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


class IssueStockTestCase(TestCase):
    def setUp(self):
        self.user = make_user()
        self.business = self.user.business
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)  # tracked PRODUCT, stock 100
        self.open_item()

    def open_item(self, stock="100.000"):
        """Give the fixture item its OPENING ledger row, as the cutover did."""
        amount = Decimal(stock)
        StockMovement.objects.create(
            business=self.business,
            item=self.item,
            movement_type=MovementType.OPENING,
            quantity_change=amount,
            stock_after=amount,
            source_type="SYSTEM",
            source_label="TEST OPENING",
            reason=AdjustmentReason.COUNT_CORRECTION,
        )
        self.assertEqual(ledger_balance(self.item), amount)

    def draft(self, quantity="2", item=None):
        return self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [line_payload(item or self.item, quantity=quantity)],
            },
            format="json",
        ).json()

    def issue(self, draft_id):
        return self.client_.post(issue_url(draft_id), {}, format="json")

    def sales(self):
        return StockMovement.objects.filter(movement_type=MovementType.SALE)


class IssueDeductsStockTests(IssueStockTestCase):
    def test_issuing_deducts_the_sold_quantity(self):
        draft = self.draft(quantity="3")
        self.assertEqual(self.issue(draft["id"]).status_code, 201)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("97.000"))

    def test_the_ledger_stays_in_sync(self):
        self.issue(self.draft(quantity="3")["id"])
        self.item.refresh_from_db()
        self.assertEqual(ledger_balance(self.item), self.item.current_stock)

    def test_the_draft_itself_moves_nothing(self):
        """Decision 31: a draft is not a sale."""
        self.draft(quantity="3")
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))
        self.assertEqual(self.sales().count(), 0)

    def test_preview_moves_nothing(self):
        self.client_.post(
            "/api/v1/invoices/preview/",
            {"party": self.party.pk, "items": [line_payload(self.item, quantity="5")]},
            format="json",
        )
        self.assertEqual(self.sales().count(), 0)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))

    def test_a_service_does_not_move_stock(self):
        service = Item.objects.create(
            business=self.business,
            name="Advisory",
            item_type=ItemType.SERVICE,
            unit="HRS",
            hsn_sac_code="998311",
            service_description="Advisory",
            sales_price=Decimal("500.00"),
            tax_rate=Decimal("18.00"),
            current_stock=None,
            track_stock=False,
        )
        draft = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(service, quantity="5")]},
            format="json",
        ).json()
        self.assertEqual(self.issue(draft["id"]).status_code, 201)
        self.assertEqual(StockMovement.objects.filter(item=service).count(), 0)

    def test_a_free_text_line_does_not_move_stock(self):
        draft = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [
                    {
                        "item_name": "Freight",
                        "item_type": "SERVICE",
                        "hsn_sac_code": "996511",
                        "unit": "NOS",
                        "service_description": "Road freight",
                        "quantity": "1",
                        "unit_price": "80.00",
                        "tax_rate": "18.00",
                    }
                ],
            },
            format="json",
        ).json()
        self.assertEqual(self.issue(draft["id"]).status_code, 201)
        self.assertEqual(self.sales().count(), 0)


class BlockPolicyTests(IssueStockTestCase):
    def setUp(self):
        super().setUp()
        self.business.negative_stock_policy = "BLOCK"
        self.business.save(update_fields=["negative_stock_policy"])

    def test_a_short_sale_is_refused_with_the_item_detail(self):
        draft = self.draft(quantity="500")
        response = self.issue(draft["id"])
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"], "INSUFFICIENT_STOCK")
        self.assertTrue(body["details"], "the UI needs to know which item and by how much")
        self.assertEqual(body["details"][0]["item_id"], self.item.pk)
        self.assertEqual(body["details"][0]["requested"], "500.000")
        self.assertEqual(body["details"][0]["available"], "100.000")

    def test_a_refused_sale_changes_nothing(self):
        """The plan's explicit requirement, asserted piece by piece."""
        draft = self.draft(quantity="500")
        self.assertEqual(self.issue(draft["id"]).status_code, 400)

        # Invoice still a draft.
        self.assertEqual(
            Invoice.objects.get(pk=draft["id"]).status, Invoice.Status.DRAFT
        )
        # No number burned.
        counter = InvoiceCounter.objects.filter(business=self.business).first()
        self.assertIsNone(counter, "the counter row was created by a failed sale")
        # Stock untouched.
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))
        # No movements.
        self.assertEqual(self.sales().count(), 0)

    def test_a_sale_within_stock_succeeds(self):
        response = self.issue(self.draft(quantity="5")["id"])
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("95.000"))

    def test_selling_the_last_unit_is_allowed(self):
        """Exactly enough is not a shortage."""
        self.assertEqual(self.issue(self.draft(quantity="100")["id"]).status_code, 201)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("0.000"))

    def test_the_same_item_on_two_lines_is_checked_as_the_sum(self):
        """
        Decision 38. Three lines of 40 against stock of 100 look fine on their
        own and sell 120; only a per-item sum catches that.
        """
        draft = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [
                    line_payload(self.item, quantity="40"),
                    line_payload(self.item, quantity="40"),
                    line_payload(self.item, quantity="40"),
                ],
            },
            format="json",
        ).json()
        response = self.issue(draft["id"])
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["error"], "INSUFFICIENT_STOCK")
        self.assertEqual(response.json()["details"][0]["short_by"], "20.000")


class AllowPolicyTests(IssueStockTestCase):
    def test_a_short_sale_goes_through_and_warns(self):
        draft = self.draft(quantity="500")
        response = self.issue(draft["id"])
        self.assertEqual(response.status_code, 201, response.content[:300])

        body = response.json()
        self.assertIn("stock_warnings", body, "the user must be told, not discover it later")
        warning = body["stock_warnings"][0]
        self.assertEqual(warning["item_id"], self.item.pk)
        self.assertEqual(warning["stock_after"], "-400.000")
        self.assertEqual(warning["item_name"], self.item.name)

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("-400.000"))
        self.assertEqual(ledger_balance(self.item), self.item.current_stock)

    def test_no_warning_when_stock_is_sufficient(self):
        body = self.issue(self.draft(quantity="5")["id"]).json()
        self.assertNotIn("stock_warnings", body)


class CancellingRestoresStockTests(IssueStockTestCase):
    def cancel(self, invoice_id):
        return self.client_.post(
            cancel_url(invoice_id), {"reason": "Wrong item"}, format="json"
        )

    def test_cancelling_restores_the_stock(self):
        issued = self.issue(self.draft(quantity="7")["id"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("93.000"))

        response = self.cancel(issued.json()["id"])
        self.assertEqual(response.status_code, 200, response.content[:300])

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))
        self.assertEqual(ledger_balance(self.item), self.item.current_stock)

    def test_the_reversal_is_recorded_as_a_movement(self):
        issued = self.issue(self.draft(quantity="7")["id"])
        self.cancel(issued.json()["id"])
        reversals = StockMovement.objects.filter(
            movement_type=MovementType.SALE_REVERSAL
        )
        self.assertEqual(reversals.count(), 1)
        self.assertEqual(reversals.get().quantity_change, Decimal("7.000"))

    def test_cancelling_twice_does_not_double_restore(self):
        issued = self.issue(self.draft(quantity="7")["id"])
        self.cancel(issued.json()["id"])
        # The second cancel is refused by the lifecycle gate, before stock.
        second = self.cancel(issued.json()["id"])
        self.assertEqual(second.status_code, 409)

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))

    def test_cancelling_a_draft_moves_nothing(self):
        draft = self.draft(quantity="4")
        response = self.client_.delete(f"{INVOICE_LIST_URL}{draft['id']}/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.sales().count(), 0)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))


class IdempotentIssueTests(IssueStockTestCase):
    def test_issuing_twice_deducts_once(self):
        """A double-click must not deduct twice."""
        draft = self.draft(quantity="4")
        first = self.issue(draft["id"])
        second = self.issue(draft["id"])

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200, "a repeat issue is idempotent")
        self.assertEqual(second.json()["invoice_number"], first.json()["invoice_number"])

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("96.000"))
        self.assertEqual(self.sales().count(), 1)


class StockApiTests(IssueStockTestCase):
    def test_stock_history_returns_the_running_balance(self):
        self.issue(self.draft(quantity="3")["id"])
        response = self.client_.get(f"/api/v1/items/{self.item.pk}/stock-history/")
        self.assertEqual(response.status_code, 200, response.content[:200])
        body = response.json()
        self.assertEqual(body["count"], 2)  # OPENING + SALE
        sale = next(m for m in body["movements"] if m["movement_type"] == "SALE")
        self.assertEqual(sale["quantity_change"], "-3.000")
        self.assertEqual(sale["stock_after"], "97.000")

    def test_stock_history_is_tenant_scoped(self):
        rival = make_user("rival@stock.test")
        response = self.client_.get(f"/api/v1/items/{self.item.pk}/stock-history/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            all(
                m["source_label"] != "OTHER"
                for m in response.json()["movements"]
            )
        )
        del rival

    def test_adjusting_stock_writes_a_movement(self):
        response = self.client_.post(
            f"/api/v1/items/{self.item.pk}/adjust-stock/",
            {
                "mode": "CHANGE",
                "quantity": "-5",
                "reason": "DAMAGED",
                "note": "Two broken",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["current_stock"], "95.000")
        self.item.refresh_from_db()
        self.assertEqual(ledger_balance(self.item), self.item.current_stock)

    def test_editing_current_stock_directly_is_refused(self):
        """Decision 34: that is what would break reconcile_stock for ever."""
        response = self.client_.patch(
            f"/api/v1/items/{self.item.pk}/",
            {"current_stock": "999"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("current_stock", response.json()["errors"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("100.000"))

    def test_resending_the_same_stock_value_is_tolerated(self):
        """HTML forms re-send every field; refusing that breaks the edit form."""
        response = self.client_.patch(
            f"/api/v1/items/{self.item.pk}/",
            {"name": "Renamed", "current_stock": "100.000"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["name"], "Renamed")

    def test_a_unit_change_is_locked_once_there_is_history(self):
        """Decision 39: PCS -> KG would reinterpret every past quantity."""
        response = self.client_.patch(
            f"/api/v1/items/{self.item.pk}/",
            {"unit": "KG"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("unit", response.json()["errors"])

    def test_business_wide_movements_endpoint(self):
        self.issue(self.draft(quantity="2")["id"])
        response = self.client_.get("/api/v1/stock/movements/")
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertTrue(response.json()["movements"])

    def test_the_movements_list_has_no_write_routes(self):
        """
        The ledger is append-only, so it must have no create/update/delete route.

        Asserted on the router registry rather than by poking URLs one at a time,
        so a write route added later under any verb is caught too. Then each verb
        is tried for real, because a route that exists but 405s is still a route
        someone could later "fix" by adding a permission.
        """
        from django.urls import reverse

        from apps.inventory.urls import router

        basenames = {basename for _, _, basename in router.registry}
        self.assertIn("stockmovement", basenames)

        self.assertEqual(reverse("stockmovement-list"), "/api/v1/stock/movements/")

        for url_name in (
            "stockmovement-create",
            "stockmovement-update",
            "stockmovement-delete",
        ):
            with self.assertRaises(NoReverseMatch, msg=url_name):
                reverse(url_name, kwargs={"pk": 1})

        for method, url in (
            ("post", "/api/v1/stock/movements/"),
            ("put", "/api/v1/stock/movements/1/"),
            ("patch", "/api/v1/stock/movements/1/"),
            ("delete", "/api/v1/stock/movements/1/"),
        ):
            response = getattr(self.client_, method)(url, {}, format="json")
            self.assertEqual(
                response.status_code,
                405,
                f"{method.upper()} {url} was not rejected by the append-only ledger",
            )

    def test_negative_stock_filter_lists_only_tracked_items_below_zero(self):
        """
        `?negative_stock=true` is the "needs attention" list.

        Untracked items must not appear: their balance is meaningless, so
        including them would bury the real problems under every service.
        """
        untracked = make_item(
            self.user, code="SVC-1", name="Consulting", track_stock=False
        )
        healthy = make_item(self.user, code="OK-1", name="Healthy", track_stock=True)
        broken = make_item(self.user, code="NEG-1", name="Oversold", track_stock=True)

        broken.current_stock = Decimal("-4.000")
        broken.save(update_fields=["current_stock"])
        untracked.current_stock = Decimal("-99.000")
        untracked.save(update_fields=["current_stock"])
        del healthy

        response = self.client_.get("/api/v1/items/?negative_stock=true")
        self.assertEqual(response.status_code, 200, response.content[:200])
        names = {row["name"] for row in response.json()["results"]}
        self.assertIn("Oversold", names)
        self.assertNotIn("Consulting", names, "an untracked service is not oversold")

    def test_a_nonsense_negative_stock_filter_is_ignored(self):
        """
        A typo must not silently return an empty list.

        "no items need attention" and "I did not understand your filter" are
        different messages, and only one of them is true. Absent or unrecognised
        values leave the list untouched.
        """
        response = self.client_.get("/api/v1/items/?negative_stock=yes-please")
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertTrue(
            response.json()["results"],
            "an unrecognised filter value must not filter anything out",
        )

    def test_unit_locked_is_reported_so_the_ui_can_disable_the_control(self):
        """
        `unit_locked` lets the form freeze the unit and say why.

        Without it the user fills the unit in, hits Save and is told the unit
        cannot change - which reads as a broken app rather than a deliberate
        rule. The flag must flip as soon as the ledger gains its first row, and
        the server must still refuse the change: advertising a lock without
        enforcing it is worse than not advertising it.
        """
        client = self.client_
        item = make_item(self.user, code="LOCK-1", name="Lockable", track_stock=True)

        before = client.get(f"/api/v1/items/{item.pk}/").json()
        self.assertFalse(
            before["unit_locked"],
            "a brand-new item has no history, so its unit must be editable",
        )

        adjustment = client.post(
            f"/api/v1/items/{item.pk}/adjust-stock/",
            {
                "mode": "SET",
                "quantity": "10.000",
                "reason": AdjustmentReason.COUNT_CORRECTION,
            },
            format="json",
        )
        self.assertEqual(adjustment.status_code, 200, adjustment.content[:200])

        after = client.get(f"/api/v1/items/{item.pk}/").json()
        self.assertTrue(
            after["unit_locked"],
            "an item with ledger history must report its unit as locked",
        )

        refused = client.patch(
            f"/api/v1/items/{item.pk}/", {"unit": "KG"}, format="json"
        )
        self.assertEqual(refused.status_code, 400, refused.content[:200])
        self.assertIn("unit", refused.json()["errors"])

    def test_another_tenants_item_cannot_be_adjusted(self):
        """Target the RIVAL's item, not our own - otherwise the test proves nothing."""
        rival_user = make_user("rival2@stock.test")
        rival_item = make_item(
            rival_user, code="RIVAL-1", name="Theirs", track_stock=True
        )

        response = self.client_.post(
            f"/api/v1/items/{rival_item.pk}/adjust-stock/",
            {"mode": "CHANGE", "quantity": "-1", "reason": "DAMAGED"},
            format="json",
        )
        self.assertIn(response.status_code, (403, 404))
        rival_item.refresh_from_db()
        self.assertEqual(rival_item.current_stock, Decimal("100.000"))


class ReconcileAfterEverythingTests(IssueStockTestCase):
    """
    The end-to-end guarantee: after a realistic mix of operations, the cache
    still equals the ledger.
    """

    def test_a_sequence_of_operations_leaves_the_ledger_in_sync(self):
        from apps.inventory.services.stock import adjust_stock

        self.issue(self.draft(quantity="5")["id"])
        self.issue(self.draft(quantity="3")["id"])
        adjust_stock(
            self.business,
            self.item,
            mode="CHANGE",
            quantity="-2",
            reason=AdjustmentReason.DAMAGED,
            note="Broken in transit",
        )
        cancelled = self.issue(self.draft(quantity="4")["id"])
        self.client_.post(
            cancel_url(cancelled.json()["id"]),
            {"reason": "Customer changed their mind"},
            format="json",
        )

        self.item.refresh_from_db()
        self.assertEqual(ledger_balance(self.item), self.item.current_stock)
        self.assertEqual(reconcile(self.business), [])

    def test_the_whole_ledger_sums_to_the_balance(self):
        self.issue(self.draft(quantity="9")["id"])
        total = StockMovement.objects.filter(item=self.item).aggregate(
            total=Sum("quantity_change")
        )["total"]
        self.item.refresh_from_db()
        self.assertEqual(total, self.item.current_stock)