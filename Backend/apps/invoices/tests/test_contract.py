"""
Frontend/backend contract, exercised over **real HTTP** (step 2.0.1).

Why this file exists
--------------------
The billing screen reads specific field names off `/invoices/preview/`. Every
other test in this suite uses DRF's in-process client, so a renamed or dropped
field can pass the whole suite and still leave a customer staring at an empty
totals panel. This module boots a real HTTP server via `LiveServerTestCase` and
speaks to it over a socket with `urllib`, exactly as the browser does.

It also covers the 1.4.5 acceptance list that the plan called "browser smoke
test": walk-in sale, inter-state B2B, free-text line, inclusive pricing, and
draft -> issue -> cancel -> copy.

What this does NOT cover, deliberately
--------------------------------------
Rendering, CSS, the JS console and `window.print()`. Those need a real browser
and stay open as 1.4.9 finding #9 / 2.0.9. This file is the cheap half of that
gap and it runs on every push.
"""

import json
import urllib.error
import urllib.request
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import LiveServerTestCase, TransactionTestCase

from apps.accounts.models import BusinessProfile
from apps.inventory.models import Item
from apps.parties.models import Party

User = get_user_model()

API = "/api/v1"

PASSWORD = "Str0ngPass!234"

#: Every field `Web_Frontend/invoices/billing.js` reads off a preview response.
#: If one of these is renamed or dropped, this fails loudly instead of shipping
#: a blank totals panel to a real user. Derived from the JS, not from the
#: serializer, so the two cannot drift apart silently.
PREVIEW_CONTRACT_FIELDS = {
    "totals",
    "supply_type",
    "state_tax_label",
    "place_of_supply",
    "document_title",
    "lines",
    "hsn_summary",
}

PREVIEW_TOTAL_FIELDS = {
    "subtotal",
    "total_discount",
    "taxable_total",
    "cgst_total",
    "sgst_total",
    "igst_total",
    "round_off",
    "grand_total",
    "tax_total",
}

#: Money fields the invoice serializer returns at the top level. The list page
#: and the billing screen both read these directly off a saved invoice.
INVOICE_CONTRACT_FIELDS = {
    "id",
    "status",
    "invoice_number",
    "display_number",
    "supply_type",
    "place_of_supply",
    "subtotal",
    "total_discount",
    "taxable_total",
    "cgst_total",
    "sgst_total",
    "igst_total",
    "round_off",
    "grand_total",
    "items",
    "business_state_code",
    # NOTE: `hsn_summary` is deliberately absent. It is a *computed* breakdown
    # and lives only on the /preview/ response; billing.js reads it from there
    # (`previewTotals?.hsn_summary || []`) and re-runs preview whenever an
    # invoice is opened. A saved invoice is never expected to carry it.
}


