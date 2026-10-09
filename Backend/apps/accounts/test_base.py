"""
Shared test base for the accounts app.

Why the cache is cleared
------------------------
`ThrottleConfigTests.test_repeated_login_attempts_eventually_get_throttled`
deliberately burns the `auth` budget, and the counter lives in the default
(Django default = per-process `LocMemCache`) cache, which is **not** reset
between tests. Without this, that one test silently 429s every later test that
logs in - a failure that looks like a broken register/login endpoint rather than
leaked state.

Tests here assert on real throttle *behaviour*, so the production rates must stay
in place; clearing the cache around each test is the correct fix, not raising the
limits.
"""

from django.core.cache import cache
from rest_framework.test import APITestCase


class AccountsTestCase(APITestCase):
    """APITestCase with a clean throttle counter per test."""

    def setUp(self):
        cache.clear()
        super().setUp()

    def tearDown(self):
        cache.clear()
        super().tearDown()