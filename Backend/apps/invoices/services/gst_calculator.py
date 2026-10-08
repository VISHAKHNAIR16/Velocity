"""
GST calculator - the ONLY place tax arithmetic happens.

INVARIANT: THE BROWSER NEVER COMPUTES TAX.
=============================================
`POST /api/v1/invoices/preview/` and invoice persistence both call
`recalculate_invoice()` in this module. Do not add client-side tax maths.

Why: JavaScript's `toFixed(2)` and Python's `Decimal`/`ROUND_HALF_UP` disagree
on exactly the half-paisa values GST billing produces:

    1.005 -> JS 1.00, Decimal 1.01
    2.675 -> JS 2.67, Decimal 2.68
    0.145 -> JS 0.14, Decimal 0.15
    0.1 + 0.2 === 0.3  ->  false in JS

If the preview computed tax in the browser it would silently disagree with the
saved invoice by a fraction of a paisa, with no error shown - and the user would
have already quoted the wrong total to a customer.

This module is deliberately free of Django models and database access: it takes
plain values and returns plain dataclasses, so it can be exhaustively tested in
milliseconds (Step B of Phase 1.4). The service layer converts models to these
inputs, and the view converts the results to the API response.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from apps.core.constants import is_ut_without_legislature, valid_gst_rate
from apps.core.money import ROUND_OFF_CAVEAT, apportion_pro_rata, q2, q3, split_amount_intra

# Re-exported so the service layer and views have one import for the caveat text
# they must show next to the round-off toggle.
__all__ = [
    "LineInput",
    "LineResult",
    "InvoiceTotals",
    "GSTCalculationError",
    "SupplyType",
    "ROUND_OFF_CAVEAT",
    "calculate_invoice",
    "recalculate_invoice",
    "default_place_of_supply",
    "resolve_supply",
    "state_tax_label",
]

ZERO = Decimal("0.00")
ONE_HUNDRED = Decimal("100")


class GSTCalculationError(ValueError):
    """
    A refusal to calculate. Never coerce bad input into a number.

    `code` is machine-readable so the API layer can surface a stable error
    string (e.g. INVALID_TAX_RATE) alongside the human message.
    """

    def __init__(self, code: str, message: str, *, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


class SupplyType:
    INTRA = "INTRA"
    INTER = "INTER"


@dataclass(frozen=True)
class LineInput:
    """What the caller supplies for one invoice line. No totals - they are derived."""

    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal
    line_discount: Decimal = ZERO
    #: "PRODUCT" or "SERVICE". A snapshot from the line, never re-read from the item.
    item_type: str = "PRODUCT"

    @classmethod
    def build(cls, *, quantity, unit_price, tax_rate, line_discount=ZERO, item_type="PRODUCT"):
        return cls(
            quantity=_dec(quantity),
            unit_price=_dec(unit_price),
            tax_rate=_dec(tax_rate),
            line_discount=_dec(line_discount),
            item_type=item_type or "PRODUCT",
        )


@dataclass(frozen=True)
class LineResult:
    index: int
    item_type: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal
    gross_amount: Decimal
    line_discount: Decimal
    invoice_discount_share: Decimal
    net_amount: Decimal
    taxable_value: Decimal
    tax_amount: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    total_amount: Decimal


@dataclass(frozen=True)
class InvoiceTotals:
    lines: list[LineResult] = field(default_factory=list)
    subtotal: Decimal = ZERO
    total_discount: Decimal = ZERO
    taxable_total: Decimal = ZERO
    cgst_total: Decimal = ZERO
    sgst_total: Decimal = ZERO
    igst_total: Decimal = ZERO
    round_off: Decimal = ZERO
    grand_total: Decimal = ZERO
    supply_type: str = SupplyType.INTRA
    state_tax_label: str = "SGST"
    document_title: str = "Tax Invoice"

    @property
    def tax_total(self) -> Decimal:
        return self.cgst_total + self.sgst_total + self.igst_total

    def as_dicts(self) -> list[dict]:
        """Per-line dicts, ready to be handed to a serializer."""
        return [
            {
                "index": r.index,
                "item_type": r.item_type,
                "quantity": str(r.quantity),
                "unit_price": str(r.unit_price),
                "tax_rate": str(r.tax_rate),
                "gross_amount": str(r.gross_amount),
                "line_discount": str(r.line_discount),
                "invoice_discount_share": str(r.invoice_discount_share),
                "net_amount": str(r.net_amount),
                "taxable_value": str(r.taxable_value),
                "tax_amount": str(r.tax_amount),
                "cgst_amount": str(r.cgst_amount),
                "sgst_amount": str(r.sgst_amount),
                "igst_amount": str(r.igst_amount),
                "total_amount": str(r.total_amount),
            }
            for r in self.lines
        ]


def _dec(value) -> Decimal:
    """Decimal-only coercion; a float is a bug, not an input format."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise GSTCalculationError(
            "INVALID_NUMBER",
            "Amounts must be Decimal or a numeric string, never a float.",
        )
    if isinstance(value, int):
        return Decimal(value)
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - surfaced as a domain error
        raise GSTCalculationError("INVALID_NUMBER", f"Cannot read {value!r} as a number.") from exc


