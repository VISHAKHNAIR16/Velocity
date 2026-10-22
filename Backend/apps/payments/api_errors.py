"""
DRF error types for payments.

The service layer raises framework-agnostic `PaymentError`s. These translate them
into responses that keep the project's standard envelope
(`{"success": false, "error": CODE, "message": ..., "errors": {...}}`), with
`error` set to the machine-readable code so the frontend can branch on it
without parsing prose.

`config.exceptions.api_exception_handler` derives `error` from
`exc.default_code`, which is why the code is passed through here rather than
only carried on the exception.
"""

from rest_framework import status
from rest_framework.exceptions import APIException

from apps.payments.errors import PaymentConflict, PaymentError


class PaymentLifecycleError(APIException):
    """A payment gate refused the operation."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, code: str, detail: str, *, status_code: int | None = None, field: str = ""):
        if status_code is not None:
            self.status_code = status_code
        self.field = field
        self.default_code = code
        super().__init__(detail=detail, code=code)
        self.data = {"details": []}


class PaymentConflictError(PaymentLifecycleError):
    """The payment cannot be created in its current state (409)."""

    status_code = status.HTTP_409_CONFLICT


def translate(exc: PaymentError) -> PaymentLifecycleError:
    """Map a service-layer `PaymentError` onto its API representation."""
    cls = PaymentConflictError if isinstance(exc, PaymentConflict) else PaymentLifecycleError
    return cls(exc.code, exc.message, status_code=exc.status, field=exc.field)
