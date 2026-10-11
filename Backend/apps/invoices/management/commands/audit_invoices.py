"""
`audit_invoices` - re-derive every issued document from its stored inputs and
report anything that does not agree.

Why (decision 26, step 2.0.8)
------------------------------
The 2.0.7 constraints stop *impossible* rows (a negative total, an issued invoice
with no number). They cannot stop a row that is possible but **wrong** - a
computed column that no longer matches its inputs, a missing number in a series,
a supply type inconsistent with the states. Nothing re-checks those today, so
corruption or a future bug would be found by a customer rather than by us.

Run it in CI against the test dataset, and weekly as a cron on the real one.
**Exits 1 on any finding** so those two callers can actually alert.

What it checks
--------------
1. Totals recomputed from the STORED line inputs, compared field by field.
2. `sum(lines) == invoice` for taxable and each tax component.
3. Number series: no duplicates, no gaps, counter matches the maximum issued.
4. `supply_type` consistent with business state vs place of supply.

Deliberate non-check
--------------------
`round_invoice_total` is NOT snapshotted on the invoice (it is a live profile
setting), so re-applying today's setting to an old invoice would produce false
alarms. Instead the arithmetic is checked: `grand_total == Σ lines + round_off`,
`|round_off| <= 0.50`, and `grand_total` a whole rupee whenever `round_off != 0`.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandParser
from django.db.models import Count

from apps.core.fiscal import financial_year
from apps.core.money import q2
from apps.invoices.models import Invoice, InvoiceCounter, InvoiceStatus
from apps.invoices.services.draft import build_line_inputs
from apps.invoices.services.gst_calculator import GSTCalculationError, calculate_invoice

ZERO = Decimal("0.00")
HALF_RUPEE = Decimal("0.50")

#: The money columns compared on the invoice and on each line.
MONEY_FIELDS = (
    "subtotal",
    "total_discount",
    "taxable_total",
    "cgst_total",
    "sgst_total",
    "igst_total",
    "grand_total",
)


class Finding:
    __slots__ = ("invoice", "code", "detail")

    def __init__(self, invoice, code: str, detail: str):
        self.invoice = invoice
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        label = self.invoice.display_number if self.invoice else "(no invoice)"
        return f"{label}: {self.code} - {self.detail}"


class Command(BaseCommand):
    help = "Re-derive issued invoices from their stored inputs and report disagreements."

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--business", type=int, help="Only audit this business id.")
        parser.add_argument("--fy", type=str, help="Only this financial year, e.g. 2026-27.")
        parser.add_argument(
            "--status",
            choices=[InvoiceStatus.ISSUED, InvoiceStatus.CANCELLED],
            default=None,
            help="Limit to one status (default: both).",
        )

    def handle(self, *args, **options):
        queryset = Invoice.objects.select_related("business", "party").order_by("id")
        if options["business"]:
            queryset = queryset.filter(business_id=options["business"])
        if options["fy"]:
            start, end = _fy_years(options["fy"])
            queryset = queryset.filter(invoice_date__year__in=(start, end))
        if options["status"]:
            queryset = queryset.filter(status=options["status"])

        # DRAFTs are deliberately excluded: they are editable working documents
        # and may legitimately hold half-finished figures.
        queryset = queryset.exclude(status=InvoiceStatus.DRAFT)

        findings: list[Finding] = []
        checked = 0

        for invoice in queryset.iterator():
            checked += 1
            findings.extend(self._audit_invoice(invoice))

        findings.extend(self._audit_number_series(queryset))

        for finding in findings:
            self.stderr.write(str(finding))

        if findings:
            self.stderr.write(self.style.ERROR(f"\n{len(findings)} finding(s) across {checked} invoice(s)."))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(f"OK: {checked} issued invoice(s) agree with their inputs."))

    # ------------------------------------------------------------------
    def _audit_invoice(self, invoice: Invoice) -> list[Finding]:
        findings: list[Finding] = []
        lines = list(invoice.items.all())

        if not lines:
            findings.append(Finding(invoice, "NO_LINES", "an issued invoice has no lines"))
            return findings

        # 1. Recompute from the stored inputs.
        try:
            expected = calculate_invoice(
                build_line_inputs(lines),
                origin_state=invoice.business_state_code,
                place_of_supply=invoice.place_of_supply,
                business_is_regular=(
                    invoice.business.gst_registration_type
                    == invoice.business.GstRegistrationType.REGULAR
                ),
                invoice_discount=invoice.invoice_discount,
                prices_include_tax=invoice.prices_include_tax,
                # NOT invoice.business.round_invoice_total: that setting is live
                # and not snapshotted, so today's value must not be applied to an
                # old invoice. The round-off arithmetic is checked separately.
                round_invoice_total=False,
                document_title=invoice.document_title,
            )
        except GSTCalculationError as exc:
            findings.append(
                Finding(invoice, "RECALC_REFUSED", f"{exc.code}: {exc.message}")
            )
            return findings

        for field in MONEY_FIELDS:
            stored = q2(getattr(invoice, field))
            wanted = q2(getattr(expected, field))
            if stored != wanted:
                findings.append(
                    Finding(
                        invoice,
                        "MONEY_MISMATCH",
                        f"{field}: stored {stored} but the stored line inputs "
                        f"recompute to {wanted}",
                    )
                )

        # 2. Lines must sum to the invoice.
        line_totals = {
            "taxable_total": sum((ln.taxable_value for ln in lines), ZERO),
            "cgst_total": sum((ln.cgst_amount for ln in lines), ZERO),
            "sgst_total": sum((ln.sgst_amount for ln in lines), ZERO),
            "igst_total": sum((ln.igst_amount for ln in lines), ZERO),
        }
        for field, summed in line_totals.items():
            stored = q2(getattr(invoice, field))
            if q2(summed) != stored:
                findings.append(
                    Finding(
                        invoice,
                        "LINE_SUM_MISMATCH",
                        f"{field}: lines sum to {q2(summed)} but the invoice says {stored}",
                    )
                )

        for line in lines:
            line_tax = q2(line.cgst_amount + line.sgst_amount + line.igst_amount)
            if line_tax != q2(line.total_amount - line.taxable_value):
                findings.append(
                    Finding(
                        invoice,
                        "LINE_TAX_ARITHMETIC",
                        f"line {line.pk} ({line.item_name}): "
                        f"taxable {line.taxable_value} + tax {line_tax} != total {line.total_amount}",
                    )
                )

        # 3. Round-off arithmetic (setting not snapshotted - see module docstring).
        findings.extend(self._audit_round_off(invoice, expected))

        # 4. Supply type consistent with the states, and a Bill of Supply is untaxed.
        findings.extend(self._audit_supply_type(invoice))

        return findings

    def _audit_round_off(self, invoice: Invoice, expected) -> list[Finding]:
        findings: list[Finding] = []
        round_off = q2(invoice.round_off)

        if abs(round_off) > HALF_RUPEE:
            findings.append(
                Finding(
                    invoice,
                    "ROUND_OFF_TOO_LARGE",
                    f"round_off {round_off} exceeds the +/- 0.50 a rounding can produce",
                )
            )

        # `expected` was recomputed with rounding OFF, so `grand_total` is the
        # unrounded figure. Anything else would make this check self-fulfilling.
        unrounded = q2(expected.grand_total)
        if q2(invoice.grand_total) - unrounded != round_off:
            findings.append(
                Finding(
                    invoice,
                    "ROUND_OFF_DISAGREES",
                    f"grand_total {invoice.grand_total} minus the unrounded "
                    f"{unrounded} is not the stored round_off {round_off}",
                )
            )

        # If a rounding was applied at all, the payable must land on a whole
        # rupee - that is the entire point of the feature.
        if round_off != ZERO and unrounded.to_integral_value() != unrounded:
            findings.append(
                Finding(
                    invoice,
                    "ROUND_OFF_NOT_WHOLE_RUPEE",
                    f"with round_off {round_off} the payable should be a whole "
                    f"rupee, but the unrounded total is {unrounded}",
                )
            )
        return findings

    def _audit_supply_type(self, invoice: Invoice) -> list[Finding]:
        findings: list[Finding] = []
        business_state = invoice.business_state_code or ""
        place = invoice.place_of_supply or ""

        if not business_state or not place:
            findings.append(
                Finding(
                    invoice,
                    "PLACE_OF_SUPPLY_UNRESOLVED",
                    "a stored state is missing, so the tax type cannot be justified",
                )
            )
            return findings

        expected = "INTRA" if business_state == place else "INTER"
        if invoice.supply_type != expected:
            findings.append(
                Finding(
                    invoice,
                    "SUPPLY_TYPE_INCONSISTENT",
                    f"stored {invoice.supply_type} but business state {business_state} "
                    f"vs place of supply {place} implies {expected}",
                )
            )

        is_bill_of_supply = invoice.document_title == "Bill of Supply"
        if is_bill_of_supply:
            for field in ("cgst_total", "sgst_total", "igst_total"):
                if q2(getattr(invoice, field)) != ZERO:
                    findings.append(
                        Finding(
                            invoice,
                            "TAX_ON_BILL_OF_SUPPLY",
                            f"a Bill of Supply must carry zero {field}, found {getattr(invoice, field)}",
                        )
                    )
        return findings

    def _audit_number_series(self, queryset) -> list[Finding]:
        """
        No duplicates, no gaps, counter matches the maximum issued.

        A gap means a number was allocated and never used - the invoice is missing
        from the table, or a bug consumed it. Cancelled numbers are deliberately
        NOT gaps: the number is never freed.
        """
        findings: list[Finding] = []

        pairs = (
            queryset.values("business_id", "invoice_number")
            .annotate(n=Count("id"))
            .filter(invoice_number__gt="")
        )
        for row in pairs:
            if row["n"] > 1:
                findings.append(
                    Finding(
                        None,
                        "DUPLICATE_INVOICE_NUMBER",
                        f"business {row['business_id']} has {row['n']} invoices numbered {row['invoice_number']}",
                    )
                )

        by_fy: dict[tuple[int, str], list[str]] = {}
        for invoice in queryset.filter(invoice_number__gt="").iterator():
            key = (invoice.business_id, financial_year(invoice.invoice_date))
            by_fy.setdefault(key, []).append(invoice.invoice_number)

        for (business_id, fy), numbers in by_fy.items():
            expected = 1
            for number in sorted(numbers):
                tail = number.rsplit("/", 1)[-1]
                try:
                    value = int(tail)
                except ValueError:
                    findings.append(
                        Finding(
                            None,
                            "UNPARSEABLE_INVOICE_NUMBER",
                            f"business {business_id} FY {fy}: {number!r} does not end in a number",
                        )
                    )
                    continue
                if value != expected:
                    findings.append(
                        Finding(
                            None,
                            "GAP_IN_NUMBER_SERIES",
                            f"business {business_id} FY {fy}: expected {expected}, found {number}",
                        )
                    )
                expected = max(expected, value) + 1

            highest = max(int(n.rsplit("/", 1)[-1]) for n in numbers)
            counter = InvoiceCounter.objects.filter(
                business_id=business_id, financial_year=fy
            ).first()
            if counter is None:
                findings.append(
                    Finding(
                        None,
                        "MISSING_COUNTER",
                        f"business {business_id} FY {fy} has issued numbers but no counter row",
                    )
                )
            elif counter.last_number != highest:
                findings.append(
                    Finding(
                        None,
                        "COUNTER_DISAGREES",
                        f"business {business_id} FY {fy}: counter says "
                        f"{counter.last_number} but the highest issued is {highest}",
                    )
                )

        return findings


def _fy_years(label: str) -> tuple[int, int]:
    """'2026-27' -> (2026, 2027). A date filter needs real years."""
    start = int(label.split("-")[0])
    return (start, start + 1)