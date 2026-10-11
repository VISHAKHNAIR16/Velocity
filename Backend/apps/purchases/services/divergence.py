"""
Compare figures a user typed against what the GST calculator produces.

Pure: no models, no database, no request. Same discipline as `stock_plan.py`, so
the tests run in milliseconds and this logic can be exercised exhaustively.

Why a purchase needs this at all
--------------------------------
A sales invoice's numbers are computed and the request body is ignored if it
disagrees - we authored that document. A purchase bill is the reverse: the
supplier authored it, their return has to match it, and their rounding may
legitimately differ from ours. So the entered figures are stored verbatim and
this module's only job is to say how far apart the two versions are.

Why the computed figures are stored too
---------------------------------------
Because a discarded discrepancy is a discarded discrepancy. Keeping both lets
`audit_purchases` answer the question that actually matters months later: was
this supplier's rounding a one-off, or does it happen on every bill? That is the
difference between a two-minute note and a conversation with your accountant
before the 4.2 GSTR-3B figures have to reconcile.

The tolerance
-------------
`TOLERANCE` is Rs 1, matching the spec. A paisa of supplier rounding is noise;
anything larger is worth seeing. It is a *reporting* threshold, not a rejection:
nothing here ever refuses a bill.
"""

from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal("0.00")

#: Differences at or below this are not worth mentioning.
TOLERANCE = Decimal("1.00")

#: The header fields compared, entered name -> computed name.
HEADER_FIELDS = (
    "taxable_total",
    "cgst_total",
    "sgst_total",
    "igst_total",
    "grand_total",
)


@dataclass(frozen=True)
class FieldDivergence:
    """One field where the entered figure and the computed figure disagree."""

    field: str
    entered: Decimal
    computed: Decimal

    @property
    def difference(self) -> Decimal:
        """Signed: positive means the user typed more than the maths produced."""
        return (self.entered or ZERO) - (self.computed or ZERO)

    @property
    def is_material(self) -> bool:
        return abs(self.difference) > TOLERANCE

    def __str__(self) -> str:
        sign = "+" if self.difference > 0 else ""
        return f"{self.field}: entered {self.entered}, computed {self.computed} ({sign}{self.difference})"


@dataclass
class DivergenceReport:
    """Everything that did not agree, header and per line."""

    header: list = field(default_factory=list)
    lines: list = field(default_factory=list)  # each: (index, [FieldDivergence, ...])
    tolerance: Decimal = TOLERANCE

    @property
    def all_divergences(self) -> list:
        return list(self.header) + [d for _, group in self.lines for d in group]

    @property
    def material(self) -> list:
        """Only the ones above tolerance - what a user should actually read."""
        return [d for d in self.all_divergences if d.is_material]

    @property
    def has_divergence(self) -> bool:
        return bool(self.material)

    @property
    def max_difference(self) -> Decimal:
        """
        Largest absolute gap anywhere.

        Stored on the bill so a list view can filter "bills that did not add up"
        without recalculating anything.
        """
        differences = [abs(d.difference) for d in self.all_divergences]
        return max(differences) if differences else ZERO

    def summary(self) -> str:
        if not self.has_divergence:
            return "Entered figures agree with the calculator."
        worst = max(self.material, key=lambda d: abs(d.difference))
        return (
            f"{len(self.material)} figure(s) differ from the calculator by more than "
            f"Rs {self.tolerance}; the largest is {worst.field} by Rs {abs(worst.difference)}."
        )


def _compare_pair(field: str, entered, computed) -> FieldDivergence:
    return FieldDivergence(
        field=field,
        entered=Decimal(str(entered if entered is not None else 0)),
        computed=Decimal(str(computed if computed is not None else 0)),
    )


def compare_header(entered, computed, *, tolerance: Decimal = TOLERANCE) -> list:
    """
    Compare the five header totals.

    `entered` and `computed` are both mapping-like (a model, a dataclass or a
    dict all work) - attribute access is tried first, then key access, so this
    stays usable from a model row and from a plain dict in tests.
    """
    divergences = []
    for name in HEADER_FIELDS:
        divergences.append(
            _compare_pair(name, _read(entered, name), _read(computed, name))
        )
    return [d for d in divergences if abs(d.difference) > ZERO]


def compare_lines(entered_lines, computed_lines, *, tolerance: Decimal = TOLERANCE) -> list:
    """
    Compare per-line amounts, positionally.

    Positional rather than by item id because a purchase line can be free-text
    with no item at all, and because the user controls line order. A length
    mismatch is reported rather than padded: silently comparing fewer lines than
    exist would hide exactly the kind of problem this module exists to surface.
    """
    if len(entered_lines) != len(computed_lines):
        # Index -1 means "the document as a whole", not a particular line. The
        # shape stays `(index, [divergences])` like every other result, so
        # consumers never have to special-case this branch.
        return [
            (
                -1,
                [
                    _compare_pair(
                        "line_count",
                        Decimal(len(entered_lines)),
                        Decimal(len(computed_lines)),
                    )
                ],
            )
        ]

    result = []
    # `strict=True` is safe here precisely because the length mismatch was handled
    # above - and it turns a future refactor that reintroduces one into a loud
    # error rather than a silently truncated comparison.
    for index, (entered, computed) in enumerate(
        zip(entered_lines, computed_lines, strict=True)
    ):
        group = [
            _compare_pair(name, _read(entered, name), _read(computed, name))
            for name in ("taxable_value", "cgst_amount", "sgst_amount", "igst_amount", "total_amount")
        ]
        group = [d for d in group if abs(d.difference) > ZERO]
        if group:
            result.append((index, group))
    return result


def _iter_lines(source, attribute: str) -> list:
    """
    Read a line collection off a document.

    On a Django model `source.items` is a **RelatedManager**, not a list, and
    `list(manager)` raises `TypeError`. Everything else - a plain list, a
    queryset, a dict entry - works as written. The `.all()` branch is what makes
    this usable on a real model row.
    """
    value = getattr(source, attribute, None)
    if value is None and isinstance(source, dict):
        value = source.get(attribute)
    if value is None:
        return []
    return list(value.all() if hasattr(value, "all") else value)


def build_report(entered, computed, *, tolerance: Decimal = TOLERANCE) -> DivergenceReport:
    """
    The full comparison for one bill.

    `entered` is the saved/entered document; `computed` is an `InvoiceTotals`
    (or anything exposing `.lines`).
    """
    computed_header = getattr(computed, "header", None)
    if computed_header is None:
        computed_header = computed

    report = DivergenceReport(
        header=compare_header(entered, computed_header, tolerance=tolerance),
        lines=compare_lines(
            _iter_lines(entered, "items"),
            _iter_lines(computed, "lines"),
            tolerance=tolerance,
        ),
        tolerance=tolerance,
    )
    return report


def _read(source, name: str):
    """Attribute first, then key. Lets models and plain dicts share this code."""
    value = getattr(source, name, None)
    if value is None and isinstance(source, dict):
        value = source.get(name)
    return value