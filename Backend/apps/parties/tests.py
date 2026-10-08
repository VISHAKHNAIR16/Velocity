"""
Tests for the Parties app.

The most important group here is `PartyTenantIsolationTests`: every business
record must be invisible and untouchable from another tenant's account.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import BusinessProfile
from apps.parties.models import Party

User = get_user_model()
PASSWORD = "Str0ng!Pass#2026"

VALID_PARTY = {
    "name": "Sunrise Traders",
    "party_type": "CUSTOMER",
    "mobile": "9876543210",
    "email": "accounts@sunrise.example",
    "gstin": "36ABCCS2942R1ZR",
    "pan": "ABCCS2942R",
    "state_code": "36",
    "billing_address": "12 MG Road",
    "billing_city": "Hyderabad",
    "billing_pincode": "500001",
    "opening_balance": "1500.50",
    "balance_type": "CREDIT",
}


def make_user(email: str, trade_name: str):
    """Create a user plus the business profile that owns their data."""
    user = User.objects.create_user(email=email, password=PASSWORD)
    business = BusinessProfile.objects.create(
        user=user, trade_name=trade_name, company_name=f"{trade_name} Pvt Ltd"
    )
    return user, business


def make_party(business, **overrides) -> Party:
    """
    Create a party directly in the database.

    Defaults to a blank GSTIN/PAN so several parties can coexist inside one
    business (the DB has unique constraints on (business, gstin) and
    (business, pan)). Pass gstin=/pan= explicitly when the test needs them.
    """
    data = {
        "name": "Test Party",
        "party_type": "CUSTOMER",
        "mobile": "9876543210",
        "email": "",
        "gstin": "",
        "pan": "",
        "state_code": "36",
        "billing_address": "12 MG Road",
        "billing_city": "Hyderabad",
        "billing_pincode": "500001",
        "opening_balance": "1500.50",
        "balance_type": "CREDIT",
        **overrides,
    }
    return Party.objects.create(business=business, **data)


# ===========================================================================
# Tenant isolation - the critical security group
# ===========================================================================
class PartyTenantIsolationTests(APITestCase):
    """User A must never see, read, change or delete User B's parties."""

    def setUp(self):
        self.user_a, self.business_a = make_user("a@example.com", "Alpha Traders")
        self.user_b, self.business_b = make_user("b@example.com", "Beta Stores")
        self.list_url = reverse("party-list")

        self.party_a = make_party(self.business_a, name="Alpha Customer")
        self.party_b = make_party(self.business_b, name="Beta Customer")

    # ---------- list ----------
    def test_list_only_returns_own_parties(self):
        self.client.force_authenticate(self.user_a)
        data = self.client.get(self.list_url).data
        names = {p["name"] for p in data["results"]}
        self.assertEqual(names, {"Alpha Customer"})
        self.assertNotIn("Beta Customer", names)

    def test_list_count_does_not_leak_other_tenants(self):
        self.client.force_authenticate(self.user_a)
        self.assertEqual(self.client.get(self.list_url).data["count"], 1)

        self.client.force_authenticate(self.user_b)
        self.assertEqual(self.client.get(self.list_url).data["count"], 1)

    def test_search_never_returns_another_tenants_party(self):
        """A search term that matches B's data must not leak it to A."""
        self.client.force_authenticate(self.user_a)
        data = self.client.get(self.list_url, {"search": "Beta"}).data
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["results"], [])

    def test_autocomplete_search_endpoint_is_scoped(self):
        """The lightweight /parties/search/ autocomplete must obey the same rule."""
        url = reverse("party-search")
        self.client.force_authenticate(self.user_a)
        data = self.client.get(url, {"q": "Beta"}).data
        self.assertEqual(data, [])

        data = self.client.get(url, {"q": "Alpha"}).data
        self.assertEqual([p["name"] for p in data], ["Alpha Customer"])

    def test_filters_do_not_leak_other_tenants(self):
        self.client.force_authenticate(self.user_a)
        for params in (
            {"party_type": "CUSTOMER"},
            {"party_type": "SUPPLIER"},
            {"party_type": "BOTH"},
            {"is_active": "true"},
            {"ordering": "-created_at"},
        ):
            with self.subTest(params=params):
                data = self.client.get(self.list_url, params).data
                names = {p["name"] for p in data["results"]}
                self.assertNotIn("Beta Customer", names)
                self.assertTrue(names <= {"Alpha Customer"})

    # ---------- retrieve / update / delete ----------
    def test_cannot_retrieve_another_tenants_party(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.get(reverse("party-detail", args=[self.party_b.pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_update_another_tenants_party(self):
        self.client.force_authenticate(self.user_a)
        url = reverse("party-detail", args=[self.party_b.pk])
        for method in ("patch", "put"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    url, {"name": "Hijacked"}, format="json"
                )
                self.assertEqual(response.status_code, 404)
        self.party_b.refresh_from_db()
        self.assertEqual(self.party_b.name, "Beta Customer")

    def test_cannot_delete_another_tenants_party(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.delete(reverse("party-detail", args=[self.party_b.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Party.objects.filter(pk=self.party_b.pk).exists())

    def test_cannot_restore_another_tenants_deleted_party(self):
        deleted = make_party(self.business_b, name="Beta Deleted", is_active=False)
        self.client.force_authenticate(self.user_a)
        response = self.client.post(reverse("party-restore", args=[deleted.pk]))
        self.assertEqual(response.status_code, 404)
        deleted.refresh_from_db()
        self.assertFalse(deleted.is_active)

    # ---------- writes ----------
    def test_created_party_is_stamped_with_callers_business(self):
        self.client.force_authenticate(self.user_a)
        response = self.client.post(self.list_url, VALID_PARTY, format="json")
        self.assertEqual(response.status_code, 201)
        created = Party.objects.get(pk=response.data["id"])
        self.assertEqual(created.business, self.business_a)
        self.assertEqual(created.business_id, self.business_a.pk)

    def test_client_cannot_assign_someone_elses_business(self):
        """The business FK is set by the view, never by the payload."""
        self.client.force_authenticate(self.user_a)
        payload = {**VALID_PARTY, "business": self.business_b.pk}
        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        created = Party.objects.get(pk=response.data["id"])
        self.assertEqual(created.business, self.business_a)

    def test_duplicate_gstin_check_is_scoped_per_business(self):
        """The same GSTIN is allowed for two different businesses, but not twice in one."""
        # Give Beta the shared GSTIN, so a second Beta party must be rejected.
        make_party(self.business_b, name="Beta Registered", gstin=VALID_PARTY["gstin"])
        self.client.force_authenticate(self.user_b)
        response = self.client.post(self.list_url, VALID_PARTY, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("gstin", response.data["errors"])

        # A third business may reuse the very same GSTIN.
        self.user_c, business_c = make_user("c@example.com", "Gamma Foods")
        self.client.force_authenticate(self.user_c)
        response = self.client.post(self.list_url, VALID_PARTY, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Party.objects.get(pk=response.data["id"]).business, business_c)

    def test_database_constraint_blocks_duplicate_gstin_within_a_business(self):
        make_party(self.business_a, gstin="29AAACA1234A1Z5", pan="AAACA1234A")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_party(self.business_a, gstin="29AAACA1234A1Z5", pan="", name="Dup")

    # ---------- auth ----------
    def test_anonymous_access_is_rejected(self):
        for url in (
            self.list_url,
            reverse("party-detail", args=[self.party_a.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 401)

    def test_superuser_without_profile_gets_one_created(self):
        """A bare superuser has no BusinessProfile; get_business() must make one."""
        admin = User.objects.create_superuser(email="root@example.com", password=PASSWORD)
        self.assertFalse(BusinessProfile.objects.filter(user=admin).exists())

        self.client.force_authenticate(admin)
        response = self.client.post(
            self.list_url,
            {**VALID_PARTY, "gstin": "", "pan": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 201)  # no RelatedObjectDoesNotExist
        created = Party.objects.get(pk=response.data["id"])
        self.assertEqual(created.business.user, admin)


# ===========================================================================
# CRUD behaviour
# ===========================================================================
class PartyCrudTests(APITestCase):
    def setUp(self):
        self.user, self.business = make_user("owner@example.com", "Solo Traders")
        self.list_url = reverse("party-list")
        self.client.force_authenticate(self.user)

    def test_create_stores_uppercase_gstin_and_pan(self):
        response = self.client.post(
            self.list_url,
            {**VALID_PARTY, "gstin": "36abccs2942r1zr", "pan": "abccs2942r"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["gstin"], "36ABCCS2942R1ZR")
        self.assertEqual(response.data["pan"], "ABCCS2942R")

    def test_create_auto_fills_state_and_pan_from_gstin(self):
        payload = {k: v for k, v in VALID_PARTY.items() if k not in ("state_code", "pan")}
        response = self.client.post(self.list_url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["state_code"], "36")
        self.assertEqual(response.data["pan"], "ABCCS2942R")

    def test_gstin_state_mismatch_is_rejected(self):
        response = self.client.post(
            self.list_url, {**VALID_PARTY, "state_code": "27"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("gstin", response.data["errors"])

    def test_pan_must_match_gstin_characters(self):
        response = self.client.post(
            self.list_url, {**VALID_PARTY, "pan": "ZZZZZ9999Z"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("pan", response.data["errors"])

    def test_unregistered_party_without_gstin_is_allowed(self):
        response = self.client.post(
            self.list_url,
            {**VALID_PARTY, "name": "Walk-in Buyer", "gstin": "", "pan": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["gstin"], "")
        self.assertFalse(response.data["is_registered"])

    def test_invalid_mobile_is_rejected(self):
        response = self.client.post(
            self.list_url, {**VALID_PARTY, "mobile": "1234567890"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("mobile", response.data["errors"])

    def test_delete_is_a_soft_delete(self):
        party = make_party(self.business, name="Temp Buyer")
        response = self.client.delete(reverse("party-detail", args=[party.pk]))
        self.assertEqual(response.status_code, 204)
        party.refresh_from_db()
        self.assertFalse(party.is_active)          # flagged, not removed
        self.assertTrue(Party.objects.filter(pk=party.pk).exists())

    def test_soft_deleted_party_leaves_the_default_list(self):
        make_party(self.business, name="Visible")
        make_party(self.business, name="Hidden", is_active=False)

        names = {p["name"] for p in self.client.get(self.list_url).data["results"]}
        self.assertEqual(names, {"Visible"})

        names = {
            p["name"]
            for p in self.client.get(self.list_url, {"is_active": "false"}).data["results"]
        }
        self.assertEqual(names, {"Hidden"})

    def test_restore_brings_the_party_back(self):
        party = make_party(self.business, name="Resurrected", is_active=False)
        response = self.client.post(reverse("party-restore", args=[party.pk]))
        self.assertEqual(response.status_code, 200)
        party.refresh_from_db()
        self.assertTrue(party.is_active)

    def test_partial_update_leaves_other_fields_untouched(self):
        party = make_party(self.business)
        self.client.patch(
            reverse("party-detail", args=[party.pk]), {"name": "Renamed"}, format="json"
        )
        party.refresh_from_db()
        self.assertEqual(party.name, "Renamed")
        self.assertEqual(party.mobile, VALID_PARTY["mobile"])
        self.assertEqual(party.opening_balance, Decimal("1500.50"))

    def test_partial_update_of_name_alone_keeps_stored_state(self):
        """Regression: PATCHing only the name must not demand a state_code."""
        party = make_party(self.business, state_code="27", gstin="", pan="")
        response = self.client.patch(
            reverse("party-detail", args=[party.pk]), {"name": "Just Renamed"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        party.refresh_from_db()
        self.assertEqual(party.state_code, "27")

    def test_partial_update_can_still_change_the_state(self):
        party = make_party(self.business, state_code="27")
        response = self.client.patch(
            reverse("party-detail", args=[party.pk]), {"state_code": "33"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        party.refresh_from_db()
        self.assertEqual(party.state_code, "33")

    def test_omitting_state_entirely_is_rejected_with_a_clear_message(self):
        response = self.client.post(
            self.list_url,
            {k: v for k, v in VALID_PARTY.items() if k != "state_code"} | {"gstin": "", "pan": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("state_code", response.data["errors"])


# ===========================================================================
# Filters and search
# ===========================================================================
class PartyFilterTests(APITestCase):
    def setUp(self):
        self.user, self.business = make_user("owner@example.com", "Filter Traders")
        self.list_url = reverse("party-list")
        self.client.force_authenticate(self.user)

        make_party(self.business, name="Cust One", party_type="CUSTOMER",
                   gstin="", pan="", email="cust1@example.com")
        make_party(self.business, name="Supp One", party_type="SUPPLIER",
                   gstin="", pan="", mobile="9000000001")
        make_party(self.business, name="Both One", party_type="BOTH",
                   gstin="", pan="", mobile="9000000002")

    def names(self, **params):
        return {p["name"] for p in self.client.get(self.list_url, params).data["results"]}

    def test_customer_filter_includes_both_parties(self):
        self.assertEqual(self.names(party_type="CUSTOMER"), {"Cust One", "Both One"})

    def test_supplier_filter_includes_both_parties(self):
        self.assertEqual(self.names(party_type="SUPPLIER"), {"Supp One", "Both One"})

    def test_both_filter_returns_only_both(self):
        self.assertEqual(self.names(party_type="BOTH"), {"Both One"})

    def test_no_filter_returns_everything(self):
        self.assertEqual(self.names(), {"Cust One", "Supp One", "Both One"})

    def test_search_by_name(self):
        self.assertEqual(self.names(search="Supp"), {"Supp One"})

    def test_search_by_mobile(self):
        self.assertEqual(self.names(search="9000000002"), {"Both One"})

    def test_search_by_email(self):
        self.assertEqual(self.names(search="cust1@"), {"Cust One"})

    def test_ordering_by_name_desc(self):
        names = [p["name"] for p in self.client.get(self.list_url, {"ordering": "-name"}).data["results"]]
        self.assertEqual(names, sorted(names, reverse=True))


# ===========================================================================
# Model helpers
# ===========================================================================
class PartySoftDeleteReuseTests(APITestCase):
    """
    A soft-deleted party frees its GSTIN/PAN for re-use, and restoring a party
    whose identifier has since been taken fails with a clear 400 rather than an
    IntegrityError (500).
    """

    def setUp(self):
        self.user, self.business = make_user("owner@example.com", "Reuse Traders")

    def test_soft_deleted_partys_gstin_can_be_reused(self):
        deleted = make_party(self.business, name="Deleted One", gstin="", pan="",
                             mobile="9000000001")
        deleted.gstin = "36ABCCS2942R1ZR"
        deleted.pan = "ABCCS2942R"
        deleted.is_active = False
        deleted.save()

        # Same GSTIN, different party, still active -> allowed now.
        replacement = make_party(self.business, name="Replacement",
                                 gstin="36ABCCS2942R1ZR", pan="ABCCS2942R",
                                 mobile="9000000002")
        self.assertEqual(replacement.gstin, "36ABCCS2942R1ZR")

    def test_duplicate_still_blocked_while_both_are_active(self):
        make_party(self.business, name="First", gstin="36ABCCS2942R1ZR", pan="ABCCS2942R",
                   mobile="9000000003")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_party(self.business, name="Second", gstin="36ABCCS2942R1ZR",
                           pan="", mobile="9000000004")

    def test_restore_succeeds_when_identifier_is_free(self):
        party = make_party(self.business, name="Restorable", gstin="", pan="",
                           mobile="9000000005")
        party.gstin = "29AAACA1234A1Z5"
        party.pan = "AAACA1234A"
        party.is_active = False
        party.save()

        self.client.force_authenticate(self.user)
        response = self.client.post(reverse("party-restore", args=[party.pk]))
        self.assertEqual(response.status_code, 200)
        party.refresh_from_db()
        self.assertTrue(party.is_active)

    def test_restore_returns_400_when_gstin_was_taken_meanwhile(self):
        released = make_party(self.business, name="Released", gstin="", pan="",
                              mobile="9000000006")
        released.gstin = "29AAACA1234A1Z5"
        released.pan = "AAACA1234A"
        released.is_active = False
        released.save()

        make_party(self.business, name="New owner", gstin="29AAACA1234A1Z5", pan="",
                   mobile="9000000007")

        self.client.force_authenticate(self.user)
        response = self.client.post(reverse("party-restore", args=[released.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "DUPLICATE_IDENTIFIER")
        self.assertIn("GSTIN", response.data["message"])
        released.refresh_from_db()
        self.assertFalse(released.is_active)  # not half-restored


class PartyShippingStateTests(APITestCase):
    """shipping_state_code drives place of supply for goods on an invoice."""

    def setUp(self):
        self.user, self.business = make_user("owner@example.com", "Shipping Traders")
        self.list_url = reverse("party-list")
        self.client.force_authenticate(self.user)

    def payload(self, **overrides):
        base = {**VALID_PARTY, "gstin": "", "pan": ""}
        base.update(overrides)
        return base

    def test_shipping_state_is_accepted_with_a_shipping_address(self):
        response = self.client.post(
            self.list_url,
            self.payload(shipping_address="9 Warehouse Rd", shipping_city="Pune",
                         shipping_pincode="411001", shipping_state_code="27"),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["shipping_state_code"], "27")

    def test_shipping_state_rejected_without_a_shipping_address(self):
        response = self.client.post(
            self.list_url, self.payload(shipping_state_code="27"), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("shipping_state_code", response.data["errors"])

    def test_invalid_shipping_state_is_rejected(self):
        response = self.client.post(
            self.list_url,
            self.payload(shipping_address="9 Warehouse Rd", shipping_state_code="99"),
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_blank_shipping_state_is_the_default(self):
        response = self.client.post(
            self.list_url,
            self.payload(shipping_address="9 Warehouse Rd", shipping_state_code=""),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["shipping_state_code"], "")

    def test_patch_clearing_shipping_address_must_clear_shipping_state_too(self):
        party = make_party(self.business, name="Has shipping", gstin="", pan="",
                           mobile="9000000008")
        party.shipping_address = "9 Warehouse Rd"
        party.shipping_state_code = "27"
        party.save()

        response = self.client.patch(
            reverse("party-detail", args=[party.pk]),
            {"shipping_address": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("shipping_state_code", response.data["errors"])

    def test_patch_of_an_unrelated_field_keeps_the_shipping_state(self):
        party = make_party(self.business, name="Keep shipping", gstin="", pan="",
                           mobile="9000000009")
        party.shipping_address = "9 Warehouse Rd"
        party.shipping_state_code = "27"
        party.save()

        response = self.client.patch(
            reverse("party-detail", args=[party.pk]), {"name": "Renamed"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        party.refresh_from_db()
        self.assertEqual(party.shipping_state_code, "27")

    def test_shipping_state_does_not_leak_across_tenants(self):
        """The field is per-party, so tenant scoping must be unaffected."""
        self.user_b, self.business_b = make_user("b@example.com", "Beta Stores")
        make_party(self.business_b, name="Beta item", shipping_address="1 Elsewhere",
                   shipping_state_code="33", gstin="", pan="", mobile="9000000010")

        self.client.force_authenticate(self.user)
        names = {p["name"] for p in self.client.get(self.list_url).data["results"]}
        self.assertNotIn("Beta item", names)


class PartyModelTests(APITestCase):
    def setUp(self):
        self.user, self.business = make_user("owner@example.com", "Model Traders")

    def test_opening_balance_signed_is_decimal_and_signed_by_direction(self):
        credit = make_party(self.business, name="Owes Us",
                            opening_balance=Decimal("1500.50"), balance_type="CREDIT")
        debit = make_party(self.business, name="We Owe",
                           opening_balance=Decimal("1500.50"), balance_type="DEBIT")

        self.assertIsInstance(credit.opening_balance_signed, Decimal)
        self.assertEqual(credit.opening_balance_signed, Decimal("1500.50"))
        self.assertEqual(debit.opening_balance_signed, Decimal("-1500.50"))

    def test_opening_balance_signed_survives_repeated_arithmetic(self):
        """Decimal arithmetic must stay exact where float would drift."""
        party = make_party(self.business, name="Rounding",
                           opening_balance=Decimal("0.10"), balance_type="CREDIT")
        total = party.opening_balance_signed
        for _ in range(10):
            total += Decimal("0.10")
        self.assertEqual(total, Decimal("1.10"))
        self.assertEqual(str(total), "1.10")

    def test_shipping_address_falls_back_to_billing(self):
        party = make_party(self.business, name="Fallback",
                           billing_address="1 Main St", billing_city="Pune",
                           billing_pincode="411001")
        self.assertEqual(
            party.get_shipping_address_lines(), ["1 Main St", "Pune", "411001"]
        )

    def test_shipping_address_is_used_when_present(self):
        party = make_party(self.business, name="Separate",
                           shipping_address="9 Warehouse Rd",
                           shipping_city="Navi Mumbai", shipping_pincode="400708")
        self.assertEqual(
            party.get_shipping_address_lines(),
            ["9 Warehouse Rd", "Navi Mumbai", "400708"],
        )

    def test_is_registered_reflects_gstin_presence(self):
        self.assertTrue(make_party(self.business, name="Reg", gstin="29AAACA1234A1Z5",
                                   pan="AAACA1234A").is_registered)
        self.assertFalse(make_party(self.business, name="Unreg", gstin="", pan="",
                                    mobile="9111111111").is_registered)

    def test_display_state_returns_readable_name(self):
        party = make_party(self.business, name="Telangana Party", state_code="36")
        self.assertEqual(party.get_display_state(), "Telangana")