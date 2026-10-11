"""
`audit_purchases` - check posted purchase bills against their stored inputs.

The exit-1 contract, the `--business/--fy/--status` filters and the
"excluded drafts" rule all mirror `audit_invoices`, so a cron or CI step can run
both and mean the same thing by each.

What makes a purchase different, and what this command therefore does NOT do
--------------------------------------------------------------------------
**No gap check on bill numbers.** `audit_invoices` flags gaps in our own invoice
series, because we allocate those numbers and a gap means a bug. Purchase bill
numbers belong to the supplier. They need not be consecutive, they need not even
be numeric, and a gap is the normal state of affairs. Duplicates are still
checked - that is the mistake the unique constraint exists for - but nothing
about the sequence is our business.

What it does check, in order of how much it matters:

1. The header agrees with the entered lines. This is a mistake in *this*
   document and is always a finding.
2. The entered figures agree with the calculator, within tolerance. This is a
   fact about the supplier's bill, so it is reported as `DIVERGENCE` and is the
   reason the `*_computed` columns exist at all.
3. A supplier who cannot charge tax has a bill with no tax on it.
4. The duplicate guard holds.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.core.fiscal import fy_bounds
from apps.purchases.models import Purchase, PurchaseStatus
from apps.purchases.services.divergence import TOLERANCE, build_report
from apps.purchases.services.draft import sum_entered
from apps.purchases.services.lifecycle import recalculate_draft, supplier_can_charge_tax

ZERO = Decimal("0.00")


class Finding:
    __slots__ = ("purchase", "code", "detail")

    def __init__(self, purchase, code: str, detail: str):
        self.purchase = purchase
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        return (
            f"{self.purchase.display_number if self.purchase else '(series)'}: "
            f"{self.code} - {self.detail}"
        )


class Command(BaseCommand):
    help = (
        "Check posted purchase bills against their stored inputs. "
        "Exits 1 on any finding."
    )

    def add_arguments(self, parser):
        parser.add_argument("--business", type=int, default=None)
        parser.add_argument("--fy", default=None, help='e.g. "2026-27"')
        parser.add_argument(
            "--status",
            choices=["POSTED", "CANCELLED"],
            default=None,
            help="Defaults to both.",
        )

    def handle(self, *args, **options):
        queryset = Purchase.objects.select_related("business", "supplier").order_by("id")

        if options["business"]:
            queryset = queryset.filter(business_id=options["business"])
        if options["status"]:
            queryset = queryset.filter(status=options["status"])
        else:
            queryset = queryset.exclude(status=PurchaseStatus.DRAFT)
        if options["fy"]:
            start, end = fy_bounds(options["fy"])
            queryset = queryset.filter(bill_date__gte=start, bill_date__lte=end)

        findings = []
        checked = 0
        for purchase in queryset.iterator():
            checked += 1
            findings.extend(self._audit(purchase))

        findings.extend(self._audit_duplicates(queryset))

        if findings:
            for finding in findings:
                self.stderr.write(str(finding))
            self.stderr.write(
                self.style.ERROR(
                    f"\n{len(findings)} finding(s) across {checked} purchase bill(s)."
                )
            )
            raise SystemExit(1)

        self.stdout.write(
            self.style.SUCCESS(f"OK: {checked} purchase bill(s) agree with their inputs.")
        )

    # ------------------------------------------------------------------

    def _audit(self, purchase: Purchase) -> list:
        findings = []

        if not purchase.items.exists():
            return [Finding(purchase, "NO_LINES", "a posted bill with no lines")]

        # 1. Header against the entered lines. A mismatch here is a mistake in
        #    the document, not a fact about the supplier.
        entered = sum_entered(purchase)
        for field in ("taxable_total", "cgst_total", "sgst_total", "igst_total"):
            stored = getattr(purchase, field, ZERO) or ZERO
            summed = entered[field] or ZERO
            if stored != summed:
                findings.append(
                    Finding(
                        purchase,
                        "LINE_SUM_MISMATCH",
                        f"{field} is {stored} but the lines add up to {summed}",
                    )
                )

        # 2. Entered against the calculator.
        recalculate_draft(purchase, business=purchase.business)
        purchase.refresh_from_db()
        report = build_report(purchase, self._computed(purchase))

        if report.has_divergence:
            worst = max(report.material, key=lambda d: abs(d.difference))
            findings.append(
                Finding(
                    purchase,
                    "DIVERGENCE",
                    f"{len(report.material)} figure(s) differ from the calculator by "
                    f"more than {TOLERANCE}; largest is {worst.field} by "
                    f"Rs {abs(worst.difference)}",
                )
            )

        # 3. An unregistered or composition supplier's bill carries no tax.
        if not supplier_can_charge_tax(purchase) and purchase.tax_total > ZERO:
            findings.append(
                Finding(
                    purchase,
                    "TAX_ON_UNREGISTERED_BILL",
                    f"supplier has no GSTIN but the bill carries "
                    f"Rs {purchase.tax_total} of tax, which cannot be claimed",
                )
            )

        return findings

    def _computed(self, purchase: Purchase):
        """Re-run the calculator from stored inputs, without saving."""
        from apps.invoices.services.gst_calculator import LineInput, calculate_invoice

        lines = list(purchase.items.all())
        if not lines:
            return None
        inputs = [
            LineInput.build(
                quantity=line.quantity,
                unit_price=line.unit_price,
                tax_rate=line.tax_rate,
                line_discount=line.line_discount,
                item_type=line.item_type,
            )
            for line in lines
        ]
        return calculate_invoice(
            inputs,
            origin_state=purchase.origin_state_code or purchase.supplier_state_code or "",
            place_of_supply=purchase.place_of_supply or purchase.business.state_code or "",
            business_is_regular=supplier_can_charge_tax(purchase),
            prices_include_tax=purchase.prices_include_tax,
            round_invoice_total=False,
        )

    def _audit_duplicates(self, queryset) -> list:
        """
        Duplicate bill numbers per supplier per FY.

        Duplicates only - **not** gaps, unlike the invoice series check. These
        numbers belong to the supplier: a gap means nothing, and reporting one
        would train the user to ignore this command.
        """
        findings = []
        rows = (
            queryset.values("supplier_id", "bill_number", "financial_year")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
        )
        for row in rows:
            findings.append(
                Finding(
                    None,
                    "DUPLICATE_BILL_NUMBER",
                    f"supplier {row['supplier_id']} has {row['total']} bills numbered "
                    f"{row['bill_number']} in {row['financial_year']}",
                )
            )
        return findings