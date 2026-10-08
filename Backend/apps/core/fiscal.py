"""
Indian financial-year helpers.

The FY runs **1 April - 31 March**, not the calendar year, and it decides the
invoice-number series (`INV/26-27/00001`), which resets on 1 April. Getting the
boundary wrong puts a 1 April invoice in the previous year's series.

Pure stdlib, no Django and no database, so it can be tested exhaustively.
"""

from datetime import date, datetime

#: First month of the Indian financial year.
FY_START_MONTH = 4
FY_START_DAY = 1
FY_END_MONTH = 3
FY_END_DAY = 31


def _as_date(value) -> date:
    """Accept a date or a datetime and return a plain date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"Expected a date or datetime, got {type(value).__name__}.")


def financial_year_start(value) -> date:
    """The 1 April that begins the FY containing `value`."""
    day = _as_date(value)
    if day.month >= FY_START_MONTH:
        return date(day.year, FY_START_MONTH, FY_START_DAY)
    return date(day.year - 1, FY_START_MONTH, FY_START_DAY)


def financial_year(value) -> str:
    """
    FY label, e.g. ``"2026-27"``.

        2026-04-01 -> "2026-27"
        2027-03-31 -> "2026-27"
        2027-04-01 -> "2027-28"
    """
    start = financial_year_start(value)
    return f"{start.year}-{str(start.year + 1)[2:]}"


def financial_year_short(value) -> str:
    """Short FY label, e.g. ``"26-27"`` - used in the invoice number."""
    return f"{financial_year(value)[2:]}"


def fy_bounds(label: str) -> tuple[date, date]:
    """
    Parse an FY label back into its inclusive date range.

    Accepts both ``"2026-27"`` and ``"26-27"``.

        fy_bounds("2026-27") -> (date(2026, 4, 1), date(2027, 3, 31))
    """
    text = (label or "").strip()
    if not text:
        raise ValueError("Financial year label is required, e.g. '2026-27'.")

    if "-" in text:
        first, second = text.split("-", 1)
    else:
        raise ValueError(f"Malformed financial year {label!r}; expected '2026-27'.")

    first = first.strip()
    second = second.strip()
    if len(first) == 4:
        start_year = int(first)
    elif len(first) == 2:
        # "26-27" -> 2026. Two-digit years are read as 20xx.
        start_year = 2000 + int(first)
    else:
        raise ValueError(f"Malformed financial year {label!r}; expected '2026-27'.")

    if not second.isdigit() or len(second) not in (2, 4):
        raise ValueError(f"Malformed financial year {label!r}; expected '2026-27'.")
    expected_tail = str(start_year + 1)[-len(second):]
    if int(second) != int(expected_tail):
        raise ValueError(
            f"Financial year {label!r} is not consecutive: "
            f"{start_year} must be followed by {start_year + 1}."
        )

    return date(start_year, FY_START_MONTH, FY_START_DAY), date(
        start_year + 1, FY_END_MONTH, FY_END_DAY
    )


def is_in_same_financial_year(first, second) -> bool:
    """True when two dates fall in the same Indian FY."""
    return financial_year(first) == financial_year(second)