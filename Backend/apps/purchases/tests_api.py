"""
Purchase API and lifecycle tests (phase 2.2).

The plan for a purchase is the opposite of a sale in three ways, and each has a
test here that would fail if the code quietly reverted to sale behaviour:

  * posting **increases** stock, and the sign is the thing most easily got
    backwards - a purchase posted as a deduction is invisible until a count
    disagrees a month later;
  * a refused post must change **nothing** (no movement, no status change),
    the same guarantee 2.1.4 gave issuing an invoice;
  * cancelling takes stock **away**, so it can be refused for going negative,
    which no sale cancellation ever can.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.inventory.models_stock import MovementType, StockMovement
from apps.inventory.services.stock import ledger_balance
from apps.parties.models import Party
from apps.purchases.models import Purchase

PURCHASES_URL = "/api/v1/purchases/"
ZERO = Decimal("0.00")


def make_supplier(user, *, name="Supplier Ltd", gstin="27AAAAA0000A1Z5", state="27", party_type=Party.PartyType.SUPPLIER):
    from apps.parties.models import Party as P

    return P.objects.create(
        business=user.business,
        name=name,
        party_type=party_type,
        gstin=gstin,
        state_code=state,
        billing_address="1 Warehouse Road",
        billing_city="Mumbai",
        billing_pincode="400001",
    )


def line_payload(item=None, **overrides):
    payload = {
        "quantity": "10.000",
        "unit_price": "100.00",
        "tax_rate": "18.00",
        "line_discount": "0.00",
        "taxable_value": "1000.00",
        "cgst_amount": "0.00",
        "sgst_amount": "0.00",
        "igst_amount": "180.00",
        "total_amount": "1180.00",
        "itc_eligible": True,
    }
    if item is not None:
        payload["item"] = item.pk
    else:
        payload.update({"item_name": "Freight", "item_type": "PRODUCT", "hsn_sac_code": "996511", "unit": "BOX"})
    payload.update(overrides)
    return payload


def make_tracked_item(user, *, name="Widget", stock="0.000"):
    """
    A tracked product with a matching OPENING ledger row.

    Defined here rather than imported from another test module: those modules
    define theirs inside test classes, and reaching into them couples this file
    to a refactor three files away.
    """
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
    # An item created at zero has no OPENING row: the ledger refuses a movement
    # that changes nothing, and an opening of zero *is* nothing.
    if amount != ZERO:
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


#: Sentinel for "send no such key at all", which is distinct from sending
#: an empty string. Both used to be untested.
_OMITTED = object()


def header(supplier, **overrides):
    payload = {
        "supplier": supplier.pk,
        "bill_number": "SUP-001",
        "bill_date": timezone.localdate().isoformat(),
        "subtotal": "1000.00",
        "total_discount": "0.00",
        "taxable_total": "1000.00",
        "cgst_total": "0.00",
        "sgst_total": "0.00",
        "igst_total": "180.00",
        "round_off": "0.00",
        "grand_total": "1180.00",
    }
    payload.update(overrides)
    return payload


class PurchaseBase(TestCase):
    def setUp(self):
        from apps.invoices.tests.base import api_client_for, make_user

        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.supplier = make_supplier(self.user)
        self.item = make_tracked_item(self.user, name="Widget", stock="0.000")

    def create_draft(self, **overrides):
        items = overrides.pop("items", [line_payload(self.item)])
        # `supplier` may be overridden; passing it twice would be a TypeError.
        supplier = overrides.pop("supplier", self.supplier)
        payload = header(supplier) | overrides | {"items": items}
        response = self.client_.post(PURCHASES_URL, payload, format="json")
        self.assertEqual(response.status_code, 201, response.content[:400])
        return response.json()

    def post(self, purchase_id):
        return self.client_.post(f"{PURCHASES_URL}{purchase_id}/post/", {}, format="json")

    def _sell_all_away(self):
        """
        Simulate the goods having been resold after the purchase.

        Written straight to the ledger and the cache together, because that is
        what an issued invoice does - this test is about the *cancellation*
        afterwards, not about issuing one.
        """
        StockMovement.objects.create(
            business=self.user.business,
            item=self.item,
            movement_type=MovementType.SALE,
            quantity_change=Decimal("-10.000"),
            stock_after=ZERO,
            source_type="INVOICE",
            source_id=1,
            source_line_id=1,
            source_label="INV-1",
        )
        self.item.current_stock = ZERO
        self.item.save(update_fields=["current_stock"])

    def cancel(self, purchase_id, reason="supplier returned it"):
        return self.client_.post(
            f"{PURCHASES_URL}{purchase_id}/cancel/", {"reason": reason}, format="json"
        )


class CreateTests(PurchaseBase):
    def test_a_draft_can_be_created_with_entered_amounts(self):
        body = self.create_draft()
        self.assertEqual(body["status"], "DRAFT")
        self.assertEqual(body["grand_total"], "1180.00")
        self.assertEqual(body["items"][0]["taxable_value"], "1000.00")

    def test_the_entered_amounts_are_never_overwritten_by_the_calculator(self):
        """
        The whole point of the module. Enter a deliberately wrong total and check
        it survives untouched, while the computed mirror records what the maths
        thinks.
        """
        body = self.create_draft(grand_total="9999.99")
        self.assertEqual(
            body["grand_total"],
            "9999.99",
            "the entered figure is the truth and must not be corrected",
        )

    def test_the_computed_mirror_is_stored_alongside(self):
        # Deliberately wrong on the entered side. 10 x 100 at 18% IGST is
        # 1000 + 180 = 1180, so 1180 is *correct* and would prove nothing;
        # 1300 is a figure the calculator will disagree with.
        self.create_draft(
            grand_total="1300.00",
            items=[line_payload(self.item, total_amount="1300.00")],
        )
        purchase = Purchase.objects.get(bill_number="SUP-001")
        self.assertNotEqual(
            purchase.taxable_total_computed,
            ZERO,
            "the calculator's view is kept so drift is visible later",
        )
        self.assertTrue(purchase.has_divergence, "a mismatch must be flagged")

    def test_a_correct_bill_has_no_divergence(self):
        # 1000 + 180 IGST is arithmetically right, so nothing should be flagged.
        self.create_draft()
        purchase = Purchase.objects.get(bill_number="SUP-001")
        self.assertFalse(
            purchase.has_divergence,
            f"a correct bill was flagged: divergence {purchase.max_divergence}",
        )

    def test_the_financial_year_is_derived_and_stored(self):
        # A real past date inside FY 2026-27. Not 31 Mar 2027 - that is in the
        # future from today, and the period rules correctly refuse it.
        self.create_draft(bill_date="2026-04-01")
        purchase = Purchase.objects.get(bill_number="SUP-001")
        self.assertEqual(purchase.financial_year, "2026-27")

    def test_moving_the_bill_date_moves_its_financial_year(self):
        """
        The FY is recomputed on every save, not set once. Otherwise a bill moved
        from March to April keeps March's FY and a duplicate slips past the
        unique guard by changing only the date.
        """
        purchase_id = self.create_draft()["id"]
        purchase = Purchase.objects.get(pk=purchase_id)
        purchase.bill_date = timezone.localdate().replace(month=4, day=1)
        purchase.save(update_fields=["bill_date"])
        self.assertEqual(purchase.financial_year, "2026-27")

    def test_the_supplier_is_snapshotted(self):
        self.create_draft()
        purchase = Purchase.objects.get(bill_number="SUP-001")
        self.assertEqual(purchase.supplier_name, "Supplier Ltd")
        self.assertEqual(purchase.supplier_gstin, "27AAAAA0000A1Z5")
        self.assertEqual(purchase.supplier_state_code, "27")

    def test_renaming_the_supplier_does_not_rewrite_a_filed_bill(self):
        self.create_draft()
        self.supplier.name = "Renamed Ltd"
        self.supplier.save(update_fields=["name"])
        purchase = Purchase.objects.get(bill_number="SUP-001")
        self.assertEqual(
            purchase.supplier_name, "Supplier Ltd", "the snapshot must not follow"
        )

    def test_a_free_text_line_is_allowed(self):
        body = self.create_draft(items=[line_payload(None, total_amount="50.00")])
        self.assertEqual(body["items"][0]["item"], None)
        self.assertEqual(body["items"][0]["item_name"], "Freight")

    def test_a_distant_supplier_is_inter_state(self):
        self.create_draft()
        purchase = Purchase.objects.get(bill_number="SUP-001")
        self.assertEqual(
            purchase.supply_type,
            "INTER",
            "a supplier in another state means an IGST credit, not CGST+SGST",
        )

    def test_origin_defaults_to_the_supplier_and_place_of_supply_to_us(self):
        self.create_draft()
        purchase = Purchase.objects.get(bill_number="SUP-001")
        self.assertEqual(purchase.origin_state_code, "27")
        self.assertEqual(purchase.place_of_supply, self.user.business.state_code)

    def test_a_supplier_in_our_own_state_is_intra_state(self):
        """
        Same-state supply gives CGST+SGST, which is the credit a shop actually
        claims. The default supplier is in state 27 and the business in 36, so
        this needs a supplier at home.
        """
        local = make_supplier(
            self.user, name="Local Supplier", gstin="36AAAAA0000A1Z5", state="36"
        )
        self.create_draft(supplier=local)
        purchase = Purchase.objects.get(bill_number="SUP-001")
        self.assertEqual(
            purchase.supply_type, "INTRA", "a local supplier is an intra-state supply"
        )
        self.assertEqual(purchase.origin_state_code, purchase.place_of_supply)

    def test_another_tenants_supplier_is_rejected(self):
        from apps.invoices.tests.base import make_user

        rival = make_user("rival@purchase.test")
        theirs = make_supplier(rival, name="Theirs")
        response = self.client_.post(
            PURCHASES_URL,
            header(theirs) | {"items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_a_walk_in_cannot_be_a_supplier(self):
        from apps.invoices.tests.base import make_party

        walk_in = make_party(self.user)
        walk_in.is_walk_in = True
        walk_in.save(update_fields=["is_walk_in"])
        response = self.client_.post(
            PURCHASES_URL,
            header(walk_in) | {"items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class DuplicateGuardTests(PurchaseBase):
    def test_the_same_supplier_bill_cannot_be_entered_twice(self):
        self.create_draft()
        response = self.client_.post(
            PURCHASES_URL,
            header(self.supplier) | {"items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("bill_number", response.json()["errors"])

    def test_a_different_supplier_may_use_the_same_bill_number(self):
        """Bill numbers are only unique per supplier, as in the real world."""
        self.create_draft()
        other = make_supplier(self.user, name="Other Ltd", gstin="27BBBBB0000B1Z5")
        response = self.client_.post(
            PURCHASES_URL,
            header(other) | {"items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_the_database_also_refuses_a_duplicate(self):
        """
        The friendly check is for humans; the constraint is the real guard.

        Without it two simultaneous requests could both pass the Python check and
        record the same bill twice, claiming input credit on it twice.
        """
        self.create_draft()
        duplicate = Purchase(
            business=self.user.business,
            supplier=self.supplier,
            bill_number="SUP-001",
            bill_date=timezone.localdate(),
            supplier_name="Supplier Ltd",
            origin_state_code="27",
            place_of_supply=self.user.business.state_code,
        )
        duplicate.financial_year = duplicate.financial_year  # set by save()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                duplicate.save()


class PostTests(PurchaseBase):
    def test_posting_increases_stock(self):
        """The sign. A purchase posted as a deduction is invisible for a month."""
        purchase_id = self.create_draft()["id"]
        self.assertEqual(self.post(purchase_id).status_code, 201)

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("10.000"))
        self.assertEqual(ledger_balance(self.item), Decimal("10.000"))

        movement = StockMovement.objects.filter(movement_type=MovementType.PURCHASE).get()
        self.assertEqual(movement.quantity_change, Decimal("10.000"))
        self.assertEqual(movement.source_label, "SUP-001")

    def test_posting_writes_one_movement_per_item_line(self):
        purchase_id = self.create_draft()["id"]
        self.post(purchase_id)
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.PURCHASE).count(), 1
        )

    def test_a_free_text_line_moves_no_stock(self):
        purchase_id = self.create_draft(items=[line_payload(None)])["id"]
        self.assertEqual(self.post(purchase_id).status_code, 201)
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.PURCHASE).count(),
            0,
            "a freight charge is not stock",
        )

    def test_posting_twice_does_not_double_the_stock(self):
        purchase_id = self.create_draft()["id"]
        self.post(purchase_id)
        second = self.post(purchase_id)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["already_posted"])

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("10.000"))
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.PURCHASE).count(), 1
        )

    def test_a_posted_bill_cannot_be_edited(self):
        purchase_id = self.create_draft()["id"]
        self.post(purchase_id)
        response = self.client_.patch(
            f"{PURCHASES_URL}{purchase_id}/",
            {"grand_total": "1.00"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_an_empty_bill_cannot_be_posted(self):
        purchase = Purchase.objects.create(
            business=self.user.business,
            supplier=self.supplier,
            bill_number="EMPTY-1",
            bill_date=timezone.localdate(),
            supplier_name="Supplier Ltd",
            origin_state_code="27",
            place_of_supply=self.user.business.state_code,
        )
        response = self.post(purchase.pk)
        self.assertEqual(response.status_code, 400)

    def test_purchase_price_is_not_rewritten_unless_asked(self):
        self.create_draft()
        self.post(Purchase.objects.get(bill_number="SUP-001").pk)
        self.item.refresh_from_db()
        self.assertNotEqual(
            self.item.purchase_price,
            Decimal("100.00"),
            "the catalogue must not be rewritten behind the user's back",
        )

    def test_purchase_price_updates_when_the_flag_is_set(self):
        self.create_draft(update_item_purchase_price=True)
        self.post(Purchase.objects.get(bill_number="SUP-001").pk)
        self.item.refresh_from_db()
        self.assertEqual(self.item.purchase_price, Decimal("100.00"))


class CancelTests(PurchaseBase):
    def test_cancelling_takes_the_stock_back(self):
        purchase_id = self.create_draft()["id"]
        self.post(purchase_id)
        self.assertEqual(self.cancel(purchase_id).status_code, 200)

        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("0.000"))
        self.assertEqual(ledger_balance(self.item), ZERO)

    def test_the_original_purchase_row_is_kept(self):
        """
        The ledger is append-only. Cancelling records a reversal; it does not
        erase the purchase, or six months later there is no evidence the goods
        ever arrived.
        """
        purchase_id = self.create_draft()["id"]
        self.post(purchase_id)
        self.cancel(purchase_id)
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.PURCHASE).count(), 1
        )
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.PURCHASE_REVERSAL).count(), 1
        )

    def test_cancelling_twice_does_not_remove_stock_twice(self):
        purchase_id = self.create_draft()["id"]
        self.post(purchase_id)
        self.cancel(purchase_id)
        self.cancel(purchase_id)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_stock, Decimal("0.000"))
        self.assertEqual(
            StockMovement.objects.filter(movement_type=MovementType.PURCHASE_REVERSAL).count(), 1
        )

    def test_a_reason_is_required(self):
        purchase_id = self.create_draft()["id"]
        self.post(purchase_id)
        response = self.client_.post(
            f"{PURCHASES_URL}{purchase_id}/cancel/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_cancelling_under_block_refuses_when_stock_has_gone(self):
        """
        The one direction a purchase cancellation can fail that a sale never can.

        The goods arrived, then were sold. Cancelling now would remove stock that
        is not there, so BLOCK refuses and says which item.
        """
        self.user.business.negative_stock_policy = "BLOCK"
        self.user.business.save(update_fields=["negative_stock_policy"])

        purchase_id = self.create_draft()["id"]
        self.post(purchase_id)

        self._sell_all_away()

        response = self.cancel(purchase_id)
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["error"], "CANCEL_WOULD_GO_NEGATIVE")
        self.assertIn("details", response.json())

    def test_cancelling_under_allow_is_permitted_and_can_go_negative(self):
        self.user.business.negative_stock_policy = "ALLOW"
        self.user.business.save(update_fields=["negative_stock_policy"])

        purchase_id = self.create_draft()["id"]
        self.post(purchase_id)

        # Sell it all on, so there is nothing left for the cancellation to
        # remove. Without this the cancel simply returns stock to zero, which is
        # correct and would not exercise the negative path at all.
        self._sell_all_away()

        self.assertEqual(self.cancel(purchase_id).status_code, 200)

        self.item.refresh_from_db()
        self.assertEqual(
            self.item.current_stock,
            Decimal("-10.000"),
            "under ALLOW the reversal is allowed and the balance goes negative",
        )

    def test_a_draft_cannot_be_cancelled(self):
        purchase_id = self.create_draft()["id"]
        response = self.cancel(purchase_id)
        self.assertEqual(response.status_code, 409)

    def test_a_locked_period_blocks_posting(self):
        # Created first, locked afterwards, so this exercises the POST path
        # rather than the save-time check (covered separately below).
        purchase_id = self.create_draft()["id"]

        self.user.business.books_locked_until = timezone.localdate()
        self.user.business.save(update_fields=["books_locked_until"])

        response = self.post(purchase_id)
        self.assertEqual(response.status_code, 400, response.content[:200])
        self.assertEqual(response.json()["error"], "PERIOD_LOCKED")

    def test_a_locked_period_blocks_saving_a_draft(self):
        """2.0.4 applies to saving, not just posting - catch it at entry."""
        self.user.business.books_locked_until = timezone.localdate() - timezone.timedelta(days=5)
        self.user.business.save(update_fields=["books_locked_until"])
        response = self.client_.post(
            PURCHASES_URL,
            header(
                self.supplier,
                bill_date=(timezone.localdate() - timezone.timedelta(days=7)).isoformat(),
            )
            | {"items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content[:200])
        self.assertEqual(Purchase.objects.count(), 0)


class TenantIsolationTests(PurchaseBase):
    def test_another_tenant_cannot_see_the_purchase(self):
        from apps.invoices.tests.base import api_client_for, make_user

        rival = make_user("rival@purchase-iso.test")
        purchase_id = self.create_draft()["id"]
        response = api_client_for(rival).get(f"{PURCHASES_URL}{purchase_id}/")
        self.assertIn(response.status_code, (403, 404))

    def test_the_purchase_list_is_scoped(self):
        from apps.invoices.tests.base import api_client_for, make_user

        self.create_draft()
        rival = make_user("rival@purchase-iso2.test")
        response = api_client_for(rival).get(PURCHASES_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            len(response.json()["results"]), 0, "another tenant's bill must not leak"
        )


class PreviewTests(PurchaseBase):
    def test_preview_reports_the_difference_without_saving(self):
        response = self.client_.post(
            f"{PURCHASES_URL}preview/",
            header(self.supplier, grand_total="1200.00")
            | {"items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        self.assertTrue(body["has_divergence"])
        self.assertEqual(Purchase.objects.count(), 0, "a preview must not write")

    def test_preview_of_a_correct_bill_is_clean(self):
        response = self.client_.post(
            f"{PURCHASES_URL}preview/",
            header(self.supplier) | {"items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertFalse(response.json()["has_divergence"])


class DueDateTests(PurchaseBase):
    """
    The date field that blocked the entire page.

    An untouched `<input type="date">` reads back as `""`, and `buildPayload`
    sent that straight through. DRF's DateField does not treat "" as absent -
    it treats it as a date it cannot parse - so every save and every preview
    answered 400 with a message about accepted formats, telling the user their
    *own* input was malformed when they had typed nothing at all.

    The whole suite was green through this: `header()` builds its payload
    without a `due_date` key, so no test ever sent one. These are the tests
    that were missing.
    """

    def _post(self, due_date):
        payload = header(self.supplier)
        if due_date is not _OMITTED:
            payload["due_date"] = due_date
        return self.client_.post(
            PURCHASES_URL, payload | {"items": [line_payload(self.item)]}, format="json"
        )

    def test_an_empty_string_due_date_is_treated_as_absent(self):
        response = self._post("")
        self.assertEqual(response.status_code, 201, response.content[:400])
        self.assertIsNone(response.json()["due_date"])

    def test_an_explicit_null_due_date_is_accepted(self):
        response = self._post(None)
        self.assertEqual(response.status_code, 201, response.content[:400])

    def test_a_missing_due_date_key_is_still_accepted(self):
        response = self._post(_OMITTED)
        self.assertEqual(response.status_code, 201, response.content[:400])

    def test_a_real_due_date_round_trips(self):
        response = self._post("2026-11-11")
        self.assertEqual(response.status_code, 201, response.content[:400])
        self.assertEqual(response.json()["due_date"], "2026-11-11")

    def test_a_nonsense_due_date_is_still_rejected(self):
        """Tolerating "" must not mean tolerating everything."""
        response = self._post("next tuesday")
        self.assertEqual(response.status_code, 400)
        self.assertIn("due_date", response.json()["errors"])

    def test_preview_also_survives_an_empty_due_date(self):
        response = self.client_.post(
            f"{PURCHASES_URL}preview/",
            header(self.supplier)
            | {"due_date": "", "items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])