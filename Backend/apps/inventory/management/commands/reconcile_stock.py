"""
`reconcile_stock` - prove the cached stock balance still equals the ledger.

The invariant this defends:

    Item.current_stock == SUM(StockMovement.quantity_change)   [tracked items]
    Item.current_stock IS NULL                                  [untracked]

`current_stock` is a cache (decision 28). A cache that can silently disagree with
its source of truth is worse than no cache, because the number still looks
authoritative. This command is how we find out.

`--fix` never edits the cache silently: it writes an ADJUSTMENT with reason
RECONCILIATION, so the repair is itself part of the explainable history. Silently
patching a number would destroy the very thing the ledger exists to preserve.

Exit code 1 on any drift, so cron and CI can alert.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandParser

from apps.inventory.services.stock import reconcile


class Command(BaseCommand):
    help = "Check that each item's cached stock equals the sum of its ledger movements."

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--business", type=int, help="Only this business id.")
        parser.add_argument(
            "--fix",
            action="store_true",
            help=(
                "Repair drift by writing a RECONCILIATION adjustment, so the "
                "change is recorded rather than silently applied."
            ),
        )

    def handle(self, *args, **options):
        from apps.accounts.models import BusinessProfile

        businesses = BusinessProfile.objects.all()
        if options["business"]:
            businesses = businesses.filter(pk=options["business"])

        total_drift = 0
        for business in businesses.order_by("pk"):
            drift = reconcile(business, fix=options["fix"])
            if not drift:
                continue

            self.stdout.write(f"\n{business.trade_name}:")
            for row in drift:
                suffix = "  (fixed)" if row.get("fixed") else ""
                self.stderr.write(
                    f"  item {row['item_id']} {row['item']}: cached {row['cached']}, "
                    f"ledger {row['ledger']}, difference {row['difference']}{suffix}"
                )
            total_drift += len(drift)

        if total_drift:
            self.stderr.write(
                self.style.ERROR(f"\n{total_drift} item(s) out of sync with the ledger.")
            )
            if not options["fix"]:
                self.stderr.write("Run again with --fix to record a reconciling adjustment.")
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("OK: every cached balance matches the ledger."))