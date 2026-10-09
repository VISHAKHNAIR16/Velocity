"""
DRF error types for the invoice lifecycle (step D).

The service layer raises framework-agnostic `IssueError`s. These translate them
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

from apps.invoices.services.issue import InvoiceConflict, IssueError


class InvoiceLifecycleError(APIException):
    """An issue/cancel gate refused the transition."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, code: str, detail: str, *, status_code: int | None = None, field: str = ""):
        if status_code is not None:
            self.status_code = status_code
        self.field = field
        # DRF's APIException.__init__ puts `code` on the detail but leaves
        # `default_code` alone, and api_exception_handler reads `default_code`
        # to build the response's `error` field. Set both, or every lifecycle
        # error surfaces as the useless "ERROR".
        self.default_code = code
        super().__init__(detail=detail, code=code)


class InvoiceConflictError(InvoiceLifecycleError):
    """The invoice is not in a state that allows this transition (409)."""

    status_code = status.HTTP_409_CONFLICT


def translate(exc: IssueError) -> InvoiceLifecycleError:
    """Map a service-layer `IssueError` onto its API representation."""
    cls = InvoiceConflictError if isinstance(exc, InvoiceConflict) else InvoiceLifecycleError
    return cls(exc.code, exc.message, status_code=exc.status, field=exc.field)


def translate_calculation(exc) -> InvoiceLifecycleError:
    """
    Map a `GSTCalculationError` onto the same envelope the issue/cancel gates use.

    The calculator refuses with a specific, machine-readable `code`
    (`PARTY_STATE_MISSING`, `INVALID_TAX_RATE`, ...). Wrapping it in a plain DRF
    `ValidationError` threw that code away and left the client with a generic
    `VALIDATION_ERROR`, so the *preview* endpoint reported one error string while
    *issue* reported another for the identical underlying condition. The code is
    the contract the frontend branches on, so it has to survive the trip.

    Status stays 400: a refusal to calculate is a bad request, and
    `InvoiceLifecycleError` defaults to 400. It is used here purely for its
    `default_code` handling.
    """
    return InvoiceLifecycleError(
        getattr(exc, "code", "GST_CALCULATION_ERROR"),
        getattr(exc, "message", str(exc)),
        field=getattr(exc, "field", ""),
    )
