"""
Multiple logins: session recording, listing, revocation.

The security claim these tests pin
---------------------------------
Before this, a refresh token lived 7 days and **nothing could invalidate it**.
The tests below assert that a revoked device can no longer refresh, while
another device on the same account keeps working. If any of them fail, a
sign-out is cosmetic again.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import UserSession
from apps.accounts.sessions import describe_device
from apps.accounts.test_base import AccountsTestCase

User = get_user_model()

PASSWORD = "Str0ngPass!234"
LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
SESSIONS_URL = "/api/v1/auth/sessions/"
REVOKE_OTHERS_URL = "/api/v1/auth/sessions/revoke-others/"
REGISTER_URL = "/api/v1/auth/register/"
PROFILE_URL = "/api/v1/business/profile/"

#: A browser-ish User-Agent, so the device label is not just "Unknown device".
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def make_user(email="owner@example.com"):
    return User.objects.create_user(email=email, password=PASSWORD, first_name="Owner")


def login(client, user, user_agent=DESKTOP_UA):
    """Log in over HTTP and return (access, refresh)."""
    response = client.post(
        LOGIN_URL,
        {"email": user.email, "password": PASSWORD},
        format="json",
        HTTP_USER_AGENT=user_agent,
    )
    if response.status_code != 200:
        raise AssertionError(f"login failed: {response.status_code} {response.content[:200]}")
    return response.json()["access"], response.json()["refresh"]


class DeviceLabelTests(AccountsTestCase):
    def test_a_windows_chrome_agent_is_understood(self):
        self.assertEqual(describe_device(DESKTOP_UA), "Chrome on Windows")

    def test_an_iphone_safari_agent_is_understood(self):
        self.assertEqual(describe_device(MOBILE_UA), "Safari on iPhone")

    def test_an_unknown_agent_does_not_produce_an_empty_label(self):
        self.assertTrue(describe_device("").strip())
        self.assertTrue(describe_device("SomeCrawler/1.0").strip())


class SessionRecordingTests(AccountsTestCase):
    def setUp(self):
        cache.clear()  # the auth throttle would otherwise bleed between tests
        self.user = make_user()
        self.client_ = APIClient()

    def test_login_creates_a_session_row(self):
        access, refresh = login(self.client_, self.user)
        self.assertEqual(UserSession.objects.filter(user=self.user).count(), 1)
        session = UserSession.objects.get(user=self.user)
        self.assertTrue(session.device)
        self.assertIsNone(session.revoked_at)

    def test_registering_also_creates_a_session(self):
        response = APIClient().post(
            REGISTER_URL,
            {"email": "new@example.com", "password": PASSWORD, "trade_name": "New Shop"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content[:200])
        self.assertEqual(UserSession.objects.filter(user__email="new@example.com").count(), 1)

    def test_two_devices_produce_two_sessions(self):
        laptop = APIClient()
        phone = APIClient()
        login(laptop, self.user, DESKTOP_UA)
        login(phone, self.user, MOBILE_UA)
        self.assertEqual(UserSession.objects.filter(user=self.user).count(), 2)

        devices = set(UserSession.objects.filter(user=self.user).values_list("device", flat=True))
        self.assertEqual(len(devices), 2, devices)


class SessionListTests(AccountsTestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client_ = APIClient()
        self.access, self.refresh = login(self.client_, self.user)

    def authed(self):
        c = APIClient()
        c.force_authenticate(user=self.user)
        return c

    def test_the_list_requires_authentication(self):
        self.assertIn(APIClient().get(SESSIONS_URL).status_code, (401, 403))

    def test_the_list_shows_every_active_device(self):
        login(APIClient(), self.user, MOBILE_UA)
        response = self.authed().get(SESSIONS_URL)
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertTrue(response.json()["success"])
        self.assertEqual(response.json()["count"], 2)

    def test_a_revoked_device_disappears_from_the_list(self):
        login(APIClient(), self.user, MOBILE_UA)  # now two devices
        victim = UserSession.objects.filter(user=self.user).first()
        self.assertEqual(self.authed().delete(f"{SESSIONS_URL}{victim.pk}/").status_code, 200)
        response = self.authed().get(SESSIONS_URL)
        self.assertEqual(response.json()["count"], 1)
        self.assertNotIn(victim.pk, [s["id"] for s in response.json()["sessions"]])

    def test_one_user_cannot_revoke_another_users_session(self):
        rival = make_user("rival@example.com")
        login(APIClient(), rival, MOBILE_UA)
        rival_session = UserSession.objects.get(user=rival)

        self.assertEqual(
            self.authed().delete(f"{SESSIONS_URL}{rival_session.pk}/").status_code, 404
        )
        rival_session.refresh_from_db()
        self.assertIsNone(rival_session.revoked_at, "another tenant's session was revoked")

    def test_an_unknown_session_id_is_404_not_500(self):
        self.assertEqual(self.authed().delete(f"{SESSIONS_URL}999999/").status_code, 404)


class RevocationActuallyRevokesTests(AccountsTestCase):
    """
    The point of the whole feature: a revoked device must stop working, and
    only that device.
    """

    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.laptop = APIClient()
        self.phone = APIClient()
        self.laptop_access, self.laptop_refresh = login(self.laptop, self.user, DESKTOP_UA)
        self.phone_access, self.phone_refresh = login(self.phone, self.user, MOBILE_UA)

    def _authed(self, access=None):
        c = APIClient()
        if access:
            c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        else:
            c.force_authenticate(user=self.user)
        return c

    def _revoke_by_refresh(self, refresh):
        session = UserSession.objects.get(user=self.user, jti=RefreshToken(refresh)["jti"])
        response = self._authed().delete(f"{SESSIONS_URL}{session.pk}/")
        self.assertEqual(response.status_code, 200, response.content[:200])

    def test_revoking_one_device_stops_its_refresh(self):
        self._revoke_by_refresh(self.laptop_refresh)

        response = APIClient().post(REFRESH_URL, {"refresh": self.laptop_refresh}, format="json")
        self.assertEqual(response.status_code, 401, "a revoked device could still refresh")

    def test_revoking_one_device_leaves_the_other_working(self):
        self._revoke_by_refresh(self.laptop_refresh)

        response = APIClient().post(REFRESH_URL, {"refresh": self.phone_refresh}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertIn("access", response.json())

    def test_logout_blacklists_the_refresh_token(self):
        """Logout must be real, not just the browser deleting its copy."""
        response = self.laptop.post(
            LOGOUT_URL, {"refresh": self.laptop_refresh}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content[:200])

        again = APIClient().post(REFRESH_URL, {"refresh": self.laptop_refresh}, format="json")
        self.assertEqual(again.status_code, 401, "logout did not invalidate the token")

        # The phone is untouched.
        other = APIClient().post(REFRESH_URL, {"refresh": self.phone_refresh}, format="json")
        self.assertEqual(other.status_code, 200)

    def test_logout_without_a_token_is_a_clear_400(self):
        response = self.laptop.post(LOGOUT_URL, {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "REFRESH_TOKEN_REQUIRED")

    def test_logging_out_twice_is_not_an_error(self):
        first = self.laptop.post(LOGOUT_URL, {"refresh": self.laptop_refresh}, format="json")
        self.assertEqual(first.status_code, 200)
        second = self.laptop.post(LOGOUT_URL, {"refresh": self.laptop_refresh}, format="json")
        self.assertEqual(second.status_code, 200, response_body(second))
        self.assertTrue(second.json().get("already_gone"))

    def test_logout_with_a_garbage_token_is_not_a_crash(self):
        response = self.laptop.post(LOGOUT_URL, {"refresh": "not-a-token"}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_revoke_others_keeps_the_calling_device_signed_in(self):
        """The button says 'sign out everywhere ELSE' - it must not sign you out."""
        caller = APIClient()
        caller.force_authenticate(user=self.user)
        response = caller.post(
            REVOKE_OTHERS_URL, {"refresh": self.phone_refresh}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(response.json()["revoked"], 1)

        still_ok = APIClient().post(REFRESH_URL, {"refresh": self.phone_refresh}, format="json")
        self.assertEqual(still_ok.status_code, 200, "the calling device was logged out")

        gone = APIClient().post(REFRESH_URL, {"refresh": self.laptop_refresh}, format="json")
        self.assertEqual(gone.status_code, 401, "the other device survived")

    def test_a_rotated_token_keeps_its_session_row(self):
        """
        BLACKLIST_AFTER_ROTATION retires the old refresh token on every refresh.
        If the new token did not inherit the session row, the device would vanish
        from the list and 'revoke this device' would never converge.
        """
        before = UserSession.objects.filter(user=self.user).count()
        response = APIClient().post(REFRESH_URL, {"refresh": self.phone_refresh}, format="json")
        self.assertEqual(response.status_code, 200)
        rotated = response.json()["refresh"]

        after = UserSession.objects.filter(user=self.user).count()
        self.assertEqual(after, before + 1, "rotation did not create the new session row")

        # And the new token is revocable in its own right.
        session = UserSession.objects.get(user=self.user, jti=RefreshToken(rotated)["jti"])
        self.assertEqual(self._authed().delete(f"{SESSIONS_URL}{session.pk}/").status_code, 200)
        self.assertEqual(
            APIClient().post(REFRESH_URL, {"refresh": rotated}, format="json").status_code, 401
        )

    def test_the_old_token_is_dead_after_rotation(self):
        """The other half of BLACKLIST_AFTER_ROTATION."""
        APIClient().post(REFRESH_URL, {"refresh": self.phone_refresh}, format="json")
        self.assertEqual(
            APIClient().post(REFRESH_URL, {"refresh": self.phone_refresh}, format="json").status_code,
            401,
            "a rotated-away refresh token is still usable - BLACKLIST_AFTER_ROTATION is off",
        )


class AuthScopeThrottleTests(AccountsTestCase):
    """Finding #3: register and refresh had no throttle scope at all."""

    def setUp(self):
        cache.clear()
        self.user = make_user()

    def test_every_unauthenticated_auth_endpoint_has_a_scope(self):
        from apps.accounts import urls

        scoped = {
            "register": urls.ThrottledRegisterView.throttle_scope,
            "login": urls.ThrottledTokenObtainPairView.throttle_scope,
            "refresh": urls.ThrottledTokenRefreshView.throttle_scope,
        }
        self.assertEqual(set(scoped.values()), {urls.AUTH_SCOPE})
        for name, scope in scoped.items():
            with self.subTest(endpoint=name):
                self.assertTrue(scope, f"{name} has no throttle_scope")

    def test_the_auth_rate_is_configured(self):
        from django.conf import settings

        self.assertIn("auth", settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])

    def test_refresh_hitting_its_limit_returns_429(self):
        client = APIClient()
        _, refresh = login(client, self.user)
        # One successful refresh is recorded above; burn the rest of the budget.
        statuses = []
        for _ in range(12):
            statuses.append(
                client.post(REFRESH_URL, {"refresh": refresh}, format="json").status_code
            )
        self.assertIn(429, statuses, f"refresh was never throttled: {statuses}")


def response_body(response):
    return response.content[:200]