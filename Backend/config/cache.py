"""
A cache backend that cannot take the site down with it.

**Why this exists.** Throttling on this project runs through the cache: DRF's
`AnonRateThrottle`, `UserRateThrottle` and `ScopedRateThrottle` all call
`cache.get` / `cache.set` before the view body runs. The cache is a *database*
table, `django_cache`.

So if that table is missing - a fresh database, a re-pointed `DATABASE_URL`, a
restore that skipped it - every throttled endpoint raises before it can do
anything else. In production `DEBUG` is off, `api_exception_handler` returns
`None` for an unexpected crash, and Django serves a bare 500 with no message
and no usable log line.

That is exactly what happened: `/api/v1/health/` answered 200 (a plain Django
view, no throttle) while `POST /api/v1/auth/login/` answered 500 for **every**
caller, including with a valid password. Nobody could log in at all, and
nothing in the response said why.

A rate limiter is not worth a total outage. Every operation here degrades to an
in-process `LocMemCache`, and the first failure logs a loud warning naming the
real cause. The trade is deliberate and recorded in `FUTURE_CHECKLIST.md`:
throttle counters become per-process, so they reset on restart and are not
shared across replicas. On the single-instance free tier that is a far smaller
cost than being locked out.

`createcachetable` is still the right permanent fix; this is the net underneath.
"""

import logging

from django.core.cache.backends.db import DatabaseCache
from django.core.cache.backends.locmem import LocMemCache

logger = logging.getLogger(__name__)

#: Logged once. A missing cache table then produces exactly one actionable line
#: instead of one traceback per request for as long as the outage lasts.
_warned = False


class ResilientDatabaseCache(DatabaseCache):
    """`DatabaseCache` that falls back to memory rather than raising."""

    def __init__(self, location, params):
        super().__init__(location, params)
        # Same name, so a key written to one lands in the other and a later
        # recovery does not look like a cold cache.
        self._fallback = LocMemCache(location, params)
        #: True between the first failed operation and the next successful one.
        #: Used to clear the fallback exactly once on recovery.
        self._degraded = False

    # -- internals --------------------------------------------------------

    def _degrade(self, exc: Exception):
        """Note the failure once, then hand back the fallback."""
        global _warned
        if not _warned:
            _warned = True
            logger.error(
                "CACHE UNAVAILABLE - falling back to an in-process cache. "
                "Rate limiting will reset on restart and is not shared across "
                "replicas. Run `python manage.py createcachetable` to fix this "
                "permanently. Cause: %s: %s",
                type(exc).__name__,
                exc,
            )
        self._degraded = True
        return self._fallback

    def _recovered(self):
        """
        The database is answering again.

        Drops whatever accumulated in the fallback. This matters more than it
        looks: throttle counters written to memory during an outage would
        otherwise stay authoritative forever, so the first recovered request
        would still be throttled by counts that no longer exist in the database
        - and, in the test suite, leaked across test cases and turned dozens of
        unrelated tests into 429s.

        Cleared once per degradation, not on every successful call, so an
        intermittently failing database cannot wipe the counters it depends on.
        """
        if self._degraded:
            self._degraded = False
            try:
                self._fallback.clear()
            except Exception:  # pragma: no cover - best effort
                pass

    # -- public API -------------------------------------------------------

    def add(self, key, value, timeout=None, version=None):
        try:
            result = super().add(key, value, timeout=timeout, version=version)
        except Exception as exc:
            return self._degrade(exc).add(key, value, timeout=timeout, version=version)
        self._recovered()
        return result

    def get(self, key, default=None, version=None):
        try:
            result = super().get(key, default=default, version=version)
        except Exception as exc:
            return self._degrade(exc).get(key, default=default, version=version)
        self._recovered()
        return result

    def set(self, key, value, timeout=None, version=None):
        try:
            result = super().set(key, value, timeout=timeout, version=version)
        except Exception as exc:
            return self._degrade(exc).set(key, value, timeout=timeout, version=version)
        self._recovered()
        return result

    def touch(self, key, timeout=None, version=None):
        try:
            result = super().touch(key, timeout=timeout, version=version)
        except Exception as exc:
            return self._degrade(exc).touch(key, timeout=timeout, version=version)
        self._recovered()
        return result

    def delete(self, key, version=None):
        try:
            result = super().delete(key, version=version)
        except Exception as exc:
            return self._degrade(exc).delete(key, version=version)
        self._recovered()
        return result

    def delete_many(self, keys, version=None):
        try:
            result = super().delete_many(keys, version=version)
        except Exception as exc:
            return self._degrade(exc).delete_many(keys, version=version)
        self._recovered()
        return result

    def clear(self):
        try:
            result = super().clear()
        except Exception as exc:
            return self._degrade(exc).clear()
        self._recovered()
        return result

    def has_key(self, key, version=None):
        try:
            result = super().has_key(key, version=version)
        except Exception as exc:
            return self._degrade(exc).has_key(key, version=version)
        self._recovered()
        return result

    def incr(self, key, delta=1, version=None):
        try:
            result = super().incr(key, delta=delta, version=version)
        except Exception as exc:
            return self._degrade(exc).incr(key, delta=delta, version=version)
        self._recovered()
        return result

    def decr(self, key, delta=1, version=None):
        try:
            result = super().decr(key, delta=delta, version=version)
        except Exception as exc:
            return self._degrade(exc).decr(key, delta=delta, version=version)
        self._recovered()
        return result

    def get_many(self, keys, version=None):
        try:
            result = super().get_many(keys, version=version)
        except Exception as exc:
            return self._degrade(exc).get_many(keys, version=version)
        self._recovered()
        return result

    def set_many(self, data, timeout=None, version=None):
        try:
            result = super().set_many(data, timeout=timeout, version=version)
        except Exception as exc:
            return self._degrade(exc).set_many(data, timeout=timeout, version=version)
        self._recovered()
        return result

    def delete_pattern(self, pattern, version=None):
        try:
            result = super().delete_pattern(pattern, version=version)
        except Exception as exc:
            return self._degrade(exc).delete_pattern(pattern, version=version)
        self._recovered()
        return result

    def has_key_pattern(self, pattern, version=None):
        try:
            result = super().has_key_pattern(pattern, version=version)
        except Exception as exc:
            return self._degrade(exc).has_key_pattern(pattern, version=version)
        self._recovered()
        return result