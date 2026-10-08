"""
Money and quantity helpers - the single owner of rounding rules.

Every rounding decision in the product lives here so the API, the billing
preview and the PDF can never disagree. Pure `Decimal`, `ROUND_HALF_UP` for
money, no database and no Django imports.

Why a dedicated module
----------------------
JavaScript's ``toFixed(2)`` and Python's ``Decimal``/``ROUND_HALF_UP`` disagree
on exactly the half-paisa values that GST billing produces:

    1.005  ->  JS 1.00   Decimal 1.01
    2.675  ->  JS 2.67   Decimal 2.68
    0.145  ->  JS 0.14   Decimal 0.15
    0.1 + 0.2 == 0.3    ->  false in JS

So tax arithmetic must happen on the server, exactly once, using these helpers.
"""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

#: Currency precision (money, tax, prices).
TWO_PLACES = Decimal("0.01")
#: Stock precision. 3 places so a 1.5 kg jar is 1.500 and never drifts.
THREE_PLACES = Decimal("0.001")
CENT = Decimal("0.01")

_ZERO = Decimal("0.00")


def q2(value) -> Decimal:
    """Round to 2 decimal places (money) using ROUND_HALF_UP."""
    return _to_decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def q3(value) -> Decimal:
    """Round to 3 decimal places (stock quantities) using ROUND_HALF_UP."""
    return _to_decimal(value).quantize(THREE_PLACES, rounding=ROUND_HALF_UP)


def q2_down(value) -> Decimal:
    """Round DOWN to 2 decimal places. Used where the split must not over-charge."""
    return _to_decimal(value).quantize(TWO_PLACES, rounding=ROUND_DOWN)


