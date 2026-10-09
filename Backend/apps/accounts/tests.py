"""Tests: authentication, business profile, and tenant isolation of the profile API."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.parties.models import Party

from .models import BusinessProfile
from .services import WALK_IN_PARTY_NAME, get_business, get_or_create_walk_in_party
from .test_base import AccountsTestCase

User = get_user_model()
STRONG_PASSWORD = "Str0ng!Pass#2026"


def make_user(email: str, trade_name: str) -> "User": # type: ignore
    """Create a user together with their business profile."""
    user = User.objects.create_user(email=email, password=STRONG_PASSWORD)
    BusinessProfile.objects.create(user=user, trade_name=trade_name, company_name=trade_name)
    return user


def make_business(email: str, trade_name: str) -> BusinessProfile:
    """Create a user and return just their business profile."""
    return make_user(email, trade_name).business


class AuthTests(AccountsTestCase):
    def test_register_creates_user_business_and_tokens(self):
        response = self.client.post(
            reverse("register"),
            {"email": "Owner@Example.com", "password": STRONG_PASSWORD, "trade_name": "Swipe Demo"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["email"], "owner@example.com")  # stored lower case
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        user = User.objects.get(email="owner@example.com")
        self.assertEqual(user.business.trade_name, "Swipe Demo")

    def test_register_rejects_weak_password(self):
        response = self.client.post(
            reverse("register"),
            {"email": "owner@example.com", "password": "12345678", "trade_name": "Swipe Demo"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "VALIDATION_ERROR")
        self.assertIn("password", response.data["errors"])
        self.assertFalse(User.objects.filter(email="owner@example.com").exists())

    def test_register_rejects_duplicate_email_ignoring_case(self):
        make_user("owner@example.com", "First")
        response = self.client.post(
            reverse("register"),
            {"email": "OWNER@example.com", "password": STRONG_PASSWORD, "trade_name": "Second"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data["errors"])

    def test_login_with_email_returns_tokens(self):
        make_user("owner@example.com", "Swipe Demo")
        response = self.client.post(
            reverse("login"),
            {"email": "owner@example.com", "password": STRONG_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_profile_requires_login_and_uses_standard_error_format(self):
        response = self.client.get(reverse("business-profile"))
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["error"], "NOT_AUTHENTICATED")


class ProfileIsolationTests(AccountsTestCase):
    """User A must never see or change User B's business."""

    def setUp(self):
        self.user_a = make_user("a@example.com", "Alpha Traders")
        self.user_b = make_user("b@example.com", "Beta Stores")
        self.url = reverse("business-profile")

    def test_each_user_only_sees_own_profile(self):
        self.client.force_authenticate(user=self.user_a)
        self.assertEqual(self.client.get(self.url).data["trade_name"], "Alpha Traders")

        self.client.force_authenticate(user=self.user_b)
        self.assertEqual(self.client.get(self.url).data["trade_name"], "Beta Stores")

    def test_update_only_changes_own_profile(self):
        self.client.force_authenticate(user=self.user_a)
        response = self.client.patch(self.url, {"trade_name": "Alpha Renamed"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_business(self.user_a).trade_name, "Alpha Renamed")
        self.assertEqual(get_business(self.user_b).trade_name, "Beta Stores")

    def test_gstin_must_match_state_code(self):
        self.client.force_authenticate(user=self.user_a)
        bad = self.client.patch(
            self.url, {"gstin": "36ABCCS2942R1ZR", "state_code": "27"}, format="json"
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn("gstin", bad.data["errors"])

        good = self.client.patch(
            self.url, {"gstin": "36abccs2942r1zr", "state_code": "36"}, format="json"
        )
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.data["gstin"], "36ABCCS2942R1ZR")  # upper-cased by the API

    def test_profile_owner_cannot_be_changed_through_the_api(self):
        self.client.force_authenticate(user=self.user_a)
        payload = {"trade_name": "Alpha", "company_name": "Alpha", "user": self.user_b.id}
        response = self.client.put(self.url, payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_business(self.user_a).user_id, self.user_a.id)
        self.assertEqual(get_business(self.user_b).user_id, self.user_b.id)


class InvoicePreferencesTests(AccountsTestCase):
    """Step A: the 1.4 invoice preferences on the business profile."""

    def setUp(self):
        self.user = make_user("owner@example.com", "Prefs Traders")
        self.url = reverse("business-profile")

    def test_new_business_defaults_are_safe(self):
        """Unregistered + no rounding + INV prefix. Charging no tax is the safe default."""
        self.client.force_authenticate(self.user)
        data = self.client.get(self.url).data
        self.assertEqual(data["gst_registration_type"], "UNREGISTERED")
        self.assertFalse(data["round_invoice_total"])
        self.assertEqual(data["invoice_number_prefix"], "INV")

    def test_invoice_preferences_can_be_saved(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            self.url,
            {
                "gst_registration_type": "REGULAR",
                "round_invoice_total": True,
                "invoice_number_prefix": "acme",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["gst_registration_type"], "REGULAR")
        self.assertTrue(response.data["round_invoice_total"])
        self.assertEqual(response.data["invoice_number_prefix"], "ACME")  # upper-cased

    def test_prefix_rejects_characters_outside_the_rule46_set(self):
        self.client.force_authenticate(self.user)
        for bad in ("TOOLONG", "IN V", "INV/26", "", "IN#"):
            with self.subTest(prefix=bad):
                response = self.client.patch(
                    self.url, {"invoice_number_prefix": bad}, format="json"
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("invoice_number_prefix", response.data["errors"])

    def test_registration_type_rejects_unknown_values(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            self.url, {"gst_registration_type": "SOMETHING"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_preferences_are_per_business(self):
        self.user_b = make_user("b@example.com", "Beta Stores")
        self.client.force_authenticate(self.user_b)
        self.client.patch(
            self.url, {"invoice_number_prefix": "BETA"}, format="json"
        )
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(self.url).data["invoice_number_prefix"], "INV")


class WalkInPartyTests(AccountsTestCase):
    """
    One walk-in customer per business, created idempotently and found by FLAG.

    Decision 21 changed two things this class used to assert:
      * the walk-in is looked up by `is_walk_in`, not by name;
      * it stores **no** state - the business state is resolved at invoice time,
        so a later business-state change cannot leave a stale walk-in behind.
    """

    def test_registration_creates_a_walk_in_party(self):
        response = self.client.post(
            reverse("register"),
            {"email": "walk@example.com", "password": STRONG_PASSWORD, "trade_name": "Corner Shop"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="walk@example.com")
        walk_in = Party.objects.get(business=user.business, is_walk_in=True)
        self.assertEqual(walk_in.name, WALK_IN_PARTY_NAME)
        self.assertEqual(walk_in.party_type, Party.PartyType.CUSTOMER)
        self.assertEqual(walk_in.gstin, "")
        self.assertTrue(walk_in.is_active)

    def test_the_walk_in_is_flagged_and_stores_no_state(self):
        business = make_business("s@example.com", "State Shop")
        business.state_code = "27"
        business.save(update_fields=["state_code", "updated_at"])
        walk_in = get_or_create_walk_in_party(business)

        self.assertTrue(walk_in.is_walk_in)
        # Decision 21: resolved from the business at invoice time, never stored.
        self.assertEqual(walk_in.state_code, "")
        self.assertRegex(walk_in.mobile, r"^[6-9][0-9]{9}$")

    def test_creating_twice_returns_the_same_party(self):
        business = make_business("twice@example.com", "Twice Shop")
        first = get_or_create_walk_in_party(business)
        second = get_or_create_walk_in_party(business)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Party.objects.filter(business=business, is_walk_in=True).count(), 1)

    def test_the_lookup_survives_a_rename(self):
        """Why it is a flag and not a name lookup."""
        business = make_business("rename@example.com", "Rename Shop")
        walk_in = get_or_create_walk_in_party(business)
        Party.objects.filter(pk=walk_in.pk).update(name="Cash Sale (edited)")

        self.assertEqual(get_or_create_walk_in_party(business).pk, walk_in.pk)

    def test_a_business_state_change_needs_no_walk_in_update(self):
        """
        The old behaviour back-filled the walk-in's stored state. Decision 21
        removed that entirely, so there is nothing left to go stale.
        """
        business = make_business("late@example.com", "Late Shop")
        business.state_code = ""  # brand-new profile, no state yet
        business.save(update_fields=["state_code", "updated_at"])
        walk_in = get_or_create_walk_in_party(business)
        self.assertEqual(walk_in.state_code, "")

        # The user later completes their profile.
        business.state_code = "33"
        business.save(update_fields=["state_code", "updated_at"])

        again = get_or_create_walk_in_party(business)
        self.assertEqual(again.pk, walk_in.pk)
        again.refresh_from_db()
        self.assertEqual(again.state_code, "", "the walk-in must still store no state")

        # ...and the next invoice picks up the new state from the business.
        from apps.invoices.services.draft import resolve_place_of_supply

        self.assertEqual(resolve_place_of_supply(again, [], business), "33")

    def test_walk_in_parties_are_scoped_per_tenant(self):
        business_a = make_business("wa@example.com", "Walk A")
        business_b = make_business("wb@example.com", "Walk B")
        walk_a = get_or_create_walk_in_party(business_a)
        walk_b = get_or_create_walk_in_party(business_b)
        self.assertNotEqual(walk_a.pk, walk_b.pk)
        self.assertEqual(walk_a.business, business_a)
        self.assertEqual(walk_b.business, business_b)


class ThrottleConfigTests(AccountsTestCase):
    """
    Throttling is configured globally, and the unauthenticated auth endpoints
    share one tighter scope. These prove the config is actually wired to views,
    not merely present in settings - the gap 1.4.9 finding #3 found, where
    register and refresh had no scope at all.
    """

    def test_throttle_classes_are_configured(self):
        settings_rf = settings.REST_FRAMEWORK
        self.assertIn(
            "rest_framework.throttling.ScopedRateThrottle",
            settings_rf["DEFAULT_THROTTLE_CLASSES"],
        )
        for scope in ("anon", "user", "auth", "login", "preview"):
            self.assertIn(scope, settings_rf["DEFAULT_THROTTLE_RATES"])

    def test_every_unauthenticated_auth_endpoint_uses_the_auth_scope(self):
        from apps.accounts.urls import (
            AUTH_SCOPE,
            ThrottledRegisterView,
            ThrottledTokenObtainPairView,
            ThrottledTokenRefreshView,
        )

        for view in (
            ThrottledRegisterView,
            ThrottledTokenObtainPairView,
            ThrottledTokenRefreshView,
        ):
            with self.subTest(view=view.__name__):
                self.assertEqual(view.throttle_scope, AUTH_SCOPE)

    def test_repeated_login_attempts_eventually_get_throttled(self):
        """A wrong password repeatedly must start returning 429, not 401 forever."""
        self.client.post(
            reverse("login"), {"email": "nobody@example.com", "password": "Wrong!Pass#1"},
            format="json",
        )
        statuses = []
        for _ in range(15):
            response = self.client.post(
                reverse("login"),
                {"email": "nobody@example.com", "password": "Wrong!Pass#1"},
                format="json",
            )
            statuses.append(response.status_code)
            if response.status_code == 429:
                break
        self.assertIn(429, statuses, f"never throttled; statuses were {set(statuses)}")


class GetBusinessTests(AccountsTestCase):
    def test_creates_missing_profile_exactly_once(self):
        admin = User.objects.create_superuser(email="admin@example.com", password=STRONG_PASSWORD)
        self.assertFalse(BusinessProfile.objects.filter(user=admin).exists())

        first = get_business(admin)
        second = get_business(admin)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(BusinessProfile.objects.filter(user=admin).count(), 1)

    def test_returns_existing_profile(self):
        user = make_user("owner@example.com", "Swipe Demo")
        self.assertEqual(get_business(user).trade_name, "Swipe Demo")
