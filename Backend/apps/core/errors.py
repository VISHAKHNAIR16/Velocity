"""
Project-wide errors that are not tied to one app.

An invoice-specific failure belongs in `apps/invoices/errors.py`. A stock
failure can be raised from the inventory ledger, the item serializer or the
invoice issue path, so it needs a home none of them owns.
"""


class StockError(Exception):
    """
    A stock operation was refused. `code` is machine-readable, matching the
    `IssueError` convention so the API layer can translate both the same way.
    """

    def __init__(self, code: str, message: str, *, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def __str__(self) -> str:
        return self.message
