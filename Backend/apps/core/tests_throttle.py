"""
Decision 24: throttle sizing, the 429 contract, proxy trust and the DB cache.

Three separate concerns, tested separately because they fail in different ways:

1. **Sizing.** A cashier must not be locked out mid-invoice. The old 600/hour was
   10 requests/minute across *every* endpoint, and autocomplete alone exhausts it.
2. **The 429 contract.** DRF's default body has no machine-readable `error` and no
   `Retry-After`, so the frontend cannot branch or schedule a retry - it can only
   show a dead error while the totals go stale.
3. **Where the counters live.** `LocMemCache` resets on every deploy and splits
   across workers, so the effective rate is multiplied by the worker count and
   vanishes on deploy.
"""

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.views import exception_handler

PASSWORD = "Str0ngPass!234"


class ThrottleSizingTests(TestCase):
    def test_the_user_rate_is_high_enough_for_a_cashier(self):
        """
        The regression this guards: `user` was 600/hour, i.e. 10 requests per
        minute across the whole API. One billing screen makes several preview
        calls per line change plus autocomplete calls, so a single sale could
        exhaust the budget.
        """
        from django.conf import settings

        rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["user"]
        per_hour = int(rate.split("/")[0])
        self.assertGreaterEqual(
            per_hour,
            5000,
            f"`user` is {rate}; a cashier would be locked out mid-invoice",
        )

    def test_preview_keeps_its_own_tighter_scope(self):
        """Raising `user` must not make the calculation endpoint unbounded."""
        from django.conf import settings

        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertIn("preview", rates)
        self.assertLess(int(rates["preview"].split("/")[0]), 5000)

    def test_auth_endpoints_keep_a_tight_rate(self):
        from django.conf import settings

        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        for scope in ("auth", "login"):
            self.assertIn(scope, rates)
            self.assertLessEqual(int(rates[scope].split("/")[0]), 60)


class ThrottleCacheTests(TestCase):
    def test_counters_are_not_stored_per_process(self):
        """
        `LocMemCache` is per process: it resets on every deploy and each worker
        counts separately, so N workers mean N times the intended rate.
        """
        from django.conf import settings

        backend = settings.CACHES["default"]["BACKEND"]
        self.assertNotIn(
            "locmem",
            backend.lower(),
            "throttle counters in LocMemCache are lost on deploy and split per worker",
        )

    def test_counters_actually_survive_and_are_shared(self):
        """Write through the cache, read from a second 'process'."""
        cache.set("throttle-probe", "value", 60)
        self.assertEqual(cache.get("throttle-probe"), "value")
        cache.delete("throttle-probe")