# ---------------------------------------------------------------------------
# Place of supply
# ---------------------------------------------------------------------------
def default_place_of_supply(
    *,
    billing_state: str,
    shipping_state: str = "",
    has_goods_line: bool = False,
) -> str:
    """
    Default place of supply for the invoice.

    Goods are governed by the place of **delivery** (GST s.10(1)(a)); services by
    the recipient's location. So a goods sale prefers the shipping state and
    falls back to the billing state. Mixed carts use the goods rule, which is
    why the caller passes `has_goods_line` rather than a per-line decision.

    A user can always override the result on the invoice.
    """
    billing_state = (billing_state or "").strip()
    shipping_state = (shipping_state or "").strip()
    if not billing_state:
        raise GSTCalculationError(
            "PARTY_STATE_MISSING",
            "The customer's state is required to decide the place of supply.",
            field="party",
        )
    if has_goods_line and shipping_state:
        return shipping_state
    return billing_state


def resolve_supply(*, business_state: str, place_of_supply: str) -> tuple[str, str]:
    """
    Return `(supply_type, state_tax_label)` for a resolved place of supply.

    Intra-state inside a UT without a legislature is CGST + UTGST: same
    arithmetic, different label.
    """
    business_state = (business_state or "").strip()
    place_of_supply = (place_of_supply or "").strip()
    if not business_state:
        raise GSTCalculationError(
            "BUSINESS_PROFILE_INCOMPLETE",
            "Set your business state before creating invoices.",
            field="business",
        )
    if not place_of_supply:
        raise GSTCalculationError(
            "PLACE_OF_SUPPLY_MISSING",
            "Select a place of supply for this invoice.",
            field="place_of_supply",
        )
    if business_state == place_of_supply:
        label = "UTGST" if is_ut_without_legislature(place_of_supply) else "SGST"
        return SupplyType.INTRA, label
    return SupplyType.INTER, "IGST"


def state_tax_label(place_of_supply: str, supply_type: str) -> str:
    """Public helper so the UI/PDF can label the column without duplicating the rule."""
    if supply_type == SupplyType.INTER:
        return "IGST"
    return "UTGST" if is_ut_without_legislature(place_of_supply) else "SGST"


