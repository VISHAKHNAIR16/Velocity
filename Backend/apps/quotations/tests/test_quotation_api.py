"""
Quotation API tests (4.3).

Covers: create quotation, compute GST, convert-to-invoice (atomic),
reject converted/expired, edit draft only, tenant isolation.
"""

from django.test import TestCase
from rest_framework import status

from apps.invoices.models import Invoice

from .base import (
    QUOTATION_LIST_URL,
    api_client_for,
    make_item,
    make_party,
    make_quotation,
    make_user,
)


def quotation_url(pk) -> str:
    return f"/api/v1/quotations/{pk}/"


class QuotationCreateTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)

    def test_create_quotation_computes_gst(self):
        payload = {
            "party": self.party.pk,
            "quotation_date": "2026-02-01",
            "valid_until": "2026-03-01",
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
        response = self.client_.post(QUOTATION_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

        data = response.json()
        self.assertEqual(data["status"], "DRAFT")
        self.assertEqual(data["grand_total"], "1180.00")
        self.assertEqual(data["cgst_total"], "90.00")
        self.assertEqual(data["sgst_total"], "90.00")

    def test_rejects_supplier_party(self):
        from apps.parties.models import Party

        supplier = Party.objects.create(
            business=self.user.business,
            name="Supplier Co",
            mobile="9999999999",
            state_code="36",
            party_type="SUPPLIER",
        )
        payload = {
            "party": supplier.pk,
            "quotation_date": "2026-02-01",
            "valid_until": "2026-03-01",
            "items": [
                {
                    "item": self.item.pk,
                    "quantity": "1.000",
                    "unit_price": "100.00",
                    "tax_rate": "18.00",
                }
            ],
        }
        response = self.client_.post(QUOTATION_LIST_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)

    def test_convert_to_invoice(self):
        quotation = make_quotation(self.user, self.party, self.item)
        response = self.client_.post(
            f"{quotation_url(quotation.pk)}convert-to-invoice/",
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)

        data = response.json()
        self.assertEqual(data["status"], "DRAFT")
        self.assertEqual(data["grand_total"], "1180.00")

        # Quotation marked converted
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, "CONVERTED")
        self.assertIsNotNone(quotation.converted_invoice)
        self.assertIsNotNone(quotation.converted_at)

        # Invoice exists and is DRAFT
        invoice = Invoice.objects.get(pk=data["id"])
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.party, self.party)

    def test_convert_already_converted_quotation(self):
        quotation = make_quotation(self.user, self.party, self.item, status="CONVERTED")
        response = self.client_.post(
            f"{quotation_url(quotation.pk)}convert-to-invoice/",
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.content)

    def test_edit_draft_only(self):
        quotation = make_quotation(self.user, self.party, self.item, status="SENT")
        response = self.client_.patch(
            quotation_url(quotation.pk),
            {"notes": "tampered"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)

    def test_list_tenant_scoped(self):
        make_quotation(self.user, self.party, self.item)

        other = make_user(email="other@example.com", state_code="29")
        other_party = make_party(other, "Other Customer", state_code="29")
        other_item = make_item(other, code="OTHER-001")
        make_quotation(other, other_party, other_item)

        response = self.client_.get(QUOTATION_LIST_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["count"], 1)
