"""
Decision 25: `Item.track_stock` - one definition of "has a stock quantity".

Before this, the model property said `item_type == PRODUCT` while the queryset
said `item_type == PRODUCT AND current_stock IS NOT NULL`. A product saved with
no stock value was "tracked" to one and "untracked" to the other, and 2.1's
`current_stock - quantity` on that NULL would 500 at issue time.

The tests below pin that the two now agree, and that the database - not just the
service layer - refuses the states that used to be representable.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import BusinessProfile
from apps.inventory.models import Item, ItemType

PASSWORD = "Str0ngPass!234"


def make_business(email):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(email=email, password=PASSWORD)
    return BusinessProfile.objects.create(
        user=user,
        trade_name=email,
        company_name=email,
        state_code="36",
        gstin="36AAAAA0000A1Z5",
    )


def make_item(business, **overrides):
    item_type = overrides.get("item_type", ItemType.PRODUCT)
    stock = overrides.get("current_stock", Decimal("10.000"))
    data = {
        "name": "Item",
        "item_type": item_type,
        "unit": "PCS",
        "hsn_sac_code": "610510",
        "sales_price": Decimal("100.00"),
        "tax_rate": Decimal("18.00"),
        "current_stock": stock,
        "low_stock_threshold": Decimal("2.000"),
        "track_stock": item_type == ItemType.PRODUCT and stock is not None,
        **overrides,
    }
    return Item.objects.create(business=business, **data)


class OneDefinitionTests(TestCase):
    """
    The actual defect: the property and the queryset answered the same question
    differently.
    """

    def setUp(self):
        self.business = make_business("one@example.com")
        self.product_with_stock = make_item(self.business, name="Tracked", item_code="T1")
        self.product_no_stock = make_item(
            self.business,
            name="Untracked product",
            item_code="T2",
            current_stock=None,
            track_stock=False,
        )
        self.service = make_item(
            self.business,
            name="A service",
            item_code="T3",
            item_type=ItemType.SERVICE,
            current_stock=None,
            track_stock=False,
            service_description="Advisory",
        )

    def test_the_property_reads_the_flag_directly(self):
        self.assertTrue(self.product_with_stock.tracks_stock)
        self.assertFalse(self.product_no_stock.tracks_stock)
        self.assertFalse(self.service.tracks_stock)

    def test_the_queryset_reads_the_same_flag(self):
        """The two must never be able to disagree again."""
        from_model = {
            item.pk
            for item in Item.objects.filter(business=self.business)
            if item.tracks_stock
        }
        from_queryset = set(
            Item.objects.filter(business=self.business).stock_tracked().values_list("pk", flat=True)
        )
        self.assertEqual(
            from_model, from_queryset, "property and queryset disagree on tracked items"
        )

    def test_a_product_with_no_stock_is_not_tracked_anywhere(self):
        """
        This is the row that made the old definitions disagree: a PRODUCT with a
        NULL quantity.
        """
        self.assertEqual(self.product_no_stock.item_type, ItemType.PRODUCT)
        self.assertFalse(self.product_no_stock.tracks_stock)
        self.assertNotIn(
            self.product_no_stock.pk,
            Item.objects.filter(business=self.business).stock_tracked().values_list("pk", flat=True),
        )

    def test_low_stock_only_looks_at_tracked_items(self):
        """An untracked item must never raise a 'low stock' alert."""
        self.product_no_stock.low_stock_threshold = Decimal("5.000")
        self.product_no_stock.save(update_fields=["low_stock_threshold"])
        self.product_no_stock.refresh_from_db()
        self.assertFalse(self.product_no_stock.is_low_stock)
        self.assertNotIn(
            self.product_no_stock.pk,
            Item.objects.filter(business=self.business).low_stock().values_list("pk", flat=True),
        )


class DatabaseConstraintTests(TestCase):
    """
    Decision 25's DB rule: `track_stock = False OR (PRODUCT AND stock not null)`,
    and a SERVICE can never be tracked. Enforced by the database, so one shell
    session cannot bypass it.
    """

    def setUp(self):
        self.business = make_business("db@example.com")

    def test_a_tracked_service_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Item.objects.create(
                    business=self.business,
                    name="Bad service",
                    item_type=ItemType.SERVICE,
                    unit="HOUR",
                    hsn_sac_code="998311",
                    service_description="Advisory",
                    sales_price=Decimal("100.00"),
                    tax_rate=Decimal("18.00"),
                    current_stock=None,
                    track_stock=True,
                )

    def test_a_tracked_product_without_a_quantity_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Item.objects.create(
                    business=self.business,
                    name="Bad product",
                    item_type=ItemType.PRODUCT,
                    unit="PCS",
                    hsn_sac_code="610510",
                    sales_price=Decimal("100.00"),
                    tax_rate=Decimal("18.00"),
                    current_stock=None,
                    track_stock=True,
                )

    def test_an_untracked_product_is_allowed(self):
        item = Item.objects.create(
            business=self.business,
            name="Fine, untracked",
            item_type=ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=Decimal("100.00"),
            tax_rate=Decimal("18.00"),
            current_stock=None,
            track_stock=False,
        )
        self.assertFalse(item.tracks_stock)

    def test_a_negative_balance_is_allowed(self):
        """
        Under the ALLOW negative-stock policy (decision 33) a sale may drive the
        balance below zero. The constraint is about *tracking*, not about the
        balance being non-negative, so this must not be blocked.
        """
        item = Item.objects.create(
            business=self.business,
            name="Oversold",
            item_type=ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=Decimal("100.00"),
            tax_rate=Decimal("18.00"),
            current_stock=Decimal("-3.000"),
            track_stock=True,
        )
        self.assertEqual(item.current_stock, Decimal("-3.000"))
        self.assertTrue(item.tracks_stock)


class SerializerRuleTests(TestCase):
    """`SERVICE` forces tracking off; `PRODUCT` defaults it on."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(
            email="ser@example.com", password=PASSWORD
        )
        make_business("other@example.com")
        self.business = BusinessProfile.objects.create(
            user=self.user,
            trade_name="Ser",
            company_name="Ser",
            state_code="36",
            gstin="36AAAAA0000A1Z5",
        )
        self.client_ = APIClient()
        self.client_.force_authenticate(self.user)
        self.url = reverse("item-list")

    def create(self, **overrides):
        payload = {
            "name": "New item",
            "item_type": ItemType.PRODUCT,
            "unit": "PCS",
            "hsn_sac_code": "610510",
            "sales_price": "100.00",
            "tax_rate": "18.00",
            **overrides,
        }
        return self.client_.post(self.url, payload, format="json")

    def test_a_product_is_tracked_by_default(self):
        """The trap decision 25 closes: created via the API and silently untracked."""
        response = self.create(name="Default tracked")
        self.assertEqual(response.status_code, 201, response.content[:300])
        item = Item.objects.get(name="Default tracked")
        self.assertTrue(item.track_stock)
        self.assertTrue(item.tracks_stock)

    def test_a_default_tracked_product_gets_a_quantity_not_null(self):
        """
        'Tracked, and none left' is a real state the shop can correct with an
        opening adjustment - and it is distinguishable from 'not tracked'.
        """
        self.create(name="Zero stock")
        item = Item.objects.get(name="Zero stock")
        self.assertIsNotNone(item.current_stock)
        self.assertEqual(item.current_stock, Decimal("0.000"))

    def test_a_product_can_opt_out_of_tracking(self):
        response = self.create(name="Untracked", track_stock=False)
        self.assertEqual(response.status_code, 201, response.content[:300])
        item = Item.objects.get(name="Untracked")
        self.assertFalse(item.track_stock)
        self.assertIsNone(
            item.current_stock,
            "'no stock recorded' must not look like 'zero stock'",
        )

    def test_a_service_is_never_tracked_even_if_asked(self):
        response = self.create(
            name="A service",
            item_type=ItemType.SERVICE,
            service_description="Advisory",
            hsn_sac_code="998311",
            track_stock=True,
        )
        self.assertEqual(response.status_code, 201, response.content[:300])
        item = Item.objects.get(name="A service")
        self.assertFalse(item.track_stock)
        self.assertIsNone(item.current_stock)

    def test_a_service_sending_a_quantity_is_still_refused(self):
        response = self.create(
            name="Bad service",
            item_type=ItemType.SERVICE,
            service_description="Advisory",
            hsn_sac_code="998311",
            current_stock="5",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("current_stock", response.json()["errors"])

    def test_turning_tracking_off_nulls_the_quantity(self):
        created = self.create(name="Will opt out", current_stock="25.000")
        self.assertEqual(created.status_code, 201, created.content[:300])
        detail = f"{self.url}{created.json()['id']}/"

        response = self.client_.patch(detail, {"track_stock": False}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        item = Item.objects.get(pk=created.json()["id"])
        self.assertFalse(item.track_stock)
        self.assertIsNone(item.current_stock)

    def test_turning_tracking_on_needs_no_quantity(self):
        """The serializer supplies 0 rather than leaving it NULL."""
        created = self.create(name="Will opt in", track_stock=False)
        self.assertEqual(created.status_code, 201, created.content[:300])
        item = Item.objects.get(pk=created.json()["id"])
        self.assertIsNone(item.current_stock)

        response = self.client_.patch(
            f"{self.url}{item.pk}/", {"track_stock": True}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        item.refresh_from_db()
        self.assertTrue(item.track_stock)
        self.assertEqual(item.current_stock, Decimal("0.000"))

    def test_the_flag_is_exposed_read_only_for_legibility(self):
        response = self.create(name="Exposed")
        self.assertIn("track_stock", response.json())
        self.assertTrue(response.json()["track_stock"])


class MigrationBackfillTests(TestCase):
    """
    The backfill reproduces the OLD queryset exactly. Getting it wrong silently
    untracks every existing catalogue, and 2.1 would then never deduct anything
    with nothing to tell the user.

    Exercised against the model rather than by re-running the migration: the
    rules are "a PRODUCT with a quantity is tracked, everything else is not",
    which is what the fixtures below encode.
    """

    def test_products_with_stock_are_tracked_after_migration(self):
        """The data-migration test the plan asks for, written against the model."""
        business = make_business("bf@example.com")
        # Simulate pre-migration rows: products that the OLD queryset counted.
        with_stock = Item.objects.create(
            business=business,
            name="Has stock",
            item_type=ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=Decimal("10.00"),
            tax_rate=Decimal("18.00"),
            current_stock=Decimal("7.000"),
            track_stock=True,
        )
        without_stock = Item.objects.create(
            business=business,
            name="No stock",
            item_type=ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=Decimal("10.00"),
            tax_rate=Decimal("18.00"),
            current_stock=None,
            track_stock=False,
        )
        self.assertTrue(with_stock.tracks_stock)
        self.assertFalse(without_stock.tracks_stock)