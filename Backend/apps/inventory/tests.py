"""
Tests for the Inventory app.

Mirrors the parties suite: `ItemTenantIsolationTests` is the security-critical
group - no tenant may ever see or touch another tenant's catalogue.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import BusinessProfile
from apps.inventory.models import Item

User = get_user_model()
PASSWORD = "Str0ng!Pass#2026"

VALID_ITEM = {
    "name": "Cotton T-Shirt",
    "item_type": "PRODUCT",
    "item_code": "TSH-001",
    "barcode": "8901234567890",
    "hsn_sac_code": "61091000",
    "unit": "PCS",
    "sales_price": "249.50",
    "purchase_price": "120.00",
    "price_includes_tax": False,
    "tax_rate": "5.00",
    "current_stock": "25.500",
    "low_stock_threshold": "5.000",
}

VALID_SERVICE = {
    "name": "Consulting Session",
    "item_type": "SERVICE",
    "hsn_sac_code": "998311",
    "service_description": "Professional consulting services",
    "unit": "HOUR",
    "sales_price": "5000.00",
    "tax_rate": "18.00",
}


def make_user(email: str, trade_name: str):
    user = User.objects.create_user(email=email, password=PASSWORD)
    business = BusinessProfile.objects.create(
        user=user, trade_name=trade_name, company_name=f"{trade_name} Pvt Ltd"
    )
    return user, business


def make_item(business, **overrides) -> Item:
    """
    Create an item directly in the database.

    Codes default to blank so several items can coexist inside one business
    (the DB has conditional unique constraints on (business, item_code) and
    (business, barcode)). Services get NULL stock by default.
    """
    data = {
        "name": "Test Item",
        "item_type": Item.ItemType.PRODUCT,
        "item_code": "",
        "barcode": "",
        "hsn_sac_code": "",
        "service_description": "",
        "unit": "PCS",
        "sales_price": Decimal("100.00"),
        "tax_rate": Decimal("18.00"),
        "current_stock": Decimal("10.000"),
        "low_stock_threshold": Decimal("2.000"),
        **overrides,
    }
    return Item.objects.create(business=business, **data)


# ===========================================================================
# Tenant isolation - the critical security group
# ===========================================================================
class ItemTenantIsolationTests(APITestCase):
    """User A must never see, read, change or delete User B's items."""

    def setUp(self):
        self.user_a, self.business_a = make_user("a@example.com", "Alpha Traders")
        self.user_b, self.business_b = make_user("b@example.com", "Beta Stores")
        self.list_url = reverse("item-list")

        self.item_a = make_item(self.business_a, name="Alpha Widget")
        self.item_b = make_item(self.business_b, name="Beta Widget")

    # ---------- list ----------
    def test_list_only_returns_own_items(self):
        self.client.force_authenticate(self.user_a)
        data = self.client.get(self.list_url).data
        self.assertEqual({i["name"] for i in data["results"]}, {"Alpha Widget"})

    def test_list_count_does_not_leak_other_tenants(self):
        self.client.force_authenticate(self.user_a)
        self.assertEqual(self.client.get(self.list_url).data["count"], 1)

        self.client.force_authenticate(self.user_b)
        self.assertEqual(self.client.get(self.list_url).data["count"], 1)

    def test_search_never_returns_another_tenants_item(self):
        self.client.force_authenticate(self.user_a)
        data = self.client.get(self.list_url, {"search": "Beta"}).data
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["results"], [])

    def test_autocomplete_search_endpoint_is_scoped(self):
        url = reverse("item-search")
        self.client.force_authenticate(self.user_a)
        self.assertEqual(self.client.get(url, {"q": "Beta"}).data, [])
        self.assertEqual(
            [i["name"] for i in self.client.get(url, {"q": "Alpha"}).data], ["Alpha Widget"]
        )

    def test_filters_do_not_leak_other_tenants(self):
        self.client.force_authenticate(self.user_a)
        for params in (
            {"item_type": "PRODUCT"},
            {"item_type": "SERVICE"},
            {"low_stock": "true"},
            {"low_stock": "false"},
            {"is_active": "true"},
            {"ordering": "-created_at"},
        ):
            with self.subTest(params=params):
                data = self.client.get(self.list_url, params).data
                names = {i["name"] for i in data["results"]}
                self.assertNotIn("Beta Widget", names)
                self.assertTrue(names <= {"Alpha Widget"})

    # ---------- retrieve / update / delete ----------
    def test_cannot_retrieve_another_tenants_item(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.get(reverse("item-detail", args=[self.item_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_update_another_tenants_item(self):
        self.client.force_authenticate(self.user_a)
        url = reverse("item-detail", args=[self.item_b.pk])
        for method in ("patch", "put"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, {"name": "Hijacked"}, format="json")
                self.assertEqual(response.status_code, 404)
        self.item_b.refresh_from_db()
        self.assertEqual(self.item_b.name, "Beta Widget")

    def test_cannot_delete_another_tenants_item(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.delete(reverse("item-detail", args=[self.item_b.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Item.objects.filter(pk=self.item_b.pk).exists())

    def test_cannot_restore_another_tenants_deleted_item(self):
        deleted = make_item(self.business_b, name="Beta Deleted", is_active=False)
        self.client.force_authenticate(self.user_a)
        response = self.client.post(reverse("item-restore", args=[deleted.pk]))
        self.assertEqual(response.status_code, 404)
        deleted.refresh_from_db()
        self.assertFalse(deleted.is_active)

    # ---------- writes ----------
    def test_created_item_is_stamped_with_callers_business(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.post(self.list_url, VALID_ITEM, format="json")
        self.assertEqual(response.status_code, 201)
        created = Item.objects.get(pk=response.data["id"])
        self.assertEqual(created.business, self.business_a)

    def test_client_cannot_assign_someone_elses_business(self):
        """The business FK is set by the view, never by the payload."""
        self.client.force_authenticate(self.user_a)
        payload = {**VALID_ITEM, "business": self.business_b.pk}
        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Item.objects.get(pk=response.data["id"]).business, self.business_a)

    def test_duplicate_item_code_is_scoped_per_business(self):
        make_item(self.business_b, name="Beta Coded", item_code="SKU-9", barcode="")
        self.client.force_authenticate(self.user_b)
        response = self.client.post(
            self.list_url, {**VALID_ITEM, "item_code": "SKU-9", "barcode": ""}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("item_code", response.data["errors"])

        # A different business may reuse the same code.
        self.user_c, business_c = make_user("c@example.com", "Gamma Foods")
        self.client.force_authenticate(self.user_c)
        response = self.client.post(
            self.list_url, {**VALID_ITEM, "item_code": "SKU-9", "barcode": ""}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Item.objects.get(pk=response.data["id"]).business, business_c)

    def test_duplicate_barcode_is_scoped_per_business(self):
        make_item(self.business_a, name="Alpha Barcoded", item_code="", barcode="BC-1")
        self.client.force_authenticate(self.user_a)
        response = self.client.post(
            self.list_url, {**VALID_ITEM, "item_code": "", "barcode": "BC-1"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("barcode", response.data["errors"])

    def test_database_constraint_blocks_duplicate_code_within_a_business(self):
        make_item(self.business_a, item_code="DUP-1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_item(self.business_a, item_code="DUP-1", name="Another")

    def test_database_constraint_allows_reusing_a_soft_deleted_items_code(self):
        """Conditional unique constraints let a deleted item's code be reused."""
        deleted = make_item(self.business_a, item_code="REUSE-1", is_active=False)
        self.assertFalse(deleted.is_active)
        replacement = make_item(self.business_a, item_code="REUSE-1", name="Replacement")
        self.assertEqual(replacement.item_code, "REUSE-1")

    # ---------- auth ----------
    def test_anonymous_access_is_rejected(self):
        for url in (self.list_url, reverse("item-detail", args=[self.item_a.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 401)

    def test_superuser_without_profile_gets_one_created(self):
        admin = User.objects.create_superuser(email="root@example.com", password=PASSWORD)
        self.assertFalse(BusinessProfile.objects.filter(user=admin).exists())

        self.client.force_authenticate(admin)
        response = self.client.post(self.list_url, VALID_ITEM, format="json")
        self.assertEqual(response.status_code, 201)  # no RelatedObjectDoesNotExist
        self.assertEqual(Item.objects.get(pk=response.data["id"]).business.user, admin)


# ===========================================================================
# CRUD behaviour
# ===========================================================================
class ItemCrudTests(APITestCase):
    def setUp(self):
        self.user, self.business = make_user("owner@example.com", "Solo Traders")
        self.list_url = reverse("item-list")
        self.client.force_authenticate(self.user)

    def test_create_product(self):
        response = self.client.post(self.list_url, VALID_ITEM, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Cotton T-Shirt")
        self.assertTrue(response.data["tracks_stock"])

    def test_service_ignores_blank_stock_and_stores_null(self):
        """Explicit null / omitted stock is accepted and stored as NULL."""
        for payload in (
            VALID_SERVICE,
            {**VALID_SERVICE, "current_stock": None, "low_stock_threshold": None},
            {**VALID_SERVICE, "current_stock": "", "low_stock_threshold": ""},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(self.list_url, payload, format="json")
                self.assertEqual(response.status_code, 201)
                self.assertIsNone(response.data["current_stock"])
                self.assertIsNone(response.data["low_stock_threshold"])
                self.assertFalse(response.data["tracks_stock"])

    def test_service_rejects_a_real_stock_number(self):
        """Sending an actual quantity for a service is a mistake worth flagging."""
        response = self.client.post(
            self.list_url, {**VALID_SERVICE, "current_stock": "5"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("current_stock", response.data["errors"])

    def test_create_uppercases_codes(self):
        response = self.client.post(
            self.list_url,
            {**VALID_ITEM, "item_code": "tsh-002", "barcode": "bc-9", "hsn_sac_code": "61091000"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["item_code"], "TSH-002")
        self.assertEqual(response.data["barcode"], "BC-9")

    def test_prices_and_stock_are_serialised_as_strings(self):
        response = self.client.post(self.list_url, VALID_ITEM, format="json")
        self.assertEqual(response.data["sales_price"], "249.50")
        self.assertEqual(response.data["current_stock"], "25.500")

    def test_delete_is_a_soft_delete(self):
        item = make_item(self.business, name="Temp Item")
        response = self.client.delete(reverse("item-detail", args=[item.pk]))
        self.assertEqual(response.status_code, 204)
        item.refresh_from_db()
        self.assertFalse(item.is_active)        # flagged, not removed
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())

    def test_soft_deleted_item_leaves_the_default_list(self):
        make_item(self.business, name="Visible")
        make_item(self.business, name="Hidden", is_active=False)

        names = {i["name"] for i in self.client.get(self.list_url).data["results"]}
        self.assertEqual(names, {"Visible"})

        names = {
            i["name"]
            for i in self.client.get(self.list_url, {"is_active": "false"}).data["results"]
        }
        self.assertEqual(names, {"Hidden"})

    def test_restore_brings_the_item_back(self):
        item = make_item(self.business, name="Resurrected", is_active=False)
        response = self.client.post(reverse("item-restore", args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertTrue(item.is_active)

    def test_partial_update_leaves_other_fields_untouched(self):
        item = make_item(self.business, item_code="KEEP-1", sales_price=Decimal("77.00"))
        self.client.patch(
            reverse("item-detail", args=[item.pk]), {"name": "Renamed"}, format="json"
        )
        item.refresh_from_db()
        self.assertEqual(item.name, "Renamed")
        self.assertEqual(item.item_code, "KEEP-1")
        self.assertEqual(item.sales_price, Decimal("77.00"))

    def test_list_uses_the_lightweight_serializer(self):
        make_item(self.business, name="Listed")
        data = self.client.get(self.list_url).data["results"][0]
        self.assertIn("tracks_stock", data)
        self.assertIn("tax_rate_label", data)
        # The list payload stays light: no long-text field the table never shows.
        self.assertNotIn("service_description", data)


# ===========================================================================
# Validation
# ===========================================================================
class ItemValidationTests(APITestCase):
    def setUp(self):
        self.user, self.business = make_user("owner@example.com", "Validation Traders")
        self.list_url = reverse("item-list")
        self.client.force_authenticate(self.user)

    def test_hsn_of_5_or_7_digits_is_rejected(self):
        for code in ("12345", "1234567"):
            with self.subTest(code=code):
                response = self.client.post(
                    self.list_url, {**VALID_ITEM, "hsn_sac_code": code}, format="json"
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("hsn_sac_code", response.data["errors"])

    def test_valid_hsn_lengths_are_accepted(self):
        for code in ("6109", "610910", "61091000"):
            with self.subTest(code=code):
                response = self.client.post(
                    self.list_url,
                    {**VALID_ITEM, "hsn_sac_code": code, "item_code": "", "barcode": ""},
                    format="json",
                )
                self.assertEqual(response.status_code, 201)

    def test_service_rejects_an_8_digit_code(self):
        response = self.client.post(
            self.list_url, {**VALID_SERVICE, "hsn_sac_code": "99831100"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("hsn_sac_code", response.data["errors"])

    def test_service_accepts_a_6_digit_sac(self):
        response = self.client.post(
            self.list_url, {**VALID_SERVICE, "hsn_sac_code": "998311"}, format="json"
        )
        self.assertEqual(response.status_code, 201)

    def test_6_digit_code_is_valid_for_both_types(self):
        """HSN and SAC overlap at 6 digits, so both must accept it."""
        product = self.client.post(
            self.list_url, {**VALID_ITEM, "hsn_sac_code": "998311"}, format="json"
        )
        self.assertEqual(product.status_code, 201)

        service = self.client.post(
            self.list_url, {**VALID_SERVICE, "hsn_sac_code": "998311"}, format="json"
        )
        self.assertEqual(service.status_code, 201)

    def test_service_without_description_is_rejected(self):
        response = self.client.post(
            self.list_url, {**VALID_SERVICE, "service_description": ""}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("service_description", response.data["errors"])

    def test_product_service_description_is_cleared(self):
        response = self.client.post(
            self.list_url, {**VALID_ITEM, "service_description": "Should not persist"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["service_description"], "")

    def test_negative_price_is_rejected(self):
        response = self.client.post(
            self.list_url, {**VALID_ITEM, "sales_price": "-5"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("sales_price", response.data["errors"])

    def test_invalid_tax_rate_is_rejected(self):
        response = self.client.post(
            self.list_url, {**VALID_ITEM, "tax_rate": "7.00"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("tax_rate", response.data["errors"])

    def test_all_supported_tax_rates_are_accepted(self):
        for rate in ("0", "0.25", "1.5", "3", "5", "12", "18", "28"):
            with self.subTest(rate=rate):
                response = self.client.post(
                    self.list_url,
                    {**VALID_ITEM, "tax_rate": rate, "item_code": "", "barcode": ""},
                    format="json",
                )
                self.assertEqual(response.status_code, 201)

    def test_negative_stock_is_rejected(self):
        response = self.client.post(
            self.list_url, {**VALID_ITEM, "current_stock": "-1"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("current_stock", response.data["errors"])

    def test_stock_keeps_three_decimal_places(self):
        response = self.client.post(
            self.list_url, {**VALID_ITEM, "current_stock": "1.500"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["current_stock"], "1.500")

    def test_unknown_unit_is_rejected(self):
        response = self.client.post(
            self.list_url, {**VALID_ITEM, "unit": "PARSEC"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("unit", response.data["errors"])

    def test_blank_name_is_rejected(self):
        response = self.client.post(self.list_url, {**VALID_ITEM, "name": "   "}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data["errors"])


# ===========================================================================
# Filters and search
# ===========================================================================
class ItemFilterTests(APITestCase):
    def setUp(self):
        self.user, self.business = make_user("owner@example.com", "Filter Traders")
        self.list_url = reverse("item-list")
        self.client.force_authenticate(self.user)

        make_item(self.business, name="Widget A", item_code="WA-1", hsn_sac_code="61091000",
                  sales_price=Decimal("10.00"), current_stock=Decimal("50.000"),
                  low_stock_threshold=Decimal("5.000"))
        make_item(self.business, name="Widget B", item_code="WB-2", hsn_sac_code="620520",
                  sales_price=Decimal("20.00"), current_stock=Decimal("1.000"),
                  low_stock_threshold=Decimal("5.000"))
        make_item(self.business, name="Service C", item_type=Item.ItemType.SERVICE,
                  service_description="Advisory", hsn_sac_code="998311",
                  sales_price=Decimal("30.00"), current_stock=None, low_stock_threshold=None)

    def names(self, **params):
        return {i["name"] for i in self.client.get(self.list_url, params).data["results"]}

    def test_filter_by_product(self):
        self.assertEqual(self.names(item_type="PRODUCT"), {"Widget A", "Widget B"})

    def test_filter_by_service(self):
        self.assertEqual(self.names(item_type="SERVICE"), {"Service C"})

    def test_low_stock_filter_excludes_services(self):
        self.assertEqual(self.names(low_stock="true"), {"Widget B"})

    def test_low_stock_false_includes_everything_else(self):
        self.assertEqual(self.names(low_stock="false"), {"Widget A", "Service C"})

    def test_search_by_name(self):
        self.assertEqual(self.names(search="Widget A"), {"Widget A"})

    def test_search_by_item_code(self):
        self.assertEqual(self.names(search="WB-2"), {"Widget B"})

    def test_search_by_hsn(self):
        self.assertEqual(self.names(search="620520"), {"Widget B"})

    def test_ordering_by_price_desc(self):
        names = [
            i["name"]
            for i in self.client.get(self.list_url, {"ordering": "-sales_price"}).data["results"]
        ]
        self.assertEqual(names, ["Service C", "Widget B", "Widget A"])


# ===========================================================================
# URL routing - guards the exact paths the frontend hardcodes
# ===========================================================================
class ItemUrlRoutingTests(APITestCase):
    """
    The JS calls literal strings (`/items/`, `/items/search/`), it does not use
    Django's reverse(). So these tests deliberately avoid reverse() and hit the
    hardcoded paths, otherwise a double-prefixed router (which published
    /api/v1/items/items/ and left /api/v1/items/ resolving to the read-only API
    root, returning 405 on POST) would still pass every other test in this file.
    """

    def setUp(self):
        self.user, self.business = make_user("owner@example.com", "Routing Traders")

    def test_list_url_accepts_post(self):
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/v1/items/", VALID_ITEM, format="json")
        self.assertEqual(response.status_code, 201)

    def test_list_url_serves_get(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/items/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.data)

    def test_detail_url_serves_get_and_patch(self):
        item = make_item(self.business, name="Routed")
        self.client.force_authenticate(self.user)

        self.assertEqual(self.client.get(f"/api/v1/items/{item.pk}/").status_code, 200)
        patched = self.client.patch(
            f"/api/v1/items/{item.pk}/", {"name": "Routed Again"}, format="json"
        )
        self.assertEqual(patched.status_code, 200)

    def test_search_url_serves_get(self):
        make_item(self.business, name="Findable")
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/items/search/", {"q": "Find"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([i["name"] for i in response.data], ["Findable"])

    def test_restore_url_serves_post(self):
        item = make_item(self.business, name="Restorable", is_active=False)
        self.client.force_authenticate(self.user)
        response = self.client.post(f"/api/v1/items/{item.pk}/restore/")
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertTrue(item.is_active)

    def test_meta_urls_serve_get(self):
        self.client.force_authenticate(self.user)
        units = self.client.get("/api/v1/meta/units/")
        self.assertEqual(units.status_code, 200)
        self.assertIn({"code": "PCS", "name": "Pieces"}, units.data)

        rates = self.client.get("/api/v1/meta/gst-rates/")
        self.assertEqual(rates.status_code, 200)
        self.assertIn({"value": "18.00", "label": "18%"}, rates.data)

    def test_routes_are_not_double_prefixed(self):
        from django.urls import Resolver404, resolve

        with self.assertRaises(Resolver404):
            resolve("/api/v1/items/items/1/")


# ===========================================================================
# Model helpers
# ===========================================================================
class ItemModelTests(APITestCase):
    def setUp(self):
        self.user, self.business = make_user("owner@example.com", "Model Traders")

    def test_tracks_stock_is_true_only_for_products(self):
        product = make_item(self.business, name="P", item_type=Item.ItemType.PRODUCT)
        service = make_item(
            self.business, name="S", item_type=Item.ItemType.SERVICE,
            service_description="Advisory", current_stock=None, low_stock_threshold=None,
        )
        self.assertTrue(product.tracks_stock)
        self.assertFalse(service.tracks_stock)

    def test_is_low_stock_at_below_and_above_threshold(self):
        above = make_item(self.business, name="Above", current_stock=Decimal("10.000"),
                          low_stock_threshold=Decimal("5.000"))
        equal = make_item(self.business, name="Equal", current_stock=Decimal("5.000"),
                          low_stock_threshold=Decimal("5.000"))
        below = make_item(self.business, name="Below", current_stock=Decimal("1.000"),
                          low_stock_threshold=Decimal("5.000"))
        self.assertFalse(above.is_low_stock)
        self.assertTrue(equal.is_low_stock)   # "at or below"
        self.assertTrue(below.is_low_stock)

    def test_service_is_never_low_stock_even_with_zero_stock(self):
        """The exact bug nullable stock prevents: a service at 0 must not alert."""
        service = make_item(
            self.business, name="Free advice", item_type=Item.ItemType.SERVICE,
            service_description="Advisory", current_stock=Decimal("0.000"),
            low_stock_threshold=Decimal("0.000"),
        )
        self.assertFalse(service.is_low_stock)

    def test_product_without_a_threshold_is_never_low_stock(self):
        item = make_item(self.business, name="No threshold", current_stock=Decimal("1.000"),
                         low_stock_threshold=None)
        self.assertFalse(item.is_low_stock)

    def test_stock_tracked_queryset_excludes_services(self):
        service = make_item(
            self.business, name="S", item_type=Item.ItemType.SERVICE,
            service_description="Advisory", current_stock=None, low_stock_threshold=None,
        )
        product = make_item(self.business, name="P", current_stock=Decimal("4.000"))
        self.assertNotIn(service, Item.objects.stock_tracked())
        self.assertIn(product, Item.objects.stock_tracked())

    def test_low_stock_queryset_excludes_services_and_untracked_products(self):
        service = make_item(
            self.business, name="S", item_type=Item.ItemType.SERVICE,
            service_description="Advisory", current_stock=None, low_stock_threshold=None,
        )
        untracked = make_item(self.business, name="Untracked", current_stock=None,
                              low_stock_threshold=Decimal("1.000"))
        low = make_item(self.business, name="Low", current_stock=Decimal("0.500"),
                        low_stock_threshold=Decimal("1.000"))
        low_ids = {i.pk for i in Item.objects.low_stock()}
        self.assertIn(low.pk, low_ids)
        self.assertNotIn(service.pk, low_ids)
        self.assertNotIn(untracked.pk, low_ids)

    def test_stock_survives_a_database_round_trip_at_three_decimals(self):
        item = make_item(self.business, current_stock=Decimal("1.500"))
        item.refresh_from_db()
        self.assertEqual(item.current_stock, Decimal("1.500"))
        self.assertEqual(item.current_stock.as_tuple().exponent, -3)

    def test_money_values_are_decimal_not_float(self):
        item = make_item(self.business, sales_price=Decimal("249.50"))
        item.refresh_from_db()
        self.assertIsInstance(item.sales_price, Decimal)
        self.assertIsInstance(item.tax_rate, Decimal)
        self.assertNotIsInstance(item.sales_price, float)

    def test_decimal_stock_arithmetic_stays_exact(self):
        """Float would drift here: 0.1 + 0.2 != 0.3 in binary floating point."""
        item = make_item(self.business, current_stock=Decimal("0.100"))
        total = item.current_stock
        total += Decimal("0.200")
        self.assertEqual(total, Decimal("0.300"))
        self.assertEqual(str(total), "0.300")

    def test_code_type_label_switches_between_hsn_and_sac(self):
        product = make_item(self.business, name="P", item_type=Item.ItemType.PRODUCT)
        service = make_item(
            self.business, name="S", item_type=Item.ItemType.SERVICE,
            service_description="Advisory", current_stock=None, low_stock_threshold=None,
        )
        self.assertEqual(product.code_type_label, "HSN Code")
        self.assertEqual(service.code_type_label, "SAC Code")

    def test_tax_rate_label_formats_without_trailing_zeros(self):
        item = make_item(self.business, tax_rate=Decimal("18.00"))
        self.assertEqual(item.tax_rate_label, "18%")

    def test_display_unit_is_human_readable(self):
        self.assertEqual(make_item(self.business, unit="KG").get_display_unit(), "Kilogram")