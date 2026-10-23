"""
DRF error types for the purchase lifecycle.

The service layer raises framework-agnostic `PurchaseError`s. These translate
them into responses that keep the project's standard envelope
(`{"success": false, "error": CODE, "message": ..., "errors": {...}}`).
"""

from rest_framework import status
from rest_framework.exceptions import APIException

from apps.purchases.errors import PurchaseConflict, PurchaseError


class PurchaseLifecycleError(APIException):
    """A purchase gate refused the operation."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, code: str, detail: str, *, status_code: int | None = None, field: str = ""):
        if status_code is not None:
            self.status_code = status_code
        self.field = field
        self.default_code = code
        super().__init__(detail=detail, code=code)
        self.data = {"details": []}


class PurchaseConflictError(PurchaseLifecycleError):
    """The purchase cannot be created in its current state (409)."""

    status_code = status.HTTP_409_CONFLICT


def translate(exc: PurchaseError) -> PurchaseLifecycleError:
    """Map a service-layer `PurchaseError` onto its API representation."""
    cls = PurchaseConflictError if isinstance(exc, PurchaseConflict) else PurchaseLifecycleError
    return cls(exc.code, exc.message, status_code=exc.status, field=exc.field)
