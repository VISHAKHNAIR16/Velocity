"""
Period rules: when may an invoice be created, issued or cancelled?

Decision 17 (1.4) made backdating a **UI warning**. That is advice, and advice
does not stop a back-dated invoice being slipped into a GSTR-1 period that has
already been filed - where it is silently never reported. Decision 23 replaces
the warning with a rule the server enforces.

Two distinct failures, kept distinct because the user's action differs:

* `PERIOD_LOCKED` - the date is in a period already reported to the tax
  department. Nothing may be created, issued or cancelled. The fix is a credit
  note dated *after* the lock date, which is the sanctioned instrument.
* `FUTURE_DATE`  - the invoice is dated ahead of today. Refused at **issue**
  only: a draft may be prepared in advance (next month's rent, say), but a
  numbered document dated in the future is not a real sale.

Why `FUTURE_DATE` is not applied at save time: preparing tomorrow's invoice is
normal bookkeeping. Issuing one dated next month is not.

All three call sites share `assert_period_open` so the rule cannot drift between
draft save, issue and cancel - the same "one helper, used everywhere" discipline
the HSN gate follows.
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone

from apps.invoices.errors import IssueError


def today_in_business_tz() -> date:
    """
    Today's date in the project's own timezone.

    `TIME_ZONE = Asia/Kolkata` with `USE_TZ = True`. `timezone.localdate()`
    converts to it correctly, which matters: an invoice dated "7 Oct" must not
    slip to "6 Oct" because UTC already rolled over. Using `date.today()` would
    read the *server's* local date, which on an UTC host is a day behind IST
    between 18:00 and 00:00 - i.e. it would reject invoices the user legitimately
    dated today, for a few hours every evening.
    """
    return timezone.localdate()


def assert_period_open(business, invoice_date: date, *, allow_future: bool = False) -> None:
    """
    Refuse a date that falls in a locked period (and, optionally, the future).

    Raises `IssueError`, which the view layer already translates into the
    project's standard `{"success": false, "error": CODE, ...}` envelope.

    `books_locked_until` is inclusive: an invoice dated exactly on the lock date
    belongs to the filed period, so `==` is refused too. Getting this boundary
    wrong in the permissive direction is exactly the bug this rule exists to
    prevent.
    """
    locked_until = getattr(business, "books_locked_until", None)
    if locked_until is not None and invoice_date <= locked_until:
        raise IssueError(
            "PERIOD_LOCKED",
            f"{invoice_date:%d %b %Y} is on or before your books lock date "
            f"({locked_until:%d %b %Y}), so it belongs to a period already filed "
            "to the GST department. Raise a credit note dated after that date "
            "instead.",
            field="invoice_date",
        )

    if not allow_future:
        today = today_in_business_tz()
        if invoice_date > today:
            raise IssueError(
                "FUTURE_DATE",
                f"{invoice_date:%d %b %Y} is in the future (today is "
                f"{today:%d %b %Y}). A numbered invoice cannot be dated ahead.",
                field="invoice_date",
            )


def earliest_allowed_date(business) -> date | None:
    """
    The earliest date the billing form should offer. `None` when nothing is
    locked, so the UI leaves the picker alone.

    Returns the day *after* the lock date - `+ 1 day` matters, because the lock
    date itself is refused.
    """
    from datetime import timedelta

    locked_until = getattr(business, "books_locked_until", None)
    if locked_until is None:
        return None
    return locked_until + timedelta(days=1)