def _to_decimal(value) -> Decimal:
    """Coerce to Decimal without ever passing through a float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # A float already lost precision before reaching us. Reconstruct from the
        # shortest repr so Decimal("2.675") means 2.675 and not 2.67499...
        raise TypeError(
            "Refusing to convert a float to Decimal: convert the string at the "
            "boundary instead (float('2.675') has already lost precision)."
        )
    return Decimal(str(value))


# ---------------------------------------------------------------------------
# Tax split
# ---------------------------------------------------------------------------
def split_amount_intra(tax) -> tuple[Decimal, Decimal]:
    """
    Split an intra-state tax amount into (CGST, SGST).

    The split is done on the **amount**, never on the rate. Splitting the rate
    and multiplying twice can disagree with the tax by a paisa:

        tax on 10.55 @ 5% = 0.53
        rate split   -> 0.26 + 0.26 = 0.52   (wrong)
        amount split -> 0.26 + 0.27 = 0.53   (correct)

    CGST takes the rounded-DOWN half so the two halves can never sum to more
    than the tax itself (i.e. we never over-charge); SGST takes the remainder,
    so the halves always re-add to exactly `tax`.

    `tax` is expected to be 2dp already. Odd rates that cannot be halved to 2dp
    (0.25%, 1.5%) are handled correctly: 0.25 -> (0.12, 0.13).
    """
    tax = q2(tax)
    cgst = q2_down(tax / 2)
    sgst = tax - cgst
    return cgst, sgst


# ---------------------------------------------------------------------------
# Discount apportionment
# ---------------------------------------------------------------------------
def apportion_pro_rata(total, weights) -> list[Decimal]:
    """
    Split `total` across `weights` so the parts sum to `total` EXACTLY.

    Uses the largest-remainder method: each share is floored to 2dp, then the
    leftover paisas are handed to the largest fractional remainders. Ties break
    by position, so the result is deterministic for the same inputs (important
    for reproducible tests and for retrying a request).

        apportion_pro_rata("150.00", [1000, 500]) -> [100.00, 50.00]
        apportion_pro_rata("100.00", [1, 1, 1])   -> [33.34, 33.33, 33.33]

    All-zero weights fall back to an even split, which still sums exactly.
    """
    total = q2(total)
    weights = [_to_decimal(w) for w in weights]
    count = len(weights)
    if count == 0:
        return []

    weight_sum = sum(weights, Decimal("0"))
    if weight_sum <= 0:
        # Nothing to weight by: share evenly, then hand out the leftover paisas.
        return _even_split(total, count)

    exact = [(total * w) / weight_sum for w in weights]
    shares = [q2_down(x) for x in exact]

    shortfall = total - sum(shares, _ZERO)
    leftover_paisas = int((shortfall / CENT).to_integral_value())
    if leftover_paisas > 0:
        # Largest remainder first; position breaks ties deterministically.
        order = sorted(range(count), key=lambda i: (-(exact[i] - shares[i]), i))
        for step in range(leftover_paisas):
            shares[order[step % count]] += CENT
    return shares


def _even_split(total: Decimal, count: int) -> list[Decimal]:
    base = q2_down(total / count)
    shares = [base] * count
    leftover_paisas = int(((total - base * count) / CENT).to_integral_value())
    for step in range(max(leftover_paisas, 0)):
        shares[step % count] += CENT
    return shares


# ---------------------------------------------------------------------------
# Amount in words (required on a tax invoice)
# ---------------------------------------------------------------------------
_ONES = (
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
)
_TENS = (
    "", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
    "Eighty", "Ninety",
)


def _under_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] if ones == 0 else f"{_TENS[tens]} {_ONES[ones]}"


def _under_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    if hundreds and rest:
        return f"{_ONES[hundreds]} Hundred {_under_hundred(rest)}"
    if hundreds:
        return f"{_ONES[hundreds]} Hundred"
    return _under_hundred(rest)


def integer_to_words(number: int) -> str:
    """
    Indian grouping: crore, lakh, thousand, hundred.

        12345678 -> "Twelve Crore Twenty Three Lakh Forty Five Thousand
                     Six Hundred Seventy Eight"
    """
    if number < 0:
        return "Minus " + integer_to_words(-number)
    if number == 0:
        return "Zero"

    crore, rest = divmod(number, 10_000_000)
    lakh, rest = divmod(rest, 100_000)
    thousand, rest = divmod(rest, 1_000)
    remainder = rest

    chunks = []
    if crore:
        chunks.append(f"{integer_to_words(crore)} Crore")
    if lakh:
        chunks.append(f"{_under_hundred(lakh)} Lakh")
    if thousand:
        chunks.append(f"{_under_hundred(thousand)} Thousand")
    if remainder:
        chunks.append(_under_thousand(remainder))
    return " ".join(chunks)


def amount_in_words(amount) -> str:
    """
    Render a money amount the way an Indian tax invoice requires it.

        Decimal("123456.78")
            -> "Rupees One Lakh Twenty Three Thousand Four Hundred Fifty Six
                and Seventy Eight Paise Only"

    Handles zero, whole amounts, paise-only amounts and large values.
    """
    amount = q2(amount)
    negative = amount < 0
    amount = abs(amount)

    paise = int((amount * 100) % 100)
    rupees = int(amount)

    if rupees == 0 and paise == 0:
        text = "Rupees Zero Only"
    elif paise == 0:
        text = f"Rupees {integer_to_words(rupees)} Only"
    elif rupees == 0:
        text = f"Rupees Zero and {_under_hundred(paise)} Paise Only"
    else:
        text = (
            f"Rupees {integer_to_words(rupees)} and "
            f"{_under_hundred(paise)} Paise Only"
        )
    return f"Minus {text}" if negative else text


# ---------------------------------------------------------------------------
# GSTR-1 caveat shown next to the round-off toggle
# ---------------------------------------------------------------------------
ROUND_OFF_CAVEAT = (
    "Rounding changes only the amount payable on this invoice. It does not change "
    "any taxable value or tax amount. When you file GSTR-1 or GSTR-3B, report the "
    "taxable value and tax shown against each HSN/SAC code - never include the "
    "round-off as taxable value, and never treat it as a discount. Your HSN-wise "
    "summary must stay on the pre-rounding figures. If your accounts are audited, "
    "consider leaving rounding off so every invoice total equals the sum of its "
    "lines exactly."
)