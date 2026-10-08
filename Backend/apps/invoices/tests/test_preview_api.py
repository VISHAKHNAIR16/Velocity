"""
`POST /api/v1/invoices/preview/` (step C).

The preview's whole reason to exist is that it must predict the saved document
exactly. These tests pin that contract rather than re-deriving the arithmetic,
which `test_gst_calculator.py` already covers.
"""

from decimal import Decimal

from django.test import TestCase

from apps.inventory.models import Item
from apps.invoices.models import Invoice

from .base import (
    INVOICE_LIST_URL,
    PREVIEW_URL,
    api_client_for,
    line_payload,
    make_item,
    make_party,
    make_user,
)

TOTAL_FIELDS = (
    "subtotal",
    "total_discount",
    "taxable_total",
    "cgst_total",
    "sgst_total",
    "igst_total",
    "round_off",
    "grand_total",
)


class PreviewEndpointTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)

    def preview(self, **overrides):
        body = {"party": self.party.pk, "items": [line_payload(self.item)]}
        body.update(overrides)
        return self.client_.post(PREVIEW_URL, body, format="json")

    def test_preview_saves_nothing(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_preview_totals_match_a_saved_invoice_exactly(self):
        payload = {
            "party": self.party.pk,
            "invoice_discount": "20.00",
            "items": [
                line_payload(self.item, quantity="3", unit_price="249.50"),
                {
                    "item_name": "Freight",
                    "item_type": Item.ItemType.SERVICE,
                    "hsn_sac_code": "996511",
                    "unit": "NOS",
                    "service_description": "Road freight",
                    "quantity": "1",
                    "unit_price": "80.00",
                    "tax_rate": "18.00",
                },
            ],
        }
        saved = self.client_.post(INVOICE_LIST_URL, payload, format="json").json()
        previewed = self.client_.post(PREVIEW_URL, payload, format="json").json()

        for field in TOTAL_FIELDS:
            self.assertEqual(
                previewed["totals"][field],
                saved[field],
                f"{field} drifted between preview and save",
            )

    def test_preview_line_figures_match_saved_lines(self):
        payload = {"party": self.party.pk, "items": [line_payload(self.item)]}
        saved = self.client_.post(INVOICE_LIST_URL, payload, format="json").json()
        previewed = self.client_.post(PREVIEW_URL, payload, format="json").json()

        for saved_line, preview_line in zip(saved["items"], previewed["lines"], strict=True):
            for field in (
                "gross_amount",
                "line_discount",
                "net_amount",
                "taxable_value",
                "cgst_amount",
                "sgst_amount",
                "igst_amount",
                "total_amount",
            ):
                self.assertEqual(
                    preview_line[field],
                    saved_line[field],
                    f"line {field} drifted between preview and save",
                )

    def test_preview_reports_supply_type_and_label(self):
        body = self.preview().json()
        self.assertEqual(body["supply_type"], "INTRA")
        self.assertEqual(body["state_tax_label"], "SGST")
        self.assertEqual(body["place_of_supply"], "36")

    def test_preview_switches_to_igst_for_another_state(self):
        other_state = make_party(self.user, name="Karnataka Buyer", state_code="29")
        body = self.preview(party=other_state.pk).json()
        self.assertEqual(body["supply_type"], "INTER")
        self.assertEqual(body["state_tax_label"], "IGST")
        self.assertEqual(body["place_of_supply"], "29")
        self.assertEqual(body["totals"]["cgst_total"], "0.00")
        self.assertEqual(body["totals"]["sgst_total"], "0.00")

    def test_preview_honours_an_explicit_place_of_supply(self):
        body = self.preview(place_of_supply="29").json()
        self.assertEqual(body["place_of_supply"], "29")
        self.assertEqual(body["supply_type"], "INTER")

    def test_preview_hsn_summary_buckets_the_same_way(self):
        second = make_item(self.user, code="SHIRT-2", hsn="610510", tax_rate="5.00")
        body = self.preview(
            items=[
                line_payload(self.item, quantity="1", unit_price="249.50"),
                line_payload(second, quantity="2", unit_price="249.50"),
            ]
        ).json()

        self.assertEqual(len(body["hsn_summary"]), 1)
        row = body["hsn_summary"][0]
        self.assertEqual(row["hsn_sac_code"], "610510")
        self.assertEqual(row["quantity"], "3.000")
        self.assertEqual(row["taxable_value"], body["totals"]["taxable_total"])
        self.assertEqual(row["total"], body["totals"]["grand_total"])

    def test_preview_cannot_be_used_to_post_a_total(self):
        """The preview response is informational; posting it back must not stick."""
        previewed = self.preview().json()
        response = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)], **previewed["totals"]},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["grand_total"], "523.95")
        self.assertNotEqual(response.json()["grand_total"], "0.01")

    def test_preview_rejects_an_empty_invoice(self):
        response = self.client_.post(PREVIEW_URL, {"party": self.party.pk, "items": []}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_preview_rejects_another_tenants_party(self):
        rival = make_user("rival@example.com", state_code="29")
        response = api_client_for(rival).post(
            PREVIEW_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_inclusive_pricing_is_honoured(self):
        """100.00 inclusive of 18% => 84.75 taxable + 15.25 tax."""
        body = self.preview(
            prices_include_tax=True,
            items=[line_payload(self.item, quantity="1", unit_price="100.00", tax_rate="18.00")],
        ).json()
        self.assertEqual(body["totals"]["taxable_total"], "84.75")
        self.assertEqual(body["totals"]["grand_total"], "100.00")

    def test_totals_are_strings_not_floats(self):
        """Decimals must not become floats anywhere on the wire."""
        body = self.preview().json()
        for field in TOTAL_FIELDS:
            self.assertIsInstance(body["totals"][field], str)
        for field in ("taxable_value", "total_amount"):
            self.assertIsInstance(body["lines"][0][field], str)
        self.assertEqual(Decimal(body["totals"]["grand_total"]), Decimal("523.95"))
