"""
The resilient cache, and the diagnostics endpoint.

Both exist because of a real outage: a total login failure presenting as a bare
500 on every throttled endpoint, with no message in the response and no shell
on the host to investigate it. These tests assert the failure mode cannot come
back, and that the diagnostic cannot leak a credential.
"""

from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase

from config.cache import ResilientDatabaseCache

DIAGNOSTICS_URL = "/api/v1/health/diagnostics/"


class CacheFallbackTests(TestCase):
    """
    The whole point: an unusable cache must not become an outage.

    DRF throttles read the cache before the view body runs, so a missing
    `django_cache` table once made *every* caller of *every* throttled endpoint
    get a 500 - including people typing the right password.

    The failure is simulated on the **parent** `DatabaseCache`, not on the
    wrapper. Patching `ResilientDatabaseCache.get` would replace the very
    `try`/`except` under test and prove nothing; breaking `super().get()`
    leaves the fallback logic intact and is what actually happens in production.
    """

    def _break_the_database(self):
        """Make every database cache operation raise, as a missing table would."""
        from django.core.cache.backends.db import DatabaseCache

        patches = [
            mock.patch.object(DatabaseCache, name, side_effect=RuntimeError("no such table"))
            for name in ("get", "set", "add", "touch", "delete", "incr", "decr")
        ]
        return patches

    def test_a_broken_cache_degrades_instead_of_raising(self):
        broken = ResilientDatabaseCache("django_cache", {})
        for patch in self._break_the_database():
            patch.start()
        try:
            # Must not raise. The fallback answers instead.
            self.assertIsNone(broken.get("missing-key"))
        finally:
            for patch in self._break_the_database():
                patch.stop()

    def test_a_missing_cache_table_does_not_break_login(self):
        """
        End to end, and the assertion that matters.

        401 means the throttle ran and the credentials were simply wrong.
        500 would mean the outage is back.
        """
        from django.core.cache.backends.db import DatabaseCache

        patches = [
            mock.patch.object(DatabaseCache, name, side_effect=RuntimeError("no such table"))
            for name in ("get", "set", "add")
        ]
        for patch in patches:
            patch.start()
        try:
            response = Client().post(
                "/api/v1/auth/login/",
                {"email": "nobody@example.com", "password": "wrong"},
                content_type="application/json",
            )
        finally:
            for patch in patches:
                patch.stop()

        self.assertNotEqual(response.status_code, 500, "a broken cache took the login down")
        self.assertIn(response.status_code, (400, 401))

    def test_it_falls_back_to_memory_so_throttling_still_works(self):
        broken = ResilientDatabaseCache("django_cache", {})
        for patch in self._break_the_database():
            patch.start()
        try:
            # Set and get must agree with each other even while the database is
            # unusable - that is what keeps the rate limiter meaningful.
            broken.set("k", "v", 30)
            self.assertEqual(broken.get("k"), "v")
        finally:
            for patch in self._break_the_database():
                patch.stop()

    def test_the_real_cache_is_usable_in_this_environment(self):
        """The happy path, so the fallback is not masking a broken setup here."""
        cache.set("__t__", "1", 10)
        self.assertEqual(cache.get("__t__"), "1")

    def test_fallback_state_is_dropped_when_the_database_recovers(self):
        """
        The bug that broke 59 unrelated tests.

        Counters written to the fallback used to stay there forever, because
        nothing ever cleared them. Two consequences:

          * In the suite, one test that degraded the cache left throttle
            counters behind and every later request came back 429 - tests that
            had nothing to do with caching failed.
          * In production, once the database came back, the first request would
            still be throttled by numbers that no longer existed anywhere.

        Both halves of the database are mocked. The point is the state machine,
        not the SQL, and driving it through a real cache table makes the test
        depend on which backend the suite is configured with.
        """
        from django.core.cache.backends.db import DatabaseCache

        broken = ResilientDatabaseCache("django_cache", {})
        broken._fallback.set("stale", "1", 300)

        # Database broken: the write degrades into the fallback.
        with mock.patch.object(
            DatabaseCache, "set", side_effect=RuntimeError("no such table")
        ):
            broken.set("k", "v", 30)
        self.assertTrue(broken._degraded, "the cache should be marked degraded")

        # Database healthy: the next successful write clears the fallback.
        with mock.patch.object(DatabaseCache, "set", return_value=True):
            broken.set("k2", "v2", 30)
        self.assertFalse(broken._degraded)
        self.assertIsNone(broken._fallback.get("stale", None))

    def test_it_does_not_clear_the_fallback_on_every_successful_call(self):
        """
        Otherwise an intermittently failing database would erase the throttle
        counters it is relying on, and the rate limit would never take effect.
        """
        from django.core.cache.backends.db import DatabaseCache

        broken = ResilientDatabaseCache("django_cache", {})
        with mock.patch.object(
            DatabaseCache, "set", side_effect=RuntimeError("no such table")
        ):
            broken.set("a", "1", 300)

        with mock.patch.object(DatabaseCache, "set", return_value=True):
            broken.set("b", "2", 30)  # recovers, clears exactly once

        with mock.patch.object(
            DatabaseCache, "set", side_effect=RuntimeError("no such table")
        ):
            broken.set("c", "3", 300)
            broken.set("d", "4", 300)  # degrades again

        self.assertEqual(
            broken._fallback.get("c"), "3", "counters must survive a second degrade"
        )


class DiagnosticsTests(TestCase):
    """
    The endpoint exists because the host has no shell. It reports operational
    facts, so it must not be able to leak one.
    """

    def test_it_answers_without_authentication(self):
        response = Client().get(DIAGNOSTICS_URL)
        self.assertEqual(response.status_code, 200)

    def test_it_reports_the_operational_booleans(self):
        body = Client().get(DIAGNOSTICS_URL).json()
        self.assertIn("cache", body)
        self.assertIn("usable", body["cache"])
        self.assertIn("table_present", body["cache"])
        self.assertIn("pending_migrations", body)
        self.assertIn("pdf_engine", body)

    def test_it_withholds_the_exception_without_a_token(self):
        """
        A bare exception message can contain a connection string, so it is
        gated. Unauthenticated callers get booleans only.
        """
        body = Client().get(DIAGNOSTICS_URL).json()
        self.assertIsInstance(body["login_probe"], str)
        self.assertIn("token", body["login_probe"])

    def test_a_connection_string_is_redacted(self):
        from config.views import _redact

        leaked = "could not connect to postgresql://user:hunter2@db.internal:5432/velocity"
        cleaned = _redact(leaked)
        self.assertNotIn("hunter2", cleaned)
        self.assertIn("[redacted]", cleaned)

    def test_a_password_assignment_is_redacted(self):
        from config.views import _redact

        self.assertNotIn("s3cr3t", _redact("SECRET_KEY=s3cr3t and more"))
        self.assertNotIn("p@ss", _redact("password=p@ss"))

    def test_redaction_leaves_harmless_text_alone(self):
        from config.views import _redact

        self.assertEqual(
            _redact("relation django_cache does not exist"),
            "relation django_cache does not exist",
        )


class CacheTableTests(TestCase):
    def test_the_cache_table_exists_where_the_migrations_ran(self):
        """
        Guards the assumption the outage broke.

        Asked through introspection, so this works on SQLite in the suite and
        Postgres in production - the raw `pg_tables` query it replaced raised
        `no such table: pg_tables` under SQLite.
        """
        from config.views import settings_cache_table

        tables = connection.introspection.table_names()
        self.assertIn(settings_cache_table(), tables)