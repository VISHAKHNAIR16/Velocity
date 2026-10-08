"""
Invoice number allocation (step D).

A number is a legal reference, so the rules here are strict:

* **One series per business per financial year**, reset on 1 April (IST).
* **Gapless and never reused.** A cancelled invoice keeps its number, because
  the number has already appeared on a document the customer holds and in that
  period's GSTR-1.
* **The prefix is snapshotted onto the counter row** when the year's counter is
  created. If a business edits its prefix in October, the rest of the FY keeps
  `INV/26-27/00007` rather than switching to `ABC/26-27/00008`, which would look
  like tampering to anyone auditing the series.
* Numbers are **≤ 16 characters** to match the model column, e.g.
  `INV/26-27/00001` (15 chars).
"""

from django.db import transaction
from django.db.models import F

from apps.core.fiscal import financial_year, financial_year_short

from ..models import InvoiceCounter

#: Series digits. Five digits = 99,999 invoices per FY per business, which is
#: far beyond a single small firm's annual volume.
NUMBER_WIDTH = 5

#: Hard ceiling on the series, so the fixed width can never silently grow past
#: the column. 99,999 invoices is ~274/day for a whole financial year.
MAX_SERIES_NUMBER = 10**NUMBER_WIDTH - 1

#: Guards the model's 16-char column rather than discovering it via a 500.
MAX_NUMBER_LENGTH = 16


class NumberingError(Exception):
    """
    Raised when a number cannot be allocated. `code` is machine-readable so the
    API can return it as an `error` field rather than prose.
    """

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _format_number(prefix: str, on_date, number: int) -> str:
    """
    `INV` + `26-27` + `00001` -> `INV/26-27/00001` (15 characters).

    The FY is rendered SHORT here even though the counter row stores the full
    `2026-27`: the long form would push `INV/2026-27/00001` to 17 characters and
    overflow the model's 16-character column.
    """
    if number > MAX_SERIES_NUMBER:
        # `%0Nd` is a MINIMUM width, so a large series silently grows the number
        # instead of being truncated. That is the right default (truncation would
        # mint duplicate numbers on a legal document), but it must fail loudly
        # rather than overflow the column as a database error 100,000 invoices
        # into the year. Widen the column before a business ever gets here.
        raise NumberingError(
            "SERIES_EXHAUSTED",
            f"This financial year's invoice series has passed "
            f"{MAX_SERIES_NUMBER:,}. Invoice numbers are limited to "
            f"{MAX_NUMBER_LENGTH} characters; contact support to widen the "
            "series before issuing more invoices this year.",
        )
    value = f"{prefix}/{financial_year_short(on_date)}/{number:0{NUMBER_WIDTH}d}"
    if len(value) > MAX_NUMBER_LENGTH:
        raise NumberingError(
            "PREFIX_TOO_LONG",
            f"'{prefix}' is too long: a number may be at most "
            f"{MAX_NUMBER_LENGTH} characters (got {len(value)}).",
            status=400,
        )
    return value


def counter_for(business, on_date) -> InvoiceCounter:
    """
    The counter row for this business and financial year, created if needed.

    Must be called inside a transaction. The unique constraint on
    (business, financial_year) is what makes two concurrent "first invoice of
    the year" attempts resolve to one row instead of two.
    """
    fy_label = financial_year(on_date)
    counter, _created = InvoiceCounter.objects.get_or_create(
        business=business,
        financial_year=fy_label,
        # Snapshotted once, here. Later prefix edits do not rewrite history.
        defaults={"number_prefix": business.invoice_number_prefix, "last_number": 0},
    )
    return counter


def allocate_invoice_number(business, on_date) -> str:
    """
    Return the next number for `business` in the FY containing `on_date`, and
    advance the counter.

    The caller must already hold the **invoice row lock** (see
    `services/issue.py` for why the lock order is invoice -> counter) and must be
    inside a transaction. This function then locks the counter row, so two
    concurrent issues cannot both read the same `last_number`.
    """
    assert transaction.get_connection().in_atomic_block, (
        "allocate_invoice_number() must run inside a transaction; a rolled-back "
        "issue would otherwise leave a gap in the series."
    )

    counter = counter_for(business, on_date)

    # A bare locked get() would fail on a row another transaction is inserting,
    # so the get_or_create above settles that race first.
    counter = (
        InvoiceCounter.objects.select_for_update().filter(pk=counter.pk).first()
    )
    if counter is None:  # pragma: no cover - the row was just created
        raise NumberingError("NUMBER_RACE", "The invoice counter vanished; retry.")

    # Atomic increment-and-read. Never `last_number + 1` in Python, which would
    # read-modify-write and could interleave with another transaction.
    InvoiceCounter.objects.filter(pk=counter.pk).update(last_number=F("last_number") + 1)
    counter.refresh_from_db(fields=["last_number"])

    return _format_number(counter.number_prefix, on_date, counter.last_number)


def peek_next_number(business, on_date) -> str:
    """
    The number the next issue *would* get, without consuming it.

    For the UI ("this will be INV/26-27/00008"). Always the counter's own
    prefix, never the business's current prefix, so the preview cannot disagree
    with what is actually issued.
    """
    fy_label = financial_year(on_date)
    counter = InvoiceCounter.objects.filter(
        business=business, financial_year=fy_label
    ).first()
    prefix = counter.number_prefix if counter else business.invoice_number_prefix
    last = counter.last_number if counter else 0
    return _format_number(prefix, on_date, last + 1)
