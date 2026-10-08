"""Draft invoice CRUD over the public API (step C)."""

from decimal import Decimal

from django.test import TestCase

from apps.inventory.models import Item
from apps.invoices.models import Invoice, InvoiceItem
from apps.invoices.services.draft import hsn_summary, summarize_hsn

from .base import (
    INVOICE_LIST_URL,
    api_client_for,
    copy_url,
    detail_url,
    line_payload,
    make_item,
    make_party,
    make_user,
)


class DraftCreateTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)

    def test_create_returns_201_with_server_computed_totals(self):
        response = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()

        self.assertEqual(body["status"], Invoice.Status.DRAFT)
        # 2 x 249.50 = 499.00, +5% split half-up => cgst 12.47 / sgst 12.48
        self.assertEqual(body["subtotal"], "499.00")
        self.assertEqual(body["taxable_total"], "499.00")
        self.assertEqual(body["cgst_total"], "12.47")
        self.assertEqual(body["sgst_total"], "12.48")
        self.assertEqual(body["igst_total"], "0.00")
        self.assertEqual(body["grand_total"], "523.95")
        self.assertEqual(len(body["items"]), 1)

    def test_draft_has_no_invoice_number_yet(self):
        """Numbering is allocated on issue (step D), not on save."""
        response = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        )
        invoice = Invoice.objects.get(pk=response.json()["id"])
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        # No number is allocated until the document is issued in step D.
        self.assertFalse(invoice.invoice_number)
        self.assertEqual(invoice.display_number, "Draft")

    def test_quantity_keeps_three_decimals(self):
        """A quantity is not money: 1.5 kg must not be rounded to 2dp."""
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [line_payload(self.item, quantity="1.500", unit_price="100.00")],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        line = InvoiceItem.objects.get(invoice_id=response.json()["id"])
        self.assertEqual(line.quantity, Decimal("1.500"))

    def test_line_snapshot_copies_the_catalogue(self):
        self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        )
        line = InvoiceItem.objects.get()
        self.assertEqual(line.item_id, self.item.pk)
        self.assertEqual(line.item_name, "Cotton Shirt")
        self.assertEqual(line.item_type, Item.ItemType.PRODUCT)
        self.assertEqual(line.hsn_sac_code, "610510")
        self.assertEqual(line.unit, "PCS")

    def test_client_cannot_forge_snapshot_for_an_item_backed_line(self):
        """Posting a different name must not change what the invoice prints."""
        self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [line_payload(self.item, item_name="Fake Name", hsn_sac_code="999999")],
            },
            format="json",
        )
        line = InvoiceItem.objects.get()
        self.assertEqual(line.item_name, "Cotton Shirt")
        self.assertEqual(line.hsn_sac_code, "610510")

    def test_free_text_line_is_allowed_and_described(self):
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [
                    {
                        "item_name": "Packing charges",
                        "item_type": Item.ItemType.SERVICE,
                        "hsn_sac_code": "996511",
                        "unit": "NOS",
                        "service_description": "Carton packing",
                        "quantity": "1",
                        "unit_price": "50.00",
                        "tax_rate": "18.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        line = InvoiceItem.objects.get()
        self.assertIsNone(line.item_id)
        self.assertEqual(line.item_name, "Packing charges")
        self.assertEqual(line.service_description, "Carton packing")
        self.assertEqual(line.cgst_amount, Decimal("4.50"))
        self.assertEqual(line.sgst_amount, Decimal("4.50"))

    def test_free_text_line_missing_required_fields_is_400_not_500(self):
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [{"item_name": "Mystery", "quantity": "1", "unit_price": "10.00"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("items", response.json()["errors"])
        self.assertEqual(Invoice.objects.count(), 0)

    def test_service_without_description_is_rejected(self):
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [
                    {
                        "item_name": "Consulting",
                        "item_type": Item.ItemType.SERVICE,
                        "hsn_sac_code": "998311",
                        "unit": "HRS",
                        "quantity": "1",
                        "unit_price": "100.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_hsn_is_rejected(self):
        """5-digit HSN is not used by GST; the calculator must refuse it."""
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [
                    {
                        "item_name": "Odd goods",
                        "item_type": Item.ItemType.PRODUCT,
                        "hsn_sac_code": "12345",
                        "unit": "PCS",
                        "quantity": "1",
                        "unit_price": "10.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_client_supplied_totals_are_ignored(self):
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "grand_total": "1.00",
                "taxable_total": "0.01",
                "cgst_total": "999.00",
                "subtotal": "-500.00",
                "items": [line_payload(self.item)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["grand_total"], "523.95")
        self.assertEqual(body["cgst_total"], "12.47")
        self.assertEqual(body["subtotal"], "499.00")

    def test_invoice_level_discount_is_apportioned_exactly(self):
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "invoice_discount": "49.90",
                "items": [line_payload(self.item, quantity="1", unit_price="249.50")],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        invoice = Invoice.objects.get(pk=response.json()["id"])
        self.assertEqual(invoice.total_discount, Decimal("49.90"))
        self.assertEqual(
            sum(line.invoice_discount_share for line in invoice.items.all()),
            Decimal("49.90"),
        )

    def test_due_date_before_invoice_date_is_400(self):
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "invoice_date": "2026-04-10",
                "due_date": "2026-04-01",
                "items": [line_payload(self.item)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class DraftReadUpdateDeleteTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)
        self.created = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()

    def test_list_returns_only_own_invoices(self):
        response = self.client_.get(INVOICE_LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)

    def test_detail_returns_the_draft_with_lines(self):
        response = self.client_.get(detail_url(self.created["id"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 1)

    def test_update_replaces_lines_and_recomputes(self):
        response = self.client_.put(
            detail_url(self.created["id"]),
            {
                "party": self.party.pk,
                "items": [line_payload(self.item, quantity="3", unit_price="249.50")],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["subtotal"], "748.50")
        self.assertEqual(InvoiceItem.objects.count(), 1)

    def test_partial_update_without_items_keeps_lines(self):
        response = self.client_.patch(
            detail_url(self.created["id"]), {"notes": "Deliver before Friday"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["notes"], "Deliver before Friday")
        self.assertEqual(response.json()["grand_total"], "523.95")
        self.assertEqual(InvoiceItem.objects.count(), 1)

    def test_delete_removes_the_draft(self):
        response = self.client_.delete(detail_url(self.created["id"]))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Invoice.objects.filter(pk=self.created["id"]).exists())

    def test_copy_creates_a_new_draft(self):
        response = self.client_.post(copy_url(self.created["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["grand_total"], "523.95")
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertNotEqual(response.json()["id"], self.created["id"])


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.other = make_user("rival@example.com", state_code="29", prefix="RIV")
        self.client_ = api_client_for(self.owner)
        self.party = make_party(self.owner)
        self.item = make_item(self.owner)
        self.invoice = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()

    def test_other_tenant_cannot_see_the_invoice(self):
        response = api_client_for(self.other).get(detail_url(self.invoice["id"]))
        self.assertEqual(response.status_code, 404)

    def test_other_tenant_cannot_use_my_party(self):
        response = api_client_for(self.other).post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_other_tenant_cannot_use_my_item(self):
        response = api_client_for(self.other).post(
            INVOICE_LIST_URL,
            {
                "party": make_party(self.other, name="Their Party").pk,
                "items": [line_payload(self.item)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_other_tenant_cannot_edit_or_delete(self):
        other = api_client_for(self.other)
        self.assertEqual(
            other.patch(detail_url(self.invoice["id"]), {"notes": "hacked"}, format="json").status_code,
            404,
        )
        self.assertEqual(other.delete(detail_url(self.invoice["id"])).status_code, 404)

    def test_list_is_scoped_to_my_business(self):
        self.assertEqual(api_client_for(self.other).get(INVOICE_LIST_URL).json()["count"], 0)


class HsnSummaryTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.shirt = make_item(self.user, code="SHIRT", hsn="610510", tax_rate="5.00")
        self.trouser = make_item(self.user, code="TROUSER", hsn="620343", tax_rate="12.00")
        self.shipping = make_item(
            self.user,
            code="SHIP",
            name="Transport",
            item_type=Item.ItemType.SERVICE,
            hsn="996511",
            unit="NOS",
            price="50.00",
            tax_rate="18.00",
        )

    def test_same_hsn_is_bucketed_and_totals_are_conserved(self):
        self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [
                    line_payload(self.shirt, quantity="1", unit_price="249.50"),
                    line_payload(self.shirt, quantity="2", unit_price="249.50"),
                    line_payload(self.shipping, quantity="1", unit_price="50.00"),
                ],
            },
            format="json",
        )
        invoice = Invoice.objects.get()
        summary = hsn_summary(invoice)

        self.assertEqual([row["hsn_sac_code"] for row in summary], ["610510", "996511"])

        shirt_row = summary[0]
        self.assertEqual(shirt_row["quantity"], Decimal("3.000"))
        self.assertEqual(shirt_row["taxable_value"], Decimal("748.50"))

        # Nothing may be invented or lost by bucketing.
        self.assertEqual(
            sum(row["taxable_value"] for row in summary), invoice.taxable_total
        )
        self.assertEqual(sum(row["total"] for row in summary), invoice.grand_total)

    def test_missing_hsn_is_reported_as_a_dash_bucket(self):
        """
        The API requires an HSN on every line, so this guards the pure function
        against rows written by other paths (imports, future migrations).
        """
        summary = summarize_hsn(
            [
                {
                    "hsn": None,
                    "description": "Unclassified goods",
                    "quantity": Decimal("2.000"),
                    "taxable": Decimal("100.00"),
                    "cgst": Decimal("0.00"),
                    "sgst": Decimal("0.00"),
                    "igst": Decimal("0.00"),
                    "total": Decimal("100.00"),
                }
            ]
        )
        self.assertEqual(summary[0]["hsn_sac_code"], "—")
        self.assertEqual(summary[0]["quantity"], Decimal("2.000"))
        self.assertEqual(summary[0]["total"], Decimal("100.00"))

    def test_hsn_codes_come_back_in_numeric_order(self):
        for code in ("996511", "610510", "620343"):
            make_item(self.user, code=f"X-{code}", hsn=code)
        self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [
                    {"item": i.pk, "quantity": "1", "unit_price": "10.00"}
                    for i in Item.objects.filter(business=self.user.business)
                ],
            },
            format="json",
        )
        summary = hsn_summary(Invoice.objects.get())
        self.assertEqual(
            [row["hsn_sac_code"] for row in summary], ["610510", "620343", "996511"]
        )


class FrozenDocumentTests(TestCase):
    """A draft is editable; an issued one is not (guarded here in step C)."""

    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)
        self.invoice = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()

    def test_issued_invoice_cannot_be_edited_or_deleted(self):
        record = Invoice.objects.get(pk=self.invoice["id"])
        record.status = Invoice.Status.ISSUED
        record.issued_at = "2026-04-01T10:00:00Z"
        record.save(update_fields=["status", "issued_at"])

        patch = self.client_.patch(
            detail_url(self.invoice["id"]), {"notes": "changed"}, format="json"
        )
        self.assertEqual(patch.status_code, 400)

        delete = self.client_.delete(detail_url(self.invoice["id"]))
        self.assertEqual(delete.status_code, 400)
        self.assertTrue(Invoice.objects.filter(pk=self.invoice["id"]).exists())

    def test_place_of_supply_override_is_preserved(self):
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "place_of_supply": "29",
                "items": [line_payload(self.item)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        invoice = Invoice.objects.get(pk=response.json()["id"])
        self.assertEqual(invoice.place_of_supply, "29")
