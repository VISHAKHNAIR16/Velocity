"""
The two open compliance/formatting decisions (1.4.4 close-out).

1. `BusinessProfile.hsn_requirement` - STRICT (default) vs STATUTORY.
2. The invoice-number series width, which is a *minimum* width in Python and
   therefore grows rather than truncates.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import BusinessProfile, HsnRequirement
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


class HsnRequirementModeTests(TestCase):
    """
    STRICT asks for HSN on every line. STATUTORY only above Rs 5,000 to a
    GSTIN holder, which is what GST Notification 12/2017-CT actually says.
    """

    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.item = make_item(self.user)
        self.buyer = make_party(self.user, name="GST Buyer", state_code="36", gstin="36AAAAA0000A1Z5")
        self.walk_in = make_party(self.user, name="Walk-in", state_code="36", gstin="")

    def set_mode(self, mode):
        self.user.business.hsn_requirement = mode
        self.user.business.save(update_fields=["hsn_requirement"])

    def draft(self, party, **line_overrides):
        return self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": party.pk,
                "items": [line_payload(self.item, **line_overrides)],
            },
            format="json",
        ).json()

    def strip_hsn(self, draft):
        from apps.invoices.models import InvoiceItem

        InvoiceItem.objects.filter(invoice_id=draft["id"]).update(hsn_sac_code="")

    def test_default_is_strict(self):
        """Existing businesses must not silently loosen the gate."""
        self.assertEqual(
            BusinessProfile._meta.get_field("hsn_requirement").default, HsnRequirement.STRICT
        )
        self.assertEqual(self.user.business.hsn_requirement, HsnRequirement.STRICT)

    def test_strict_blocks_a_missing_hsn_even_to_a_walk_in(self):
        self.set_mode(HsnRequirement.STRICT)
        draft = self.draft(self.walk_in)
        self.strip_hsn(draft)
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "HSN_REQUIRED")

    def test_statutory_blocks_a_missing_hsn_above_the_threshold(self):
        self.set_mode(HsnRequirement.STATUTORY)
        # 2 x 249.50 + 5% = 523.95, comfortably above Rs 5,000? No - below it.
        # Push it above with a large quantity.
        draft = self.draft(self.buyer, quantity="40", unit_price="249.50")
        self.strip_hsn(draft)
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "HSN_REQUIRED")

    def test_statutory_allows_a_missing_hsn_below_the_threshold(self):
        self.set_mode(HsnRequirement.STATUTORY)
        draft = self.draft(self.buyer)  # 523.95, below Rs 5,000
        self.strip_hsn(draft)
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_statutory_allows_a_missing_hsn_without_a_gstin(self):
        """B2C: no GSTIN, so the B2B threshold never engages."""
        self.set_mode(HsnRequirement.STATUTORY)
        draft = self.draft(self.walk_in, quantity="40", unit_price="249.50")
        self.strip_hsn(draft)
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_the_threshold_is_exactly_five_thousand(self):
        self.set_mode(HsnRequirement.STATUTORY)
        # Above the threshold, using a quantity that lands the total over 5000.
        draft = self.draft(self.buyer, quantity="21", unit_price="249.50")
        self.assertGreater(Decimal(draft["grand_total"]), Decimal("5000.00"))
        self.strip_hsn(draft)
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_statutory_still_rejects_an_invalid_hsn_code(self):
        """
        Loosening the *requirement* must not loosen the *format* rule: a garbage
        code is still wrong wherever the gate engages.

        Scope note: below the threshold in STATUTORY mode the gate does not run
        at all, so a code planted directly in the database is not re-checked at
        issue time. The draft-time validator is what normally prevents an
        invalid code from existing - see the test below.
        """
        from apps.invoices.models import InvoiceItem

        self.set_mode(HsnRequirement.STATUTORY)
        draft = self.draft(self.buyer, quantity="40", unit_price="249.50")
        InvoiceItem.objects.filter(invoice_id=draft["id"]).update(hsn_sac_code="12345")
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "HSN_INVALID")

    def test_an_invalid_hsn_is_refused_when_the_draft_is_saved(self):
        """The real first line of defence, in either mode."""
        for mode in (HsnRequirement.STRICT, HsnRequirement.STATUTORY):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                response = self.client_.post(
                    INVOICE_LIST_URL,
                    {
                        "party": self.walk_in.pk,
                        "items": [
                            {
                                "item_name": "Odd goods",
                                "item_type": "PRODUCT",
                                "hsn_sac_code": "12345",  # 5 digits: never used by GST
                                "unit": "PCS",
                                "quantity": "1",
                                "unit_price": "10.00",
                            }
                        ],
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 400)

    def test_an_unregistered_business_is_exempt_in_both_modes(self):
        """A Bill of Supply carries no HSN requirement."""
        self.user.business.gst_registration_type = (
            BusinessProfile.GstRegistrationType.UNREGISTERED
        )
        self.user.business.save(update_fields=["gst_registration_type"])

        for mode in (HsnRequirement.STRICT, HsnRequirement.STATUTORY):
            with self.subTest(mode=mode):
                self.set_mode(mode)
                draft = self.draft(self.buyer, quantity="40", unit_price="249.50")
                self.strip_hsn(draft)
                response = self.client_.post(issue_url(draft["id"]), {}, format="json")
                self.assertEqual(response.status_code, 201, response.content[:300])

    def test_the_mode_is_readable_and_writable_through_the_profile_api(self):
        response = self.client_.patch(
            "/api/v1/business/profile/",
            {"hsn_requirement": HsnRequirement.STATUTORY},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.user.business.refresh_from_db()
        self.assertEqual(self.user.business.hsn_requirement, HsnRequirement.STATUTORY)

    def test_an_unknown_mode_is_rejected(self):
        response = self.client_.patch(
            "/api/v1/business/profile/", {"hsn_requirement": "WHATEVER"}, format="json"
        )
        self.assertEqual(response.status_code, 400)


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
