"""
Quotation errors, in one place with no dependencies.

Framework-agnostic: the service layer raises these; `api_errors.translate()`
turns them into DRF responses.
"""


class QuotationError(Exception):
    """A quotation gate refused the operation. `code` is machine-readable."""

    def __init__(self, code: str, message: str, status: int = 400, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.field = field

    def __str__(self) -> str:
        return self.message


class QuotationConflict(QuotationError):
    """The quotation cannot be converted in its current state."""

    def __init__(
        self, message: str, code: str = "INVALID_STATE", status: int = 409, field: str = ""
    ):
        super().__init__(code, message, status=status, field=field)
