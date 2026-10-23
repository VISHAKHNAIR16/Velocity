"""
DRF error types for expenses.
"""

from rest_framework import status
from rest_framework.exceptions import APIException

from apps.expenses.errors import ExpenseError


class ExpenseErrorAPI(APIException):
    """An expense gate refused the operation."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, code: str, detail: str, *, status_code: int | None = None, field: str = ""):
        if status_code is not None:
            self.status_code = status_code
        self.field = field
        self.default_code = code
        super().__init__(detail=detail, code=code)
        self.data = {"details": []}


def translate(exc: ExpenseError) -> ExpenseErrorAPI:
    """Map a service-layer `ExpenseError` onto its API representation."""
    return ExpenseErrorAPI(exc.code, exc.message, status_code=exc.status, field=exc.field)
