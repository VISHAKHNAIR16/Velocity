"""Project-wide utility views."""

import logging

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
