"""
Purchase errors, in one place with no dependencies.

Framework-agnostic: the service layer raises these; `api_errors.translate()`
turns them into DRF responses.
"""


class PurchaseError(Exception):
    """
    A purchase gate refused the operation. `code` is machine-readable.

    Deliberately framework-agnostic: the service layer knows nothing about DRF.
    `api_errors.translate()` is what turns one of these into a response.
    """

    def __init__(self, code: str, message: str, status: int = 400, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.field = field

    def __str__(self) -> str:
        return self.message


class PurchaseConflict(PurchaseError):
    """The purchase cannot be created in its current state."""

    def __init__(self, message: str, code: str = "INVALID_STATE", status: int = 409):
        super().__init__(code, message, status=status)
