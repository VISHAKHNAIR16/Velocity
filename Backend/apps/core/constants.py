"""
Shared tax constants - the single source of truth for anything GST-related.

This module deliberately imports nothing from other apps, so any app can depend
on it without creating a circular import. `apps.accounts.constants` re-exports
`GST_STATE_CHOICES` for backwards compatibility.

Rule of thumb: if a tax concept is needed by two or more apps (items,
invoices, purchases, reports), it belongs here - never copy-paste the list.
"""

import re
from decimal import Decimal

# ---------------------------------------------------------------------------
# GST state / UT codes (moved from apps/accounts/constants.py)
# ---------------------------------------------------------------------------
GST_STATE_CHOICES = [
    ("01", "Jammu and Kashmir"),
    ("02", "Himachal Pradesh"),
    ("03", "Punjab"),
    ("04", "Chandigarh"),
    ("05", "Uttarakhand"),
    ("06", "Haryana"),
    ("07", "Delhi"),
    ("08", "Rajasthan"),
    ("09", "Uttar Pradesh"),
    ("10", "Bihar"),
    ("11", "Sikkim"),
    ("12", "Arunachal Pradesh"),
    ("13", "Nagaland"),
    ("14", "Manipur"),
    ("15", "Mizoram"),
    ("16", "Tripura"),
    ("17", "Meghalaya"),
    ("18", "Assam"),
    ("19", "West Bengal"),
    ("20", "Jharkhand"),
    ("21", "Odisha"),
    ("22", "Chhattisgarh"),
    ("23", "Madhya Pradesh"),
    ("24", "Gujarat"),
    ("26", "Dadra and Nagar Haveli and Daman and Diu"),
    ("27", "Maharashtra"),
    ("29", "Karnataka"),
    ("30", "Goa"),
    ("31", "Lakshadweep"),
    ("32", "Kerala"),
    ("33", "Tamil Nadu"),
    ("34", "Puducherry"),
    ("35", "Andaman and Nicobar Islands"),
    ("36", "Telangana"),
    ("37", "Andhra Pradesh"),
    ("38", "Ladakh"),
]

# ---------------------------------------------------------------------------
# GST tax rates
# ---------------------------------------------------------------------------
# Values are Decimal (never float / int) so they drop straight into a
# DecimalField and into the calculator without a conversion step.
#
# SLAB HISTORY: the 56th GST Council restructured the structure effective
# 22 Sept 2025 - mainly 5% and 18%, with a 40% de-merit rate for specified goods
# (tobacco continues at 28% + cess). 12% and 28% are RETIRED for new invoices but
# are kept here on purpose: invoices already issued at those rates must stay
# reproducible, and the rate is stored per line.
# TODO (confirm with a CA): whether 12/28 should still appear in the *dropdown*.
#
# ⚠️ 0% IS AMBIGUOUS. `Decimal("0.00")` currently covers BOTH nil-rated and
# exempt supplies, but GSTR-1 reports them in SEPARATE tables (nil-rated vs
# exempt). Do not treat them as interchangeable. Splitting them into distinct
# rates is a later migration plus a change to the calculator's tax gate.
GST_RATE_CHOICES = [
    (Decimal("0.00"), "0%"),
    (Decimal("0.25"), "0.25%"),
    (Decimal("1.50"), "1.5%"),
    (Decimal("3.00"), "3%"),
    (Decimal("5.00"), "5%"),
    (Decimal("12.00"), "12% (retired)"),
    (Decimal("18.00"), "18%"),
    (Decimal("28.00"), "28% (retired)"),
    (Decimal("40.00"), "40%"),
]

DEFAULT_GST_RATE = Decimal("18.00")

#: Fast membership test: {Decimal("0.00"), Decimal("5.00"), ...}
VALID_GST_RATES = frozenset(rate for rate, _ in GST_RATE_CHOICES)

