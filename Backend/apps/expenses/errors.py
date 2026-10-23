"""
Expense errors, in one place with no dependencies.

Framework-agnostic: the service layer raises these; `api_errors.translate()`
turns them into DRF responses.
"""


class ExpenseError(Exception):
    """An expense gate refused the operation. `code` is machine-readable."""

    def __init__(self, code: str, message: str, status: int = 400, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.field = field

    def __str__(self) -> str:
        return self.message