# ---------------------------------------------------------------------------
# The calculation
# ---------------------------------------------------------------------------
def calculate_invoice(
    lines,
    *,
    business_state: str,
    place_of_supply: str,
    business_is_regular: bool = True,
    invoice_discount=ZERO,
    prices_include_tax: bool = False,
    round_invoice_total: bool = False,
    document_title: str = "",
) -> InvoiceTotals:
    """
    Calculate an invoice from line inputs. Pure: no models, no database, no request.

    Per line, in this exact order (a different order is the usual bug):
      1. gross   = q2(quantity * unit_price)
      2. net     = gross - line_discount                      (reject discount > gross)
      3. net     = net - apportioned invoice_discount_share
      4. exclusive: taxable = net,   tax = q2(taxable * rate/100)
         inclusive: taxable = q2(net / (1 + rate/100)), tax = net - taxable
      5. intra  -> split_amount_intra(tax);  inter -> igst = tax
      6. total   = taxable + tax
    """
    supply_type, label = resolve_supply(
        business_state=business_state, place_of_supply=place_of_supply
    )

    line_inputs = list(lines or [])
    if not line_inputs:
        raise GSTCalculationError("NO_LINES", "An invoice needs at least one line.")

    _validate_lines(line_inputs, business_is_regular)
    # MUST use the return value: the gate rewrites every line's rate to 0.
    line_inputs = _apply_registration_gate(line_inputs, business_is_regular)
    gross_values = [q2(item.quantity * item.unit_price) for item in line_inputs]
    subtotal = sum(gross_values, ZERO)

    invoice_discount = _dec(invoice_discount)
    if invoice_discount < 0:
        raise GSTCalculationError(
            "INVALID_DISCOUNT", "An invoice discount cannot be negative.", field="discount"
        )
    if invoice_discount > subtotal:
        raise GSTCalculationError(
            "INVALID_DISCOUNT",
            "The invoice discount is greater than the subtotal.",
            field="discount",
        )

    # Apportioned across the post-line-discount net values, so the shares track
    # what is actually taxable rather than the undiscounted gross.
    net_values = [gross - item.line_discount for gross, item in zip(gross_values, line_inputs, strict=True)]
    net_total = sum(net_values, ZERO)

    # The real constraint: total discounts may not exceed what is left to
    # discount, which is the post-line-discount amount (not the gross subtotal).
    if invoice_discount > net_total:
        raise GSTCalculationError(
            "INVALID_DISCOUNT",
            "The discounts exceed the invoice amount.",
            field="discount",
        )

    shares = apportion_pro_rata(invoice_discount, net_values) if invoice_discount else [
        ZERO for _ in line_inputs
    ]

    results: list[LineResult] = []
    for index, (item, gross, net_before_share, share) in enumerate(
        zip(line_inputs, gross_values, net_values, shares, strict=True)
    ):
        net = net_before_share - share
        if net < 0:
            raise GSTCalculationError(
                "INVALID_DISCOUNT",
                "The discounts on this line exceed its amount.",
                field="line_discount",
            )

        if prices_include_tax:
            # Extract the tax rather than recomputing it: recomputing
            # q2(net * rate/100) can overshoot by a paisa and break the total.
            #   taxable = net * 100 / (100 + rate)
            #   tax     = net - taxable
            taxable = q2(net * ONE_HUNDRED / (ONE_HUNDRED + item.tax_rate))
            tax = net - taxable
        else:
            taxable = net
            tax = q2(net * item.tax_rate / ONE_HUNDRED)

        cgst = sgst = igst = ZERO
        if supply_type == SupplyType.INTRA:
            cgst, sgst = split_amount_intra(tax)
        else:
            igst = tax

        results.append(
            LineResult(
                index=index,
                item_type=item.item_type,
                quantity=q3(item.quantity),
                unit_price=q2(item.unit_price),
                tax_rate=item.tax_rate,
                gross_amount=gross,
                line_discount=q2(item.line_discount),
                invoice_discount_share=q2(share),
                net_amount=q2(net),
                taxable_value=q2(taxable),
                tax_amount=q2(tax),
                cgst_amount=cgst,
                sgst_amount=sgst,
                igst_amount=igst,
                total_amount=q2(taxable) + q2(tax),
            )
        )

    sum_of_line_totals = sum((r.total_amount for r in results), ZERO)

    grand_total = sum_of_line_totals
    round_off = ZERO
    if round_invoice_total:
        # Nearest rupee, half up. Presentation only: taxable values and tax are
        # already final and are not touched.
        grand_total = sum_of_line_totals.quantize(Decimal("1"), rounding="ROUND_HALF_UP")
        round_off = grand_total - sum_of_line_totals

    title = document_title or ("Tax Invoice" if business_is_regular else "Bill of Supply")

    return InvoiceTotals(
        lines=results,
        subtotal=q2(subtotal),
        total_discount=q2(
            sum((item.line_discount for item in line_inputs), ZERO) + invoice_discount
        ),
        taxable_total=q2(sum((r.taxable_value for r in results), ZERO)),
        cgst_total=q2(sum((r.cgst_amount for r in results), ZERO)),
        sgst_total=q2(sum((r.sgst_amount for r in results), ZERO)),
        igst_total=q2(sum((r.igst_amount for r in results), ZERO)),
        round_off=q2(round_off),
        grand_total=q2(grand_total),
        supply_type=supply_type,
        state_tax_label=label,
        document_title=title,
    )


def recalculate_invoice(*args, **kwargs) -> InvoiceTotals:
    """
    The single entry point used by BOTH `/invoices/preview/` and invoice
    persistence. Two names would be two places to keep in step.
    """
    return calculate_invoice(*args, **kwargs)


def _validate_lines(lines: list[LineInput], business_is_regular: bool) -> None:
    for index, item in enumerate(lines):
        if item.quantity <= 0:
            raise GSTCalculationError(
                "INVALID_QUANTITY",
                "Quantity must be greater than zero.",
                field=f"lines[{index}].quantity",
            )
        if item.unit_price < 0:
            raise GSTCalculationError(
                "INVALID_PRICE",
                "Price cannot be negative.",
                field=f"lines[{index}].unit_price",
            )
        if item.line_discount < 0:
            raise GSTCalculationError(
                "INVALID_DISCOUNT",
                "Line discount cannot be negative.",
                field=f"lines[{index}].line_discount",
            )
        if item.line_discount > q2(item.quantity * item.unit_price):
            raise GSTCalculationError(
                "INVALID_DISCOUNT",
                "Line discount cannot be greater than the line amount.",
                field=f"lines[{index}].line_discount",
            )
        # An unregistered/composition business is forced to 0% below, so only
        # validate the rate when tax is actually being charged.
        if business_is_regular and not valid_gst_rate(item.tax_rate):
            raise GSTCalculationError(
                "INVALID_TAX_RATE",
                f"{item.tax_rate}% is not a supported GST rate.",
                field=f"lines[{index}].tax_rate",
            )
        if item.tax_rate < 0:
            raise GSTCalculationError(
                "INVALID_TAX_RATE",
                "Tax rate cannot be negative.",
                field=f"lines[{index}].tax_rate",
            )


def _apply_registration_gate(lines: list[LineInput], business_is_regular: bool) -> list[LineInput]:
    """
    Only a REGULAR business may charge GST. Composition and Unregistered
    businesses must show zero tax and the title 'Bill of Supply'.
    """
    if business_is_regular:
        return lines
    return [
        LineInput(
            quantity=item.quantity,
            unit_price=item.unit_price,
            tax_rate=ZERO,
            line_discount=item.line_discount,
            item_type=item.item_type,
        )
        for item in lines
    ]