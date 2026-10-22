"""
Dashboard API tests (3.3).

Covers: /api/v1/dashboard/stats/ (sales, purchases, receivables, payables,
estimated net profit) and /api/v1/dashboard/low-stock/ (items at/below
threshold, services excluded).
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework import status

from apps.inventory.models import Item
from apps.invoices.models import Invoice
from apps.payments.tests.base import (
    api_client_for,
    make_issued_invoice,
    make_party,
    make_user,
)


class DashboardStatsTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user, "Sharma Stores")

        # Issued invoices: one paid, one partially paid, one unpaid
        self.inv_paid = make_issued_invoice(self.user, self.party, "1000.00")
        self.inv_paid.paid_amount = Decimal("1000.00")
        self.inv_paid.balance_due = Decimal("0.00")
        self.inv_paid.payment_status = Invoice.PaymentStatus.PAID
        self.inv_paid.save()

        self.inv_partial = make_issued_invoice(self.user, self.party, "500.00")
        self.inv_partial.paid_amount = Decimal("200.00")
        self.inv_partial.balance_due = Decimal("300.00")
        self.inv_partial.payment_status = Invoice.PaymentStatus.PARTIAL
        self.inv_partial.save()

        self.inv_unpaid = make_issued_invoice(self.user, self.party, "300.00")
        # stays UNPAID, balance_due 300

    def test_stats_returns_aggregates(self):
        response = self.client_.get("/api/v1/dashboard/stats/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        data = response.json()

        self.assertEqual(data["total_sales"], "1800.00")
        self.assertEqual(data["total_receivables"], "600.00")  # 300 + 300
        self.assertEqual(data["total_purchases"], "0")  # purchases module removed
        self.assertEqual(data["total_payables"], "0")  # purchases module removed
        self.assertEqual(data["estimated_net_profit"], "1800.00")
        self.assertIn("not yet active", data["total_purchases_note"])
        self.assertIn("Estimated", data["estimated_net_profit_note"])

    def test_stats_tenant_isolation(self):
        """Another business's invoices don't affect this business's stats."""
        other_user = make_user(email="other@example.com", state_code="29")
        other_party = make_party(other_user, "Other", state_code="29")
        make_issued_invoice(other_user, other_party, "99999.00")

        response = self.client_.get("/api/v1/dashboard/stats/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["total_sales"], "1800.00")


class DashboardLowStockTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)

        # Low-stock product (at threshold)
        self.low_item = Item.objects.create(
            business=self.user.business,
            item_code="LOW-001",
            name="Low Stock Item",
            item_type=Item.ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=Decimal("100.00"),
            tax_rate=Decimal("5.00"),
            current_stock=Decimal("3.000"),
            low_stock_threshold=Decimal("5.000"),
            track_stock=True,
        )

        # Well-stocked product
        self.ok_item = Item.objects.create(
            business=self.user.business,
            item_code="OK-001",
            name="OK Item",
            item_type=Item.ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=Decimal("100.00"),
            tax_rate=Decimal("5.00"),
            current_stock=Decimal("50.000"),
            low_stock_threshold=Decimal("5.000"),
            track_stock=True,
        )

        # Service (never appears in low-stock)
        self.service = Item.objects.create(
            business=self.user.business,
            item_code="SVC-001",
            name="Consulting",
            item_type=Item.ItemType.SERVICE,
            unit="HRS",
            hsn_sac_code="998311",
            sales_price=Decimal("1000.00"),
            tax_rate=Decimal("18.00"),
            track_stock=False,
        )

    def test_low_stock_returns_only_below_threshold(self):
        response = self.client_.get("/api/v1/dashboard/low-stock/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        data = response.json()

        ids = {item["id"] for item in data}
        self.assertIn(self.low_item.pk, ids)
        self.assertNotIn(self.ok_item.pk, ids)
        self.assertNotIn(self.service.pk, ids)

    def test_low_stock_tenant_isolation(self):
        other = make_user(email="other@example.com", state_code="29")
        Item.objects.create(
            business=other.business,
            item_code="OTHER-001",
            name="Other Low",
            item_type=Item.ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=Decimal("100.00"),
            tax_rate=Decimal("5.00"),
            current_stock=Decimal("1.000"),
            low_stock_threshold=Decimal("5.000"),
            track_stock=True,
        )

        response = self.client_.get("/api/v1/dashboard/low-stock/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], self.low_item.pk)
