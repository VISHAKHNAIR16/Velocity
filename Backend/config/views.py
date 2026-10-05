"""Project-wide utility views."""
from django.http import JsonResponse


def health_check(request):
    """Lightweight liveness probe. Touches no database and needs no auth."""
    return JsonResponse({"success": True, "status": "ok", "service": "velocity-api"})