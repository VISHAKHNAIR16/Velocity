"""
Invoice errors, in one place with no dependencies.

These were defined in `services/issue.py`, which made them impossible to import
from a sibling module: `period_rules.py` needs `IssueError` to refuse a locked
period, and `issue.py` needs `period_rules` to run that check — so `issue.py`
imported `period_rules` while `period_rules` imported `issue`, and the cycle
broke the app at import time.

An error type used by more than one service does not belong inside one of them.
Both modules now import from here; `issue.py` re-exports the names so existing
imports keep working.
"""


class IssueError(Exception):
    """
    A gate refused the operation. `code` is machine-readable.

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


class InvoiceConflict(IssueError):
    """The invoice is not in a state that allows the requested transition."""

    def __init__(self, message: str, code: str = "INVALID_STATE", status: int = 409):
        super().__init__(code, message, status=status)