class HttpClient:
    """Minimal real-HTTP client. No DRF test client, no in-process shortcut."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token: str | None = None

    def call(self, method: str, path: str, body=None):
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw, status = resp.read().decode(), resp.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read().decode(), exc.code
        parsed = json.loads(raw) if raw.strip()[:1] in "{[" else {}
        return status, parsed

    def login(self, email: str):
        status, body = self.call(
            "POST", f"{API}/auth/login/", {"email": email, "password": PASSWORD}
        )
        if status != 200:
            raise AssertionError(f"login failed ({status}): {json.dumps(body)[:300]}")
        self.token = body["access"]
        return body


class ContractWalkTests(LiveServerTestCase):
    """
    LiveServerTestCase (not TestCase) because a real socket server is needed.
    That also means each test commits, so fixtures are rebuilt per test.
    """

    maxDiff = None

    # -- assertions ---------------------------------------------------------
    def assert_ok(self, status, body, what):
        self.assertEqual(status, 200, f"{what} -> {status}: {json.dumps(body)[:300]}")

    def assert_keys(self, payload, required, what):
        missing = required - set(payload)
        self.assertFalse(missing, f"{what} is missing {sorted(missing)}")

    def dec(self, value) -> Decimal:
        return Decimal(str(value))

    # -- fixtures ------------------------------------------------------------
    def build_fixtures(self):
        # Every test here logs in, and `login` is throttled at 10/minute. The
        # throttle counter lives in the (per-process) default cache, which other
        # test modules - `ThrottleConfigTests` deliberately exhausts it - would
        # otherwise leave full. Clearing here makes each test independent without
        # weakening the production rate.
        cache.clear()

        self.user = User.objects.create_user(
            email="contract@example.com", password=PASSWORD, first_name="Owner"
        )
        self.business = BusinessProfile.objects.create(
            user=self.user,
            trade_name="Contract Traders",
            company_name="Contract Traders",
            owner_name="Owner",
            phone="9000000000",
            state_code="36",
            gstin="36AAAAA0000A1Z5",
            pan="AAAAA0000A",
            address_line="1 Test Street",
            city="Hyderabad",
            pincode="500001",
            invoice_number_prefix="INV",
            gst_registration_type=BusinessProfile.GstRegistrationType.REGULAR,
        )
        # Walk-in has a deliberately BLANK state: decision 21 resolves it from the
        # business at invoice time. It is flagged, NOT matched by name.
        self.walk_in = Party.objects.create(
            business=self.business,
            name="Walk-in / Cash Customer",
            mobile="9876543210",
            state_code="",
            is_walk_in=True,
        )
        self.karnataka = Party.objects.create(
            business=self.business,
            name="Karnataka B2B Buyer",
            mobile="9000000123",
            state_code="29",
            gstin="29AAACK1234M1Z5",
            billing_address="9 Market Road",
            billing_city="Bengaluru",
            billing_pincode="560001",
        )
        self.item = Item.objects.create(
            business=self.business,
            item_code="SHIRT-001",
            name="Cotton Shirt",
            item_type=Item.ItemType.PRODUCT,
            unit="PCS",
            hsn_sac_code="610510",
            sales_price=Decimal("249.50"),
            tax_rate=Decimal("5.00"),
            current_stock=Decimal("100.000"),
            low_stock_threshold=Decimal("5.000"),
        )
        self.http = HttpClient(self.live_server_url)
        self.http.login("contract@example.com")

    def line_for_item(self, **overrides):
        payload = {
            "item": self.item.pk,
            "quantity": "2",
            "unit_price": str(self.item.sales_price),
            "tax_rate": str(self.item.tax_rate),
        }
        payload.update(overrides)
        return payload

    # -- the contract itself -------------------------------------------------
    def test_01_login_returns_the_fields_the_frontend_stores(self):
        self.build_fixtures()
        body = HttpClient(self.live_server_url).login("contract@example.com")
        self.assert_keys(body, {"access", "refresh"}, "login response")

    def test_02_walk_in_sale_charges_cgst_plus_sgst(self):
        """The single most common retail invoice. Decision 20 made it possible."""
        self.build_fixtures()
        payload = {"party": self.walk_in.pk, "items": [self.line_for_item()]}

        status, preview = self.http.call("POST", f"{API}/invoices/preview/", payload)
        self.assert_ok(status, preview, "walk-in preview")
        self.assert_keys(preview, PREVIEW_CONTRACT_FIELDS, "preview")
        self.assert_keys(preview["totals"], PREVIEW_TOTAL_FIELDS, "preview totals")

        self.assertEqual(preview["supply_type"], "INTRA", preview["supply_type"])
        # The label names the *combined* column. Karnataka is not a UT without a
        # legislature, so it is SGST; a UT would say UTGST, inter-state IGST.
        self.assertEqual(preview["state_tax_label"], "SGST", preview["state_tax_label"])
        self.assertEqual(preview["place_of_supply"], "36", preview["place_of_supply"])
        totals = preview["totals"]
        self.assertGreater(self.dec(totals["cgst_total"]), 0, "CGST should be charged")
        self.assertGreater(self.dec(totals["sgst_total"]), 0, "SGST should be charged")
        self.assertEqual(self.dec(totals["igst_total"]), 0, "IGST must be zero intra-state")

        # Preview must equal what saving actually stores, on every money field.
        status, invoice = self.http.call("POST", f"{API}/invoices/", payload)
        self.assertEqual(status, 201, json.dumps(invoice)[:300])
        self.assert_keys(invoice, INVOICE_CONTRACT_FIELDS, "saved invoice")
        for field in PREVIEW_TOTAL_FIELDS:
            self.assertEqual(
                str(preview["totals"][field]),
                str(invoice[field]),
                f"{field} drifted between preview and save",
            )

    def test_03_inter_state_b2b_charges_igst(self):
        self.build_fixtures()
        payload = {"party": self.karnataka.pk, "items": [self.line_for_item()]}

        status, preview = self.http.call("POST", f"{API}/invoices/preview/", payload)
        self.assert_ok(status, preview, "inter-state preview")

        self.assertEqual(preview["supply_type"], "INTER", preview["supply_type"])
        self.assertEqual(preview["state_tax_label"], "IGST", preview["state_tax_label"])
        self.assertEqual(preview["place_of_supply"], "29", preview["place_of_supply"])
        self.assertGreater(self.dec(preview["totals"]["igst_total"]), 0, "IGST should be charged")
        self.assertEqual(self.dec(preview["totals"]["cgst_total"]), 0, "no CGST on IGST")
        self.assertEqual(self.dec(preview["totals"]["sgst_total"]), 0, "no SGST on IGST")

    def test_04_free_text_line_needs_no_catalogue_item(self):
        self.build_fixtures()
        payload = {
            "party": self.walk_in.pk,
            "items": [
                self.line_for_item(),
                {
                    "item_name": "Transport charges",
                    "item_type": "SERVICE",
                    "hsn_sac_code": "996511",
                    "unit": "NOS",
                    "service_description": "Road freight",
                    "quantity": "1",
                    "unit_price": "80.00",
                    "tax_rate": "18.00",
                },
            ],
        }
        status, preview = self.http.call("POST", f"{API}/invoices/preview/", payload)
        self.assert_ok(status, preview, "free-text preview")
        self.assertEqual(len(preview["lines"]), 2, preview["lines"])

        codes = sorted(row["hsn_sac_code"] for row in preview["hsn_summary"])
        self.assertEqual(codes, ["610510", "996511"], codes)

        # The HSN summary must conserve the grand total or GSTR-1 is wrong.
        summary_total = sum(self.dec(row["total"]) for row in preview["hsn_summary"])
        self.assertEqual(
            summary_total, self.dec(preview["totals"]["grand_total"]), "HSN summary loses money"
        )

        status, invoice = self.http.call("POST", f"{API}/invoices/", payload)
        self.assertEqual(status, 201, json.dumps(invoice)[:300])
        self.assertIsNone(invoice["items"][1]["item"], "free-text line should have no item")
        self.assertEqual(invoice["items"][1]["item_name"], "Transport charges")

    def test_05_inclusive_price_extracts_the_tax(self):
        self.build_fixtures()
        payload = {
            "party": self.walk_in.pk,
            "prices_include_tax": True,
            "items": [self.line_for_item(quantity="1", unit_price="118.00", tax_rate="18.00")],
        }
        status, preview = self.http.call("POST", f"{API}/invoices/preview/", payload)
        self.assert_ok(status, preview, "inclusive preview")
        totals = preview["totals"]
        self.assertEqual(self.dec(totals["grand_total"]), Decimal("118.00"), totals["grand_total"])
        self.assertEqual(self.dec(totals["taxable_total"]), Decimal("100.00"), totals["taxable_total"])
        self.assertEqual(self.dec(totals["tax_total"]), Decimal("18.00"), totals["tax_total"])

    def test_06_the_browser_cannot_dictate_the_totals(self):
        """
        The old contract walk is what caught this class of bug. A malicious or
        simply buggy client posting its own totals must not change what is stored.
        """
        self.build_fixtures()
        honest = {"party": self.walk_in.pk, "items": [self.line_for_item()]}
        tampered = dict(honest)
        tampered.update(
            {
                "subtotal": "1.00",
                "taxable_total": "0.01",
                "cgst_total": "999.00",
                "sgst_total": "888.00",
                "igst_total": "777.00",
                "grand_total": "0.01",
            }
        )

        status, preview = self.http.call("POST", f"{API}/invoices/preview/", honest)
        self.assert_ok(status, preview, "honest preview")
        status, invoice = self.http.call("POST", f"{API}/invoices/", tampered)
        self.assertEqual(status, 201, json.dumps(invoice)[:300])

        for field in ("subtotal", "taxable_total", "cgst_total", "sgst_total", "igst_total", "grand_total"):
            self.assertEqual(
                str(invoice[field]),
                str(preview["totals"][field]),
                f"posted {field} was not ignored",
            )

    def test_07_draft_issue_cancel_copy_lifecycle(self):
        self.build_fixtures()
        payload = {"party": self.walk_in.pk, "items": [self.line_for_item()]}

        status, draft = self.http.call("POST", f"{API}/invoices/", payload)
        self.assertEqual(status, 201, json.dumps(draft)[:300])
        self.assertEqual(draft["status"], "DRAFT", draft["status"])
        self.assertEqual(draft["invoice_number"], "", "a draft must not be numbered")

        pk = draft["id"]
        # First issue is 201 (a document was created); a repeat is 200 (idempotent).
        status, issued = self.http.call("POST", f"{API}/invoices/{pk}/issue/", {})
        self.assertEqual(status, 201, json.dumps(issued)[:300])
        self.assertEqual(issued["status"], "ISSUED", issued["status"])
        self.assertTrue(issued["invoice_number"], "issuing must allocate a number")

        # Idempotent: a double-click must not burn a second number.
        status, again = self.http.call("POST", f"{API}/invoices/{pk}/issue/", {})
        self.assertEqual(status, 200, json.dumps(again)[:300])
        self.assertEqual(again["invoice_number"], issued["invoice_number"], "double issue burned a number")

        status, copied = self.http.call("POST", f"{API}/invoices/{pk}/copy/", {})
        self.assertEqual(status, 201, json.dumps(copied)[:300])
        self.assertEqual(copied["status"], "DRAFT", "a copy is always a draft")
        self.assertEqual(copied["invoice_number"], "", "a copy must not inherit the number")
        self.assertNotEqual(copied["id"], pk)

        status, cancelled = self.http.call(
            "POST", f"{API}/invoices/{pk}/cancel/", {"reason": "Contract test cancellation"}
        )
        self.assertEqual(status, 200, json.dumps(cancelled)[:300])
        self.assertEqual(cancelled["status"], "CANCELLED", cancelled["status"])

    def test_08_error_responses_use_the_project_envelope(self):
        """429, 400 and friends must all carry a machine-readable `error`."""
        self.build_fixtures()
        # Free-text line with no HSN: refused, and recognisably so.
        bad = {
            "party": self.walk_in.pk,
            "items": [
                {
                    "item_name": "Mystery",
                    "item_type": "SERVICE",
                    "hsn_sac_code": "",
                    "unit": "NOS",
                    "service_description": "x",
                    "quantity": "1",
                    "unit_price": "1.00",
                }
            ],
        }
        status, body = self.http.call("POST", f"{API}/invoices/preview/", bad)
        self.assertEqual(status, 400, json.dumps(body)[:200])
        self.assertEqual(body.get("error"), "VALIDATION_ERROR", json.dumps(body)[:200])
        self.assertIn("message", body, "the envelope needs a human-readable message")

    def test_09_another_tenants_data_is_unreachable(self):
        """Tenant isolation over real HTTP, not just via the in-process client."""
        self.build_fixtures()
        other = User.objects.create_user(email="intruder@example.com", password=PASSWORD)
        BusinessProfile.objects.create(
            user=other,
            trade_name="Intruder",
            company_name="Intruder",
            owner_name="Intruder",
            phone="9000000001",
            state_code="07",
            gstin="07AAAAA0000A1Z5",
            pan="AAAAA0000A",
            address_line="2 Other Road",
            city="Delhi",
            pincode="110001",
            invoice_number_prefix="OTH",
        )
        intruder_party = Party.objects.create(
            business=other.business, name="Intruder Customer", mobile="9000000999", state_code="07"
        )

        status, body = self.http.call("GET", f"{API}/parties/")
        self.assertEqual(status, 200, json.dumps(body)[:200])
        names = [row["name"] for row in body["results"]]
        self.assertNotIn("Intruder Customer", names, "cross-tenant leak in party list")

        status, body = self.http.call("GET", f"{API}/invoices/{intruder_party.pk}/")
        # Either 404 (not visible) or 403; what must NOT happen is 200.
        self.assertIn(status, (403, 404), f"cross-tenant read returned {status}")


class UnauthenticatedAccessTests(TransactionTestCase):
    """Every endpoint must refuse an anonymous caller except register/login/health."""

    def test_endpoints_refuse_anonymous_callers(self):
        from rest_framework.test import APIClient

        allowed = {f"{API}/auth/register/", f"{API}/auth/login/", "/api/health/"}
        endpoints = [
            f"{API}/invoices/",
            f"{API}/invoices/preview/",
            f"{API}/parties/",
            f"{API}/items/",
            f"{API}/business/profile/",
        ]
        for path in endpoints:
            if path in allowed:
                continue
            response = APIClient().get(path)
            self.assertIn(
                response.status_code,
                (401, 403),
                f"{path} returned {response.status_code} to an anonymous caller",
            )