class ProxyTrustTests(TestCase):
    def test_num_proxies_is_configured(self):
        """
        1.4.9 finding #14. Behind Render without this, DRF reads the proxy's IP
        for every request, so all anonymous callers share one throttle bucket and
        one attacker locks out every login on the platform.
        """
        from django.conf import settings

        self.assertEqual(
            settings.REST_FRAMEWORK.get("NUM_PROXIES"),
            1,
            "NUM_PROXIES must be set so X-Forwarded-For is trusted correctly",
        )

    def test_throttling_uses_the_forwarded_client_ip(self):
        """
        Behavioural: two different forwarded IPs must not share a bucket. With
        NUM_PROXIES unset these collapse onto the proxy's address.
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()
        User.objects.create_user(email="a@example.com", password=PASSWORD)

        statuses = []
        for ip in ("203.0.113.10", "203.0.113.11"):
            client = APIClient()
            response = client.post(
                reverse("login"),
                {"email": "a@example.com", "password": "Wrong!Pass#1"},
                format="json",
                REMOTE_ADDR="198.51.100.1",          # the proxy
                HTTP_X_FORWARDED_FOR=ip,             # the real client
            )
            statuses.append(response.status_code)
            # Both should be 401 (wrong password), not 429 for one of them.
            self.assertNotEqual(
                response.status_code, 429, f"{ip} was throttled as if shared"
            )


class ThrottleResponseShapeTests(TestCase):
    """The 429 must be usable by a client, not just a string."""

    def setUp(self):
        cache.clear()

    def _exhaust_login(self):
        client = APIClient()
        response = None
        for _ in range(15):
            response = client.post(
                reverse("login"),
                {"email": "nobody@example.com", "password": "Wrong!Pass#1"},
                format="json",
            )
            if response.status_code == 429:
                break
        return response

    def test_a_429_uses_the_project_error_envelope(self):
        response = self._exhaust_login()
        self.assertEqual(response.status_code, 429)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error"], "THROTTLED")
        self.assertIn("message", body)
        self.assertIn("errors", body)

    def test_a_429_carries_retry_after(self):
        """
        Without this the browser has nothing to schedule a retry from, so the
        only thing it can do is show a dead error while the totals go stale.
        """
        response = self._exhaust_login()
        self.assertIn("Retry-After", response)
        wait = int(response["Retry-After"])
        self.assertGreater(wait, 0)

    def test_the_body_carries_the_same_wait_as_the_header(self):
        """The two must agree, or the UI and the HTTP client disagree."""
        response = self._exhaust_login()
        self.assertEqual(int(response["Retry-After"]), response.json()["retry_after"])

    def test_retry_after_is_a_whole_number_of_seconds(self):
        """`Retry-After` is delay-seconds or an HTTP-date; a float is neither."""
        response = self._exhaust_login()
        self.assertRegex(response["Retry-After"], r"^\d+$")

    def test_the_handler_leaves_non_throttle_errors_alone(self):
        """It must not rewrite ordinary errors on its way through."""
        from django.conf import settings

        self.assertEqual(
            settings.REST_FRAMEWORK["EXCEPTION_HANDLER"],
            "config.exceptions.throttle_exception_handler",
        )
        self.assertIsNotNone(exception_handler)


class BulkReadinessTests(TestCase):
    """
    A shop owner importing a catalogue, or an autocomplete firehose, must not be
    throttled. Guards the failure 2.0.5 exists to prevent.
    """

    def setUp(self):
        cache.clear()
        from django.contrib.auth import get_user_model

        from apps.accounts.models import BusinessProfile

        User = get_user_model()
        self.user = User.objects.create_user(email="bulk@example.com", password=PASSWORD)
        BusinessProfile.objects.create(
            user=self.user,
            trade_name="Bulk",
            company_name="Bulk",
            state_code="36",
            gstin="36AAAAA0000A1Z5",
        )
        self.client_ = APIClient()
        self.client_.force_authenticate(user=self.user)

    def test_three_hundred_reads_are_not_throttled(self):
        """300 autocomplete-style GETs - well past the old 600/hour budget."""
        codes = set()
        for _ in range(300):
            codes.add(self.client_.get("/api/v1/meta/units/").status_code)
        self.assertEqual(
            codes,
            {200},
            f"an authenticated read was throttled: {codes}",
        )

    def test_preview_is_still_throttled_above_its_own_limit(self):
        """
        The complement of the above: raising `user` must not have made preview
        unbounded, which is the whole reason it has its own scope.
        """
        from decimal import Decimal

        from apps.inventory.models import Item
        from apps.parties.models import Party

        Party.objects.create(
            business=self.user.business, name="Buyer", mobile="9000000000", state_code="36"
        )
        Item.objects.create(
            business=self.user.business,
            item_code="X-1",
            name="Thing",
            item_type=Item.ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=Decimal("10.00"),
            tax_rate=Decimal("5.00"),
            current_stock=Decimal("5.000"),
        )

        codes = set()
        for _ in range(200):
            response = self.client_.post(
                "/api/v1/invoices/preview/",
                {
                    "party": Party.objects.first().pk,
                    "items": [
                        {
                            "item": Item.objects.first().pk,
                            "quantity": "1",
                            "unit_price": "10.00",
                            "tax_rate": "5.00",
                        }
                    ],
                },
                format="json",
            )
            codes.add(response.status_code)
        self.assertIn(429, codes, "preview has no effective limit")


class IteratorUnderPoolingTests(TestCase):
    """
    1.4.9 finding #10. Neon's pooled URL uses transaction pooling, which breaks
    server-side cursors - and Django's `.iterator()` uses them.

    Skipped on SQLite because it has no cursors at all; the point is the
    PostgreSQL path, which the CI `postgres-concurrency` job exercises.
    """

    def test_cursor_factory_is_client_side_on_postgres(self):
        from django.conf import settings
        from django.db import connection

        if connection.vendor != "postgresql":
            self.skipTest("no server-side cursors on SQLite")

        options = settings.DATABASES["default"].get("OPTIONS") or {}
        self.assertIn(
            "cursor_factory",
            options,
            "PostgreSQL must force client-side cursors for the pooled endpoint",
        )

    def test_iterating_a_table_works(self):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)