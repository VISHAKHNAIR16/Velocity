"""
Purchase errors and their translation to API responses.

Mirrors `apps/invoices/errors.py` so a purchase failure produces the same
envelope a customer already knows from sales: `{"error": CODE, "message": ...}`.
"""

from apps.core.errors import StockError


class PurchaseError(Exception):
    """Base for failures that carry a machine-readable code."""

    def __init__(self, code: str, message: str, status: int = 400, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.field = field


class PurchaseConflict(PurchaseError):
    """The bill is not in a state where this operation is legal."""

    def __init__(self, message: str, code: str = "INVALID_STATE"):
        super().__init__(code, message, status=409)


class DuplicateBillNumber(PurchaseError):
    """
    This supplier's bill number is already recorded for this financial year.

    The database enforces this too (the unique constraint), but the constraint
    arrives as an opaque `IntegrityError` naming a constraint nobody can read, so
    the friendly check exists to produce a message a shopkeeper can act on. The
    constraint remains the real guard against a race - see the model docstring.
    """

    def __init__(self, bill_number: str, financial_year: str):
        super().__init__(
            "DUPLICATE_BILL_NUMBER",
            f"Supplier bill {bill_number} is already recorded for {financial_year}. "
            "Every bill from the same supplier must have its own number.",
            status=409,
            field="bill_number",
        )


#: Errors that carry `details` rows the frontend highlights, mirroring the way
#: `INSUFFICIENT_STOCK` does for a sale.
DETAILED_ERRORS = (
    StockError,  # INSUFFICIENT_STOCK / ITEM_INACTIVE / CANCEL_WOULD_GO_NEGATIVE
)


def translate_purchase(exc: Exception, *, status: int = 400) -> tuple:
    """
    Turn an exception into `(http_status, body)`.

    `details` is preserved for the stock errors, because "only 3 in stock" is
    not actionable on a fifteen-line bill unless the frontend knows which line.
    That is the same reason the 2.1.4 handler stopped discarding it.
    """
    if isinstance(exc, PurchaseError):
        body = {
            "success": False,
            "error": exc.code,
            "message": exc.message,
            "errors": {exc.field: [exc.message]} if exc.field else {},
        }
        return exc.status, body

    if isinstance(exc, StockError):
        body = {
            "success": False,
            "error": exc.code,
            "message": exc.message,
            "errors": {},
        }
        details = getattr(exc, "details", None)
        if details:
            body["details"] = details
            body["errors"] = {exc.field: [exc.message]} if exc.field else {}
        return status, body

    # The period rules (2.0.4) live in the invoices app and raise `IssueError`.
    # Purchases obey the same books lock, so the same exception has to translate
    # here - otherwise a locked-period refusal surfaces as a 500 instead of the
    # 400 the user needs.
    from apps.invoices.errors import IssueError

    if isinstance(exc, IssueError):
        body = {
            "success": False,
            "error": exc.code,
            "message": exc.message,
            "errors": {exc.field: [exc.message]} if getattr(exc, "field", "") else {},
        }
        return getattr(exc, "status", status) or status, body

    return status, {
        "success": False,
        "error": "ERROR",
        "message": str(exc),
        "errors": {},
    }