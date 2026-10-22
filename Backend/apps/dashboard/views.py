"""
Dashboard API views (3.3).

Read-only aggregations for the business dashboard:

* GET /api/v1/dashboard/stats/      Total sales, purchases, receivables,
                                    payables, estimated net profit.
* GET /api/v1/dashboard/low-stock/  Items at or below their low-stock threshold.

Design rules:

1. **Cash-basis, labeled "Estimated".** Net profit is sales − purchases −
   expenses. Without stock valuation / COGS (out of scope in 2.1.6) this is a
   cash-basis estimate and is labeled as such in the UI.
2. **Purchases module removed (2.2).** Total Purchases and Total Payables
   show 0 until purchases are restored (Phase 4 or a restore). The UI notes
   this so nobody mistakes ₹0 for "no spending".
3. **Low-stock uses `Item.objects.low_stock()`.** Services never appear.
4. **Tenant-scoped.** Every query filters by the caller's business.
"""

from decimal import Decimal

from django.db.models import DecimalField, Q, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services import get_business
from apps.core.viewsets import TenantQuerysetMixin
from apps.inventory.models import Item
from apps.invoices.models import Invoice


class _DashboardMixin(TenantQuerysetMixin):
    """Shared tenant-scoping helper for dashboard views."""

    def get_business(self):
        return get_business(self.request.user)


class DashboardStatsView(_DashboardMixin, APIView):
    """
    GET /api/v1/dashboard/stats/

    Aggregated business statistics:

    * total_sales         — grand_total of all ISSUED invoices (all time)
    * total_sales_month   — grand_total of ISSUED invoices this calendar month
    * total_purchases     — 0 until purchases are restored (2.2 removed)
    * total_receivables   — sum of balance_due on ISSUED invoices not PAID
    * total_payables      — 0 until purchases are restored (2.2 removed)
    * estimated_net_profit — total_sales − total_purchases (cash-basis, "Estimated")
    """

    def get(self, request):
        business = self.get_business()
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        invoices = Invoice.objects.filter(
            business=business,
            status=Invoice.Status.ISSUED,
        )

        total_sales = invoices.aggregate(s=Sum("grand_total", output_field=DecimalField()))[
            "s"
        ] or Decimal("0")

        total_sales_month = invoices.filter(invoice_date__gte=month_start).aggregate(
            s=Sum("grand_total", output_field=DecimalField())
        )["s"] or Decimal("0")

        # Receivables: balance_due on invoices that are not fully paid.
        total_receivables = invoices.filter(
            ~Q(payment_status=Invoice.PaymentStatus.PAID)
        ).aggregate(s=Sum("balance_due", output_field=DecimalField()))["s"] or Decimal("0")

        # Purchases module removed in 2.2 — placeholder until restored.
        total_purchases = Decimal("0")
        total_payables = Decimal("0")

        estimated_net_profit = total_sales - total_purchases

        return Response(
            {
                "total_sales": str(total_sales),
                "total_sales_this_month": str(total_sales_month),
                "total_purchases": str(total_purchases),
                "total_purchases_note": (
                    "Purchases module not yet active (2.2 removed). "
                    "Shows 0 until purchase bills are restored."
                ),
                "total_receivables": str(total_receivables),
                "total_payables": str(total_payables),
                "total_payables_note": (
                    "Purchases module not yet active (2.2 removed). "
                    "Shows 0 until purchase bills are restored."
                ),
                "estimated_net_profit": str(estimated_net_profit),
                "estimated_net_profit_note": (
                    "Cash-basis estimate, not true profit. "
                    "Ignores stock valuation / COGS. Label 'Estimated' in the UI."
                ),
            }
        )


class DashboardLowStockView(_DashboardMixin, APIView):
    """
    GET /api/v1/dashboard/low-stock/

    Items where current_stock <= low_stock_threshold.
    Uses Item.objects.low_stock() so services never appear.
    """

    def get(self, request):
        business = self.get_business()
        items = Item.objects.low_stock().filter(business=business)

        data = [
            {
                "id": item.id,
                "name": item.name,
                "item_code": item.item_code,
                "current_stock": str(item.current_stock)
                if item.current_stock is not None
                else None,
                "low_stock_threshold": (
                    str(item.low_stock_threshold) if item.low_stock_threshold is not None else None
                ),
                "unit": item.unit,
            }
            for item in items
        ]
        return Response(data)