# ---------------------------------------------------------------------------
# Measuring units
# ---------------------------------------------------------------------------
# (code, label). Codes are stored in a max_length=8 column.
MEASURING_UNITS = [
    ("PCS", "Pieces"),
    ("NOS", "Numbers"),
    ("KG", "Kilogram"),
    ("GM", "Gram"),
    ("LTR", "Litre"),
    ("ML", "Millilitre"),
    ("MTR", "Metre"),
    ("CM", "Centimetre"),
    ("FT", "Foot"),
    ("M2", "Square metre"),
    ("M3", "Cubic metre"),
    ("BOX", "Box"),
    ("BAG", "Bag"),
    ("CBM", "Cubic metre (CBM)"),
    ("DZN", "Dozen"),
    ("SET", "Set"),
    ("PAIR", "Pair"),
    ("SQFT", "Square foot"),
    ("HOUR", "Hour"),
    ("DAY", "Day"),
    ("MONTH", "Month"),
]

DEFAULT_UNIT = "PCS"
VALID_UNITS = frozenset(code for code, _ in MEASURING_UNITS)

# ---------------------------------------------------------------------------
# Union territories without a legislature
# ---------------------------------------------------------------------------
# Intra-state supply inside these UTs is charged **CGST + UTGST**, not CGST + SGST.
# The arithmetic is identical, so the amount is stored in the same columns and
# only the label differs (see `state_tax_label` in the GST calculator).
# The other UTs/UTs (J&K, Delhi, Puducherry) do have legislatures, so they use SGST.
#
# ⚠️ Confirm this list with a CA before launch (1.4.8 compliance Q4).
UT_WITHOUT_LEGISLATURE = frozenset({"04", "26", "31", "35", "38"})


def is_ut_without_legislature(state_code: str) -> bool:
    """True when intra-state tax in this state code is CGST + UTGST."""
    return state_code in UT_WITHOUT_LEGISLATURE

# ---------------------------------------------------------------------------
# HSN (goods) / SAC (services) codes
# ---------------------------------------------------------------------------
# IMPORTANT: HSN and SAC digit ranges OVERLAP - a 6-digit code is valid for both
# a product and a service, so a code cannot be classified by its digits alone.
# The real GST rule is that HSN/SAC codes are only ever 4, 6 or 8 digits long
# (5 and 7 are never valid), and SAC codes never exceed 6.
HSN_CODE_LENGTHS = (4, 6, 8)
SAC_CODE_LENGTHS = (4, 6)

HSN_PATTERN = r"^(?:[0-9]{4}|[0-9]{6}|[0-9]{8})$"
SAC_PATTERN = r"^(?:[0-9]{4}|[0-9]{6})$"

#: Services must print this description on a tax invoice alongside the SAC code.
SERVICE_DESCRIPTION_MAX_LENGTH = 80

_HSN_RE = re.compile(HSN_PATTERN)
_SAC_RE = re.compile(SAC_PATTERN)


# ---------------------------------------------------------------------------
# Helpers (so callers never re-implement these rules)
# ---------------------------------------------------------------------------
def state_name(code: str) -> str:
    """Human-readable state name from a 2-digit GST state code."""
    return dict(GST_STATE_CHOICES).get(code, code)


def valid_gst_rate(rate) -> bool:
    """True if `rate` is one of the supported GST slabs (Decimal-safe)."""
    if rate is None or rate == "":
        return False
    try:
        return Decimal(str(rate)) in VALID_GST_RATES
    except (ArithmeticError, ValueError):
        return False


def gst_rate_label(rate) -> str:
    """'18.00' -> '18%' for display in the UI and on invoices."""
    try:
        value = Decimal(str(rate))
    except (ArithmeticError, ValueError):
        return str(rate)
    label = value.normalize()
    text = format(label, "f")
    return f"{text.rstrip('0').rstrip('.') if '.' in text else text}%"


def is_hsn(code: str) -> bool:
    """True if `code` is a structurally valid HSN (goods) code."""
    return bool(code) and bool(_HSN_RE.match(str(code).strip()))


def is_sac(code: str) -> bool:
    """True if `code` is a structurally valid SAC (services) code."""
    return bool(code) and bool(_SAC_RE.match(str(code).strip()))