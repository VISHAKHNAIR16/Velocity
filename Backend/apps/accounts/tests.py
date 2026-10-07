"""Tests: authentication, business profile, and tenant isolation of the profile API."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import BusinessProfile
from .services import get_business

User = get_user_model()
STRONG_PASSWORD = "Str0ng!Pass#2026"


def make_user(email: str, trade_name: str) -> "User": # type: ignore
    """Create a user together with their business profile."""
    user = User.objects.create_user(email=email, password=STRONG_PASSWORD)
    BusinessProfile.objects.create(user=user, trade_name=trade_name, company_name=trade_name)
    return user


class AuthTests(APITestCase):
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


class ProfileIsolationTests(APITestCase):
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


class GetBusinessTests(APITestCase):
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
