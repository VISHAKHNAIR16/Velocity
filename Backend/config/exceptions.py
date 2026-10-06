"""Turns every API error into one consistent JSON shape."""
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
    return response