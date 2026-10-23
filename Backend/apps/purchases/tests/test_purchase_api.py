"""
Purchase API tests (4.1).

Covers: create purchase bill, validate supplier party, compute GST totals,
record stock movements, edit/delete draft only, purchase returns, tenant isolation.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework import status

from .base import (
    PURCHASE_LIST_URL,
    api_client_for,
    make_issued_purchase,
    make_item,
    make_party,
    make_user,
)

PURCHASE_RETURNS_URL = "/api/v1/purchases/returns/"


def purchase_url(pk) -> str:
    return f"/api/v1/purchases/{pk}/"


class PurchaseCreateTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.supplier = make_party(self.user, "Supplier Co", party_type="SUPPLIER")
        self.item = make_item(self.user)

    def test_create_purchase_computes_gst(self):
        payload = {
            "party": self.supplier.pk,
            "purchase_date": "2026-02-01",
            "bill_number": "BILL-001",
            "place_of_supply": "36",
            "supply_type": "INTRA",
            "items": [
                {
                    "item": self.item.pk,
                    "quantity": "10.000",
                    "unit_price": "100.00",
                    "tax_rate": "18.00",
                }
            ],
        }
        response = self.client_.post(PURCHASE_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

        data = response.json()
        self.assertEqual(data["status"], "ISSUED")
        self.assertEqual(data["grand_total"], "1180.00")
        self.assertEqual(data["cgst_total"], "90.00")
        self.assertEqual(data["sgst_total"], "90.00")
        self.assertEqual(data["igst_total"], "0.00")

    def test_create_purchase_inter_state(self):
        payload = {
            "party": self.supplier.pk,
            "purchase_date": "2026-02-01",
            "bill_number": "BILL-002",
            "place_of_supply": "29",
            "supply_type": "INTER",
            "items": [
                {
                    "item": self.item.pk,
                    "quantity": "5.000",
                    "unit_price": "200.00",
                    "tax_rate": "18.00",
                }
            ],
        }
        response = self.client_.post(PURCHASE_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

        data = response.json()
        self.assertEqual(data["cgst_total"], "0.00")
        self.assertEqual(data["sgst_total"], "0.00")
        self.assertEqual(data["igst_total"], "180.00")
        self.assertEqual(data["grand_total"], "1180.00")

    def test_rejects_customer_party(self):
        customer = make_party(self.user, "Retail Customer", party_type="CUSTOMER")
        payload = {
            "party": customer.pk,
            "purchase_date": "2026-02-01",
            "items": [
                {
                    "item": self.item.pk,
                    "quantity": "1.000",
                    "unit_price": "100.00",
                    "tax_rate": "18.00",
                }
            ],
        }
        response = self.client_.post(PURCHASE_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertEqual(response.json()["error"], "PARTY_NOT_SUPPLIER")

    def test_rejects_zero_quantity(self):
        payload = {
            "party": self.supplier.pk,
            "purchase_date": "2026-02-01",
            "items": [
                {
                    "item": self.item.pk,
                    "quantity": "0.000",
                    "unit_price": "100.00",
                    "tax_rate": "18.00",
                }
            ],
        }
        response = self.client_.post(PURCHASE_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)

    def test_edit_draft_only(self):
        # Purchases are issued immediately, so there's nothing to edit as draft.
        # Create one, then try to update - should succeed only if DRAFT.
        purchase = make_issued_purchase(self.user, self.supplier, self.item)
        response = self.client_.patch(
            purchase_url(purchase.pk),
            {"notes": "tampered"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)

    def test_list_tenant_scoped(self):
        self.client_.post(
            PURCHASE_LIST_URL,
            {
                "party": self.supplier.pk,
                "purchase_date": "2026-02-01",
                "bill_number": "BILL-003",
                "place_of_supply": "36",
                "supply_type": "INTRA",
                "items": [
                    {
                        "item": self.item.pk,
                        "quantity": "1.000",
                        "unit_price": "100.00",
                        "tax_rate": "18.00",
                    }
                ],
            },
            format="json",
        )

        other = make_user(email="other@example.com", state_code="29")
        other_client = api_client_for(other)
        other_supplier = make_party(other, "Other Supplier", state_code="29", party_type="SUPPLIER")
        other_item = make_item(other, code="OTHER-001")
        other_client.post(
            PURCHASE_LIST_URL,
            {
                "party": other_supplier.pk,
                "purchase_date": "2026-02-01",
                "bill_number": "OTHER-001",
                "items": [
                    {
                        "item": other_item.pk,
                        "quantity": "1.000",
                        "unit_price": "100.00",
                        "tax_rate": "18.00",
                    }
                ],
            },
            format="json",
        )

        response = self.client_.get(PURCHASE_LIST_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["count"], 1)

    def test_stock_movement_recorded(self):
        """A purchase bill adds stock via PURCHASE movement."""
        from apps.inventory.models_stock import MovementType, SourceType, StockMovement

        self.client_.post(
            PURCHASE_LIST_URL,
            {
                "party": self.supplier.pk,
                "purchase_date": "2026-02-01",
                "bill_number": "BILL-STOCK",
                "place_of_supply": "36",
                "supply_type": "INTRA",
                "items": [
                    {
                        "item": self.item.pk,
                        "quantity": "10.000",
                        "unit_price": "100.00",
                        "tax_rate": "18.00",
                    }
                ],
            },
            format="json",
        )

        movements = StockMovement.objects.filter(
            business=self.user.business,
            source_type=SourceType.PURCHASE,
            movement_type=MovementType.PURCHASE,
        )
        self.assertEqual(movements.count(), 1)
        self.assertEqual(movements.first().quantity_change, Decimal("10.000"))
