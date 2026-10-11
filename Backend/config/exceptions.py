"""Turns every API error into one consistent JSON shape."""

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    """
    Output: {"success": false, "error": "CODE", "message": "...", "errors": {field: [msgs]}}
    `errors` is only filled for validation problems (one entry per bad field).
    """
    response = exception_handler(exc, context)
    if response is None:
        return None  # unexpected crash: Django returns a 500 and logs the details

    data = response.data
    if isinstance(exc, ValidationError):
        code = "VALIDATION_ERROR"
        message = "Please correct the errors below."
        errors = data if isinstance(data, dict) else {"non_field_errors": data}
    else:
        code = str(getattr(exc, "default_code", "error")).upper()
        message = str(data.get("detail", exc)) if isinstance(data, dict) else str(exc)
        errors = {}

    response.data = {"success": False, "error": code, "message": message, "errors": errors}

    # Preserve per-item `details` attached by a service error (2.1.4's
    # INSUFFICIENT_STOCK needs to say WHICH item and by how much). Without this
    # the envelope rewrite above silently dropped them and the client could only
    # show "insufficient stock" with no idea which line to fix.
    extra = getattr(exc, "data", None)
    if isinstance(extra, dict):
        response.data.update(extra)

    return response


def throttle_exception_handler(exc, context):
    """
    Make a 429 speak the project's error format, and tell the client when to retry.

    Decision 24. DRF's default 429 body is `{"detail": "Request was throttled."}`
    - no `error` code, so the frontend cannot branch on it - and it omits
    `Retry-After`, leaving the browser with nothing to schedule a retry from.

    Without this the billing screen's only option is to show a dead error, and a
    cashier mid-invoice sees stale or blank totals with no indication that
    waiting a few seconds would fix it. The frontend's retry-with-backoff reads
    exactly these two fields.
    """
    response = api_exception_handler(exc, context)
    if response is None or response.status_code != status.HTTP_429_TOO_MANY_REQUESTS:
        return response

    detail = response.data.get("message", "")
    wait = getattr(exc, "wait", None)
    if wait:
        # Integer seconds: `Retry-After` is defined as either a delay or an HTTP
        # date. A fractional value would be rejected by strict clients.
        response["Retry-After"] = str(int(wait))
        response.data["retry_after"] = int(wait)
        response.data["message"] = (
            f"Too many requests. Try again in {int(wait)} second"
            f"{'s' if int(wait) != 1 else ''}."
        )
    else:
        response.data["retry_after"] = None
    response.data["error"] = "THROTTLED"
    if "Expected available in" in detail:
        # Keep the server's own phrasing as the human-readable part rather than
        # discarding it; the frontend shows `message` verbatim.
        response.data["message"] = f"{response.data['message']} ({detail})"
    return response
