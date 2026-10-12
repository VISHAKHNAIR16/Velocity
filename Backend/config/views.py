"""Project-wide utility views."""

import logging
import re

from django.db import connection  # pyright: ignore[reportMissingModuleSource]
from django.http import JsonResponse  # pyright: ignore[reportMissingModuleSource]

logger = logging.getLogger(__name__)


def health_check(request):
    """Lightweight liveness probe. Touches no database and needs no auth."""
    return JsonResponse({"success": True, "status": "ok", "service": "velocity-api"})


def db_health_check(request):
    """Verifies the database connection by running a trivial query."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"success": True, "database": "connected"})
    except Exception:
        # Full error goes to the server logs; the public response stays generic
        # so no connection details leak.
        logger.exception("Database health check failed")
        return JsonResponse({"success": False, "database": "unavailable"}, status=503)


# --- Diagnostics -------------------------------------------------------------
#
# Exists because a total login outage produced a bare 500 with no message and no
# useful log line, and the host has no shell access to run `manage.py` anything.
# Opening a URL in a browser was the only channel available, so this is that
# channel.
#
# Two tiers, and the split is deliberate:
#
#   * Unauthenticated, it answers only **booleans and names** - does the cache
#     table exist, are migrations pending, is the PDF engine importable, does
#     the new column exist. Nothing here can leak a secret or a record.
#   * With `?token=` matching the `DIAGNOSTICS_TOKEN` environment variable, it
#     also returns the sanitised exception from a live request. That text can
#     contain a connection string, so it is redacted and gated.

#: Anything resembling a credential is stripped before it can be returned.
#: The whole pattern is compiled case-insensitively - an inline `(?i)` part-way
#: through is a `re.error` on Python 3.11+, which would have taken the
#: diagnostics endpoint down at import time, i.e. precisely when it was needed.
#:
#: The key names are matched as a *suffix* of a larger identifier, so
#: `SECRET_KEY`, `DB_PASSWORD` and `x-api-key` are all caught. Matching the bare
#: word instead missed `SECRET_KEY=` and leaked the very value most worth
#: hiding - caught by a test, not by reading.
_SECRET = re.compile(
    r"(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|cloudinary)://[^\s'\"]+"
    r"|\b[A-Za-z0-9_]*(?:password|secret|token|credential|api[_-]?key)"
    r"[A-Za-z0-9_]*\s*[=:]\s*\S+",
    re.IGNORECASE,
)


def _redact(text: str, limit: int = 300) -> str:
    """Strip anything that looks like a credential, then truncate."""
    return _SECRET.sub("[redacted]", str(text))[:limit]


def _pending_migrations() -> list[str]:
    from django.db.migrations.executor import MigrationExecutor

    try:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        return [
            f"{app}.{name}"
            for app, name in executor.migration_plan(targets)
            if app
        ][:20]
    except Exception as exc:  # pragma: no cover - diagnostics only
        return [f"<unreadable: {type(exc).__name__}>"]


def _cache_table_exists() -> bool:
    """
    Is the cache table there?

    Asked through Django's own introspection rather than `pg_tables`. The
    project runs Postgres in production and **SQLite in the test suite**, and
    raw Postgres SQL here made every one of these tests error with
    `no such table: pg_tables` - the diagnostic failing exactly when the test
    suite pointed at a different database than production does.
    """
    try:
        return settings_cache_table() in connection.introspection.table_names()
    except Exception:
        return False


def settings_cache_table() -> str:
    from django.conf import settings

    return (
        settings.DATABASES["default"]
        .get("OPTIONS", {})
        .get("cache_table", "django_cache")
    )


def diagnostics_check(request):
    """GET /api/v1/health/diagnostics/ - why is nothing working?"""
    from django.conf import settings

    report = {"success": True}

    # 1. Cache. The prime suspect, because throttling reads it before any view
    #    body runs, so a missing table 500s every throttled endpoint.
    cache_report = {"backend": settings.CACHES["default"]["BACKEND"]}
    try:
        from django.core.cache import cache

        cache.set("__diag__", "1", 10)
        cache_report["usable"] = cache.get("__diag__") == "1"
    except Exception as exc:
        cache_report["usable"] = False
        cache_report["error"] = _redact(f"{type(exc).__name__}: {exc}")
    cache_report["table_present"] = _cache_table_exists()
    report["cache"] = cache_report

    # 2. Schema. A missing column from an unapplied migration is the other
    #    classic cause of a 500 on the first request that touches a model.
    report["pending_migrations"] = _pending_migrations()
    try:
        from apps.accounts.models import BusinessProfile

        report["schema"] = {
            "businessprofile_has_signature": "signature"
            in {f.name for f in BusinessProfile._meta.concrete_fields}
        }
    except Exception as exc:
        report["schema"] = {"error": _redact(f"{type(exc).__name__}: {exc}")}

    # 3. PDF engine (2.3). Independent of login, but the next thing anyone
    #    asks about, and free of side effects.
    try:
        from apps.documents.services.renderer import engine_status

        status = engine_status()
        report["pdf_engine"] = {
            "available": status.available,
            "version": status.version,
            "detail": _redact(status.detail),
        }
    except Exception as exc:
        report["pdf_engine"] = {"available": False, "detail": _redact(type(exc).__name__)}

    # 4. The live exception, only when a token matches.
    expected = getattr(settings, "DIAGNOSTICS_TOKEN", "")
    supplied = request.GET.get("token", "")
    if expected and supplied and supplied == expected:
        from django.test import Client

        probe = Client()
        try:
            response = probe.get("/api/v1/auth/login/")
            report["login_probe"] = {"status": response.status_code}
        except Exception as exc:
            report["login_probe"] = {
                "status": 500,
                "exception": _redact(f"{type(exc).__name__}: {exc}"),
            }
    else:
        report["login_probe"] = "add ?token=<DIAGNOSTICS_TOKEN> for the exception detail"

    return JsonResponse(report)
