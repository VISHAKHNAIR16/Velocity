"""
The `/preview/` drift guard (step C, task 1.4.3).

`/invoices/preview/` exists so the browser can show totals before saving. That is
only trustworthy if it produces *exactly* what the save produces. This module
sweeps the matrix the spec calls for - every GST slab, intra/inter, inclusive vs
exclusive pricing, line vs invoice discounts, round-off on/off - plus the
half-paisa inputs where JavaScript and `Decimal` famously disagree.

Nothing here re-derives the arithmetic (that is `test_gst_calculator.py`'s job).
It only ever compares preview against the persisted row.
"""

from decimal import Decimal

from django.test import TestCase

from apps.core.constants import GST_RATE_CHOICES
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

LINE_FIELDS = (
    "gross_amount",
    "line_discount",
    "net_amount",
    "taxable_value",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "total_amount",
)

class PreviewDriftMatrixTests(TestCase):
    """One user, many payloads, compared field by field against the database."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.party_same = make_party(cls.user, name="Local Buyer", state_code="36")
        cls.party_other = make_party(cls.user, name="Other State Buyer", state_code="29")
        cls.item = make_item(cls.user)
        cls.item_2 = make_item(
            cls.user, code="MUG", name="Steel Mug", hsn="732391", tax_rate="12.00"
        )
        cls.service = make_item(
            cls.user,
            code="FREIGHT",
            name="Road freight",
            item_type="SERVICE",
            hsn="996511",
            unit="NOS",
            price="80.00",
            tax_rate="18.00",
        )

    def setUp(self):
        self.client_ = api_client_for(self.user)

    def assert_no_drift(self, payload, label):
        saved = self.client_.post(INVOICE_LIST_URL, payload, format="json")
        self.assertEqual(saved.status_code, 201, f"{label}: save failed {saved.data}")
        saved_body = saved.json()

        previewed = self.client_.post(PREVIEW_URL, payload, format="json")
        self.assertEqual(previewed.status_code, 200, f"{label}: preview failed")
        preview_body = previewed.json()

        for field in TOTAL_FIELDS:
            self.assertEqual(
                preview_body["totals"][field],
                saved_body[field],
                f"{label}: invoice total {field} drifted",
            )

        self.assertEqual(
            len(preview_body["lines"]), len(saved_body["items"]), f"{label}: line count"
        )
        for index, (preview_line, saved_line) in enumerate(
            zip(preview_body["lines"], saved_body["items"], strict=True)
        ):
            for field in LINE_FIELDS:
                self.assertEqual(
                    preview_line[field],
                    saved_line[field],
                    f"{label}: line {index} {field} drifted",
                )

        self.assertEqual(
            preview_body["supply_type"], saved_body["supply_type"], f"{label}: supply_type"
        )
        self.assertEqual(
            preview_body["place_of_supply"],
            saved_body["place_of_supply"],
            f"{label}: place_of_supply",
        )
        return saved_body

    def test_every_gst_slab_intra_state(self):
        for rate, _label in GST_RATE_CHOICES:
            rate = str(rate)
            with self.subTest(rate=rate):
                self.assert_no_drift(
                    {
                        "party": self.party_same.pk,
                        "items": [line_payload(self.item, tax_rate=rate)],
                    },
                    f"intra slab {rate}",
                )

    def test_every_gst_slab_inter_state(self):
        for rate, _label in GST_RATE_CHOICES:
            rate = str(rate)
            with self.subTest(rate=rate):
                self.assert_no_drift(
                    {
                        "party": self.party_other.pk,
                        "items": [line_payload(self.item, tax_rate=rate)],
                    },
                    f"inter slab {rate}",
                )

    def test_inclusive_versus_exclusive_pricing(self):
        for inclusive in (False, True):
            for rate_decimal, _label in GST_RATE_CHOICES:
                rate = str(rate_decimal)
                with self.subTest(inclusive=inclusive, rate=rate):
                    self.assert_no_drift(
                        {
                            "party": self.party_same.pk,
                            "prices_include_tax": inclusive,
                            "items": [line_payload(self.item, tax_rate=rate)],
                        },
                        f"inclusive={inclusive} slab {rate}",
                    )

    def test_line_and_invoice_discount_combinations(self):
        """
        Discounts are the easiest place for preview and save to drift.

        Pairs are chosen so the discount never exceeds the post-line-discount
        amount; exceeding it is a separate, separately tested validation.
        """
        combinations = [
            ("0.00", "0.00"),
            ("0.00", "100.00"),
            ("50.00", "100.00"),
            ("0.01", "0.01"),
            ("249.50", "0.00"),
            ("249.50", "340.25"),
        ]
        for line_discount, invoice_discount in combinations:
            with self.subTest(line=line_discount, invoice=invoice_discount):
                self.assert_no_drift(
                    {
                        "party": self.party_same.pk,
                        "invoice_discount": invoice_discount,
                        "items": [
                            line_payload(self.item, line_discount=line_discount),
                            line_payload(self.item_2, line_discount=line_discount),
                        ],
                    },
                    f"line {line_discount} invoice {invoice_discount}",
                )

    def test_mixed_product_and_service_lines(self):
        self.assert_no_drift(
            {
                "party": self.party_other.pk,
                "items": [
                    line_payload(self.item, quantity="3", unit_price="249.50"),
                    line_payload(self.item_2, quantity="1.750", unit_price="340.25"),
                    line_payload(self.service, quantity="1", unit_price="80.00"),
                ],
            },
            "mixed lines",
        )

    def test_round_off_on_and_off(self):
        for round_invoice_total in (False, True):
            self.user.business.round_invoice_total = round_invoice_total
            self.user.business.save(update_fields=["round_invoice_total"])
            with self.subTest(round_invoice_total=round_invoice_total):
                self.assert_no_drift(
                    {
                        "party": self.party_same.pk,
                        "items": [
                            line_payload(self.item, quantity="3", unit_price="249.50"),
                            line_payload(self.service, quantity="1", unit_price="80.00"),
                        ],
                    },
                    f"round_off={round_invoice_total}",
                )

    def test_half_paisa_boundaries(self):
        """
        Money rounds HALF_UP, so a value landing exactly on .xx5 must go up.

        Prices are 2dp and quantity is 3dp, so the boundary is reached through
        quantity x price (and then again through tax on the result) - not
        through a 3dp price, which the API correctly refuses.
        """
        cases = [
            # (quantity, unit_price, expected gross after ROUND_HALF_UP)
            ("0.125", "1.00", "0.13"),  # 0.125 -> 0.13 (half-even would say 0.12)
            ("1.005", "2.50", "2.51"),  # 2.5125 -> 2.51
            ("2.005", "1.25", "2.51"),  # 2.506250 -> 2.51
            ("3.000", "2.50", "7.50"),
        ]
        for quantity, unit_price, expected_gross in cases:
            with self.subTest(quantity=quantity, unit_price=unit_price):
                body = self.assert_no_drift(
                    {
                        "party": self.party_same.pk,
                        "items": [
                            line_payload(self.item, quantity=quantity, unit_price=unit_price)
                        ],
                    },
                    f"boundary {quantity} x {unit_price}",
                )
                self.assertEqual(body["items"][0]["gross_amount"], expected_gross)

    def test_a_three_decimal_price_is_rejected(self):
        """The boundary must be reached via quantity, not a 3dp price."""
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party_same.pk,
                "items": [line_payload(self.item, quantity="1", unit_price="2.675")],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("unit_price", str(response.json()["errors"]))

    def test_totals_are_conserved_not_merely_agreed(self):
        """
        A shared bug in both paths would still "agree". Check the arithmetic
        identities so the matrix cannot pass on a consistently wrong answer.
        """
        body = self.assert_no_drift(
            {
                "party": self.party_other.pk,
                "invoice_discount": "37.77",
                "items": [
                    line_payload(self.item, quantity="3", unit_price="249.50"),
                    line_payload(self.item_2, quantity="1.750", unit_price="340.25"),
                    line_payload(self.service, quantity="1", unit_price="80.00"),
                ],
            },
            "conservation",
        )
        invoice = Invoice.objects.get(pk=body["id"])
        lines = list(invoice.items.all())

        self.assertEqual(
            sum(line.invoice_discount_share for line in lines), invoice.total_discount
        )
        self.assertEqual(
            sum(line.total_amount for line in lines), invoice.grand_total
        )
        tax_total = invoice.cgst_total + invoice.sgst_total + invoice.igst_total
        self.assertEqual(tax_total, invoice.grand_total - invoice.taxable_total)
        # Intra or inter, never a mix on one invoice.
        self.assertEqual(invoice.cgst_total == 0, invoice.sgst_total == 0)


class ClientSuppliedTotalsAreIgnoredTests(TestCase):
    """The request body carries inputs only - verified through the real API."""

    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)

    def test_every_money_field_is_read_only(self):
        """Posting every total at once must change nothing."""
        honest = {
            "party": self.party.pk,
            "items": [line_payload(self.item)],
        }
        expected = self.client_.post(INVOICE_LIST_URL, honest, format="json").json()

        tampered = {
            **honest,
            "subtotal": "-1.00",
            "total_discount": "999.99",
            "taxable_total": "1.00",
            "cgst_total": "888.88",
            "sgst_total": "777.77",
            "igst_total": "666.66",
            "round_off": "5.55",
            "grand_total": "0.01",
            "items": [
                {**line_payload(self.item), "taxable_value": "0.02", "total_amount": "0.03",
                 "cgst_amount": "0.04", "sgst_amount": "0.05", "igst_amount": "0.06"}
            ],
        }
        actual = self.client_.post(INVOICE_LIST_URL, tampered, format="json").json()

        for field in TOTAL_FIELDS:
            self.assertEqual(
                actual[field], expected[field], f"{field} was taken from the client"
            )
        for field in LINE_FIELDS:
            self.assertEqual(
                actual["items"][0][field],
                expected["items"][0][field],
                f"line {field} was taken from the client",
            )

    def test_totals_recomputed_when_only_the_header_changes(self):
        """PATCH the discount and watch every stored figure move with it."""
        created = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()

        updated = self.client_.patch(
            f"/api/v1/invoices/{created['id']}/",
            {"invoice_discount": "100.00"},
            format="json",
        ).json()

        self.assertEqual(updated["total_discount"], "100.00")
        self.assertEqual(updated["taxable_total"], "399.00")
        invoice = Invoice.objects.get(pk=created["id"])
        self.assertEqual(
            sum(line.invoice_discount_share for line in invoice.items.all()),
            Decimal("100.00"),
        )
