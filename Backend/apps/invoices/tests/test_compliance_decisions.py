"""
The two open compliance/formatting decisions (1.4.4 close-out).

1. `BusinessProfile.hsn_requirement` - STRICT (default) vs STATUTORY.
2. The invoice-number series width, which is a *minimum* width in Python and
   therefore grows rather than truncates.
"""

from datetime import date

from django.test import TestCase

from apps.accounts.models import BusinessProfile, HsnMinDigits
from apps.invoices.services.numbering import (
    MAX_NUMBER_LENGTH,
    MAX_SERIES_NUMBER,
    NUMBER_WIDTH,
    NumberingError,
    _format_number,
)
from apps.invoices.tests.base import (
    INVOICE_LIST_URL,
    api_client_for,
    line_payload,
    make_item,
    make_party,
    make_user,
)
from apps.invoices.tests.test_issue_api import issue_url


class HsnMinDigitsTests(TestCase):
    """
    Decision 22, which replaces decision 18's `STATUTORY` mode.

    An HSN/SAC code is required on **every** line of a tax invoice. What varies
    by business is how many digits of it must be reported, and that scales with
    annual turnover (4 digits up to Rs 5 crore, 6 above).

    The retired `STATUTORY` mode gated on a per-invoice Rs 5,000 threshold. That
    number is not an HSN rule - it is an old reverse-charge daily limit - and it
    asked "does this line need a code?" instead of "how precise must it be?".
    These tests pin the digit rule and prove the Rs 5,000 figure is gone.
    """

    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.item = make_item(self.user)  # 6-digit HSN 610510
        self.buyer = make_party(self.user, name="GST Buyer", state_code="36", gstin="36AAAAA0000A1Z5")
        self.walk_in = make_party(self.user, name="Walk-in", state_code="36", gstin="")

    def set_min_digits(self, digits):
        self.user.business.hsn_min_digits = digits
        self.user.business.save(update_fields=["hsn_min_digits"])

    def draft(self, party, **line_overrides):
        return self.client_.post(
            INVOICE_LIST_URL,
            {"party": party.pk, "items": [line_payload(self.item, **line_overrides)]},
            format="json",
        ).json()

    def set_line_hsn(self, draft, code):
        from apps.invoices.models import InvoiceItem

        InvoiceItem.objects.filter(invoice_id=draft["id"]).update(hsn_sac_code=code)

    def issue(self, draft_id):
        return self.client_.post(issue_url(draft_id), {}, format="json")

    # -- the default ---------------------------------------------------------
    def test_the_default_is_four_digits(self):
        """Existing businesses must not silently become stricter."""
        self.assertEqual(
            BusinessProfile._meta.get_field("hsn_min_digits").default, HsnMinDigits.HSN_4
        )
        self.assertEqual(self.user.business.hsn_min_digits, 4)

    def test_the_retired_mode_is_gone_from_the_schema(self):
        """Decision 22: the Rs 5,000 mode must not linger as a usable option."""
        field_names = {f.name for f in BusinessProfile._meta.get_fields()}
        self.assertNotIn("hsn_requirement", field_names)
        self.assertIn("hsn_min_digits", field_names)

    # -- a code is always required ------------------------------------------
    def test_a_missing_hsn_is_refused_for_every_recipient(self):
        for party, label in ((self.walk_in, "B2C walk-in"), (self.buyer, "B2B with GSTIN")):
            with self.subTest(recipient=label):
                draft = self.draft(party)
                self.set_line_hsn(draft, "")
                response = self.issue(draft["id"])
                self.assertEqual(response.status_code, 400, response.content[:200])
                self.assertEqual(response.json()["error"], "HSN_REQUIRED")

    def test_the_invoice_value_does_not_change_the_requirement(self):
        """
        The exact regression decision 22 exists to prevent: Rs 5,000 used to
        decide whether a code was needed. A tiny invoice and a huge one now
        behave identically.
        """
        for quantity in ("1", "100"):
            with self.subTest(quantity=quantity):
                draft = self.draft(self.buyer, quantity=quantity)
                self.set_line_hsn(draft, "")
                response = self.issue(draft["id"])
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "HSN_REQUIRED")

    def test_a_gstin_on_the_recipient_does_not_change_the_requirement(self):
        draft = self.draft(self.buyer)
        self.set_line_hsn(draft, "")
        response = self.issue(draft["id"])
        self.assertEqual(response.json()["error"], "HSN_REQUIRED")

    # -- the digit minimum ---------------------------------------------------
    def test_a_four_digit_code_passes_at_the_minimum_of_four(self):
        self.set_min_digits(HsnMinDigits.HSN_4)
        draft = self.draft(self.buyer)
        self.set_line_hsn(draft, "6105")  # valid 4-digit HSN
        response = self.issue(draft["id"])
        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_a_four_digit_code_is_refused_at_the_minimum_of_six(self):
        self.set_min_digits(HsnMinDigits.HSN_6)
        draft = self.draft(self.buyer)
        self.set_line_hsn(draft, "6105")
        response = self.issue(draft["id"])
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["error"], "HSN_TOO_SHORT")

    def test_a_six_digit_code_passes_at_the_minimum_of_six(self):
        self.set_min_digits(HsnMinDigits.HSN_6)
        draft = self.draft(self.buyer)  # item HSN is 610510
        response = self.issue(draft["id"])
        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_the_minimum_is_not_a_maximum(self):
        """An 8-digit code must still be accepted at a 6-digit minimum."""
        self.set_min_digits(HsnMinDigits.HSN_6)
        draft = self.draft(self.buyer)
        self.set_line_hsn(draft, "61051010")
        response = self.issue(draft["id"])
        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_the_article_keeps_carrying_the_digits_its_owner_already_had(self):
        """Raising the setting must never mutate an already-issued invoice."""
        self.set_min_digits(HsnMinDigits.HSN_4)
        draft = self.draft(self.buyer)
        self.set_line_hsn(draft, "6105")
        self.assertEqual(self.issue(draft["id"]).status_code, 201)

        self.set_min_digits(HsnMinDigits.HSN_6)
        from apps.invoices.models import InvoiceItem

        self.assertEqual(
            InvoiceItem.objects.get(invoice_id=draft["id"]).hsn_sac_code,
            "6105",
            "an issued invoice must keep the code it was issued with",
        )

    # -- SAC (services) ------------------------------------------------------
    def test_a_service_sac_is_measured_by_the_same_minimum(self):
        self.set_min_digits(HsnMinDigits.HSN_6)
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.buyer.pk,
                "items": [
                    {
                        "item_name": "Consulting",
                        "item_type": "SERVICE",
                        "hsn_sac_code": "9983",  # 4-digit SAC
                        "unit": "HRS",
                        "service_description": "Advisory",
                        "quantity": "1",
                        "unit_price": "500.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content[:300])
        issued = self.issue(response.json()["id"])
        self.assertEqual(issued.status_code, 400, issued.content[:300])
        self.assertEqual(issued.json()["error"], "HSN_TOO_SHORT")

    # -- format is still separate from the minimum ---------------------------
    def test_a_structurally_invalid_code_is_still_invalid(self):
        """5 and 7 digits are not HSN structures at any minimum."""
        for digits in (HsnMinDigits.HSN_4, HsnMinDigits.HSN_6):
            with self.subTest(min_digits=digits):
                self.set_min_digits(digits)
                draft = self.draft(self.buyer)
                self.set_line_hsn(draft, "12345")
                response = self.issue(draft["id"])
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "HSN_INVALID")

    def test_an_invalid_hsn_is_refused_when_the_draft_is_saved(self):
        """The first line of defence, at draft time rather than issue time."""
        for digits in (HsnMinDigits.HSN_4, HsnMinDigits.HSN_6):
            with self.subTest(min_digits=digits):
                self.set_min_digits(digits)
                response = self.client_.post(
                    INVOICE_LIST_URL,
                    {
                        "party": self.walk_in.pk,
                        "items": [
                            {
                                "item_name": "Odd goods",
                                "item_type": "PRODUCT",
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

    def test_a_bill_of_supply_is_exempt_at_either_minimum(self):
        """An UNREGISTERED business issues a Bill of Supply: no HSN requirement."""
        self.user.business.gst_registration_type = (
            BusinessProfile.GstRegistrationType.UNREGISTERED
        )
        self.user.business.save(update_fields=["gst_registration_type"])

        for digits in (HsnMinDigits.HSN_4, HsnMinDigits.HSN_6):
            with self.subTest(min_digits=digits):
                self.set_min_digits(digits)
                draft = self.draft(self.buyer)
                self.set_line_hsn(draft, "")
                self.assertEqual(self.issue(draft["id"]).status_code, 201)

    # -- the profile API -----------------------------------------------------
    def test_the_minimum_is_readable_and_writable_through_the_profile_api(self):
        response = self.client_.patch(
            "/api/v1/business/profile/", {"hsn_min_digits": 6}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.user.business.refresh_from_db()
        self.assertEqual(self.user.business.hsn_min_digits, 6)

    def test_an_out_of_range_minimum_is_rejected(self):
        for bad in (0, 5, 7, 8):
            with self.subTest(value=bad):
                response = self.client_.patch(
                    "/api/v1/business/profile/", {"hsn_min_digits": bad}, format="json"
                )
                self.assertEqual(response.status_code, 400)

    def test_the_retired_field_cannot_change_anything(self):
        """
        DRF silently ignores keys a serializer does not declare, so posting the
        retired `hsn_requirement` does not error - and, importantly, does not
        change the setting either. The safety property that matters is that the
        old knob is dead, not that the request is rejected.
        """
        before = self.user.business.hsn_min_digits
        response = self.client_.patch(
            "/api/v1/business/profile/", {"hsn_requirement": "STATUTORY"}, format="json"
        )
        self.assertIn(response.status_code, (200, 400), response.content[:200])
        self.user.business.refresh_from_db()
        self.assertEqual(self.user.business.hsn_min_digits, before)


class NumberSeriesWidthTests(TestCase):
    """
    Python's `%0Nd` is a MINIMUM width, so a large series grows the number
    instead of truncating. Truncation would mint duplicate numbers on a legal
    document, which is the worst possible outcome - so the width must fail
    loudly instead.
    """

    def test_the_column_is_exactly_full_at_the_maximum_series(self):
        """A 4-char prefix at the 5-digit maximum fills all 16 characters."""
        number = _format_number("VELO", date(2026, 4, 1), MAX_SERIES_NUMBER)
        self.assertEqual(number, "VELO/26-27/99999")
        self.assertEqual(len(number), MAX_NUMBER_LENGTH)

    def test_the_series_cannot_grow_past_the_column(self):
        with self.assertRaises(NumberingError) as caught:
            _format_number("VELO", date(2026, 4, 1), MAX_SERIES_NUMBER + 1)
        self.assertEqual(caught.exception.code, "SERIES_EXHAUSTED")
        self.assertIn("support", caught.exception.message.lower())

    def test_the_maximum_is_exactly_the_fixed_width(self):
        self.assertEqual(MAX_SERIES_NUMBER, 10**NUMBER_WIDTH - 1)
        self.assertEqual(len(str(MAX_SERIES_NUMBER)), NUMBER_WIDTH)

    def test_the_width_arithmetic_is_pinned(self):
        """
        If anyone changes NUMBER_WIDTH or the FY format, this fails rather than
        overflowing the column silently at some volume in the future.
        """
        prefix, fy, series = len("VELO"), len("26-27"), NUMBER_WIDTH
        self.assertEqual(prefix + 1 + fy + 1 + series, MAX_NUMBER_LENGTH)
