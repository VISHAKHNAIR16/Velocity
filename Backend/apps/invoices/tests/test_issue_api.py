"""
Issue / cancel over the public API (step D).

Literal URLs throughout, per 1.3.7.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import BusinessProfile
from apps.inventory.models import Item
from apps.invoices.models import Invoice, InvoiceCounter, InvoiceItem
from apps.invoices.tests.base import (
    INVOICE_LIST_URL,
    api_client_for,
    detail_url,
    line_payload,
    make_item,
    make_party,
    make_user,
)
from apps.parties.models import Party


def issue_url(invoice) -> str:
    pk = invoice.pk if hasattr(invoice, "pk") else invoice
    return f"/api/v1/invoices/{pk}/issue/"


def cancel_url(invoice) -> str:
    pk = invoice.pk if hasattr(invoice, "pk") else invoice
    return f"/api/v1/invoices/{pk}/cancel/"


class IssueTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)
        self.draft = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()

    def test_issue_returns_201_and_a_number(self):
        response = self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201)

        body = response.json()
        self.assertEqual(body["status"], Invoice.Status.ISSUED)
        self.assertEqual(body["invoice_number"], "INV/26-27/00001")
        self.assertEqual(body["display_number"], "INV/26-27/00001")
        self.assertIsNotNone(body["issued_at"])

    def test_issue_stamps_the_actor(self):
        self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        invoice = Invoice.objects.get(pk=self.draft["id"])
        self.assertEqual(invoice.issued_by, self.user)

    def test_issue_snapshots_the_party(self):
        self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        invoice = Invoice.objects.get(pk=self.draft["id"])
        self.assertEqual(invoice.recipient_name, "Sharma Stores")
        self.assertEqual(invoice.recipient_state_code, "36")
        self.assertEqual(invoice.business_name, self.user.business.company_name)
        self.assertEqual(invoice.business_gstin, self.user.business.gstin)
        self.assertEqual(invoice.document_title, "Tax Invoice")

    def test_the_snapshot_survives_a_later_party_rename(self):
        """The document must stand on its own once issued."""
        self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        self.party.name = "Renamed After The Fact"
        self.party.gstin = "36BBBBB9999B1Z5"
        self.party.save()

        invoice = Invoice.objects.get(pk=self.draft["id"])
        self.assertEqual(invoice.recipient_name, "Sharma Stores")
        self.assertEqual(invoice.recipient_gstin, "")

    def test_issuing_twice_is_idempotent_and_burns_no_second_number(self):
        first = self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        self.assertEqual(first.status_code, 201)

        second = self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["invoice_number"], "INV/26-27/00001")

        self.assertEqual(
            InvoiceCounter.objects.get(business=self.user.business).last_number, 1
        )
        self.assertEqual(Invoice.objects.count(), 1)

    def test_issue_recalculates_from_stored_lines_not_the_catalogue(self):
        """
        The catalogue may be re-priced after the draft is made. What the customer
        agreed to is the stored line, so that is what must be issued.
        """
        self.item.sales_price = "999.00"
        self.item.name = "Repriced Product"
        self.item.save()

        response = self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["subtotal"], "499.00")

        line = InvoiceItem.objects.get(invoice_id=self.draft["id"])
        self.assertEqual(line.item_name, "Cotton Shirt")
        self.assertEqual(line.unit_price, Decimal("249.50"))

    def test_consecutive_issues_are_consecutive_numbers(self):
        second_draft = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()

        first = self.client_.post(issue_url(self.draft["id"]), {}, format="json").json()
        second = self.client_.post(issue_url(second_draft["id"]), {}, format="json").json()
        self.assertEqual(first["invoice_number"], "INV/26-27/00001")
        self.assertEqual(second["invoice_number"], "INV/26-27/00002")

    def test_a_cancelled_invoice_cannot_be_issued(self):
        self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        self.client_.post(
            cancel_url(self.draft["id"]), {"reason": "Wrong party"}, format="json"
        )
        response = self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "ALREADY_CANCELLED")

    def test_next_number_endpoint_does_not_consume(self):
        response = self.client_.get("/api/v1/invoices/next_number/?date=2026-04-01")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["next_number"], "INV/26-27/00001")

        self.client_.post(issue_url(self.draft["id"]), {}, format="json")
        after = self.client_.get("/api/v1/invoices/next_number/?date=2026-04-01")
        self.assertEqual(after.json()["next_number"], "INV/26-27/00002")
        self.assertEqual(
            InvoiceCounter.objects.get(business=self.user.business).last_number, 1
        )

    def test_next_number_rejects_a_bad_date(self):
        response = self.client_.get("/api/v1/invoices/next_number/?date=31-04-2026")
        self.assertEqual(response.status_code, 400)


class IssueGateTests(TestCase):
    """Each gate gets a machine-readable code, checked on a real invoice."""

    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)

    def draft(self, **overrides):
        payload = {"party": self.party.pk, "items": [line_payload(self.item)]}
        payload.update(overrides)
        return self.client_.post(INVOICE_LIST_URL, payload, format="json").json()

    def test_business_without_a_state_is_refused(self):
        # Blank the state AFTER the draft exists: place-of-supply resolution
        # needs it, so a draft cannot even be created without one.
        draft = self.draft()
        self.user.business.state_code = ""
        self.user.business.save(update_fields=["state_code"])
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "BUSINESS_PROFILE_INCOMPLETE")
        self.assertEqual(InvoiceCounter.objects.count(), 0, "no number may be burned")

    def test_party_without_a_state_falls_back_to_the_business_state(self):
        """
        Decision 9. `Party` has no `is_walk_in` column, so a blank party state is
        resolved from the business rather than refused.
        """
        draft = self.draft()
        self.party.state_code = ""
        self.party.save(update_fields=["state_code"])
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(response.json()["place_of_supply"], "36")
        self.assertEqual(response.json()["supply_type"], "INTRA")

    def test_unresolvable_place_of_supply_is_refused(self):
        """
        Neither party nor business has a state, so CGST/SGST vs IGST cannot be
        determined.

        `BUSINESS_PROFILE_INCOMPLETE` is reported rather than
        `PARTY_STATE_MISSING`, because the blank business state is the more
        specific cause and is checked first.
        """
        draft = self.draft()
        self.party.state_code = ""
        self.party.save(update_fields=["state_code"])
        self.user.business.state_code = ""
        self.user.business.save(update_fields=["state_code"])

        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "BUSINESS_PROFILE_INCOMPLETE")
        self.assertEqual(InvoiceCounter.objects.count(), 0)

    def test_a_free_text_line_without_an_hsn_never_becomes_a_draft(self):
        """
        The friendly first line of defence: a free-text line may not even be
        saved without an HSN/SAC, so it can never reach the issue-time gate.
        """
        response = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.party.pk,
                "items": [
                    line_payload(self.item),
                    {
                        "item_name": "Mystery fee",
                        "item_type": Item.ItemType.SERVICE,
                        "hsn_sac_code": "",
                        "unit": "NOS",
                        "service_description": "Handling",
                        "quantity": "1",
                        "unit_price": "20.00",
                    },
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(InvoiceCounter.objects.count(), 0)

    def test_hsn_gate_fires_when_a_line_loses_its_code_after_the_draft(self):
        """The gate is checked at issue, not only at draft time."""
        draft = self.draft()
        InvoiceItem.objects.filter(invoice_id=draft["id"]).update(hsn_sac_code="")
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "HSN_REQUIRED")
        self.assertEqual(InvoiceCounter.objects.count(), 0)

    def test_invalid_hsn_is_refused_at_issue(self):
        draft = self.draft()
        InvoiceItem.objects.filter(invoice_id=draft["id"]).update(hsn_sac_code="12345")
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "HSN_INVALID")

    def test_an_empty_invoice_is_refused(self):
        draft = self.draft()
        InvoiceItem.objects.filter(invoice_id=draft["id"]).delete()
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "INVOICE_EMPTY")

    def test_an_unregistered_business_issues_a_bill_of_supply(self):
        self.user.business.gst_registration_type = (
            BusinessProfile.GstRegistrationType.UNREGISTERED
        )
        self.user.business.save(update_fields=["gst_registration_type"])
        response = self.client_.post(issue_url(self.draft()["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["document_title"], "Bill of Supply")

    def test_a_refused_issue_leaves_the_invoice_a_draft(self):
        draft = self.draft()
        self.party.state_code = ""
        self.party.save(update_fields=["state_code"])
        self.user.business.state_code = ""
        self.user.business.save(update_fields=["state_code"])
        self.client_.post(issue_url(draft["id"]), {}, format="json")
        invoice = Invoice.objects.get(pk=draft["id"])
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertFalse(invoice.invoice_number)


class CancelTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)
        self.issued = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()
        self.client_.post(issue_url(self.issued["id"]), {}, format="json")

    def test_cancel_requires_a_reason(self):
        response = self.client_.post(cancel_url(self.issued["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "CANCELLATION_REASON_REQUIRED")
        self.assertEqual(
            Invoice.objects.get(pk=self.issued["id"]).status, Invoice.Status.ISSUED
        )

    def test_cancel_with_a_blank_reason_is_refused(self):
        response = self.client_.post(
            cancel_url(self.issued["id"]), {"reason": "   "}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_cancel_stamps_actor_time_and_reason(self):
        response = self.client_.post(
            cancel_url(self.issued["id"]), {"reason": "Duplicate entry"}, format="json"
        )
        self.assertEqual(response.status_code, 200)

        invoice = Invoice.objects.get(pk=self.issued["id"])
        self.assertEqual(invoice.status, Invoice.Status.CANCELLED)
        self.assertEqual(invoice.cancellation_reason, "Duplicate entry")
        self.assertEqual(invoice.cancelled_by, self.user)
        self.assertIsNotNone(invoice.cancelled_at)

    def test_cancelling_never_frees_the_number(self):
        """
        The number is on a document the customer holds and in that period's
        GSTR-1. Reusing it would be a compliance problem, so the series must
        continue past it.
        """
        self.client_.post(
            cancel_url(self.issued["id"]), {"reason": "Duplicate entry"}, format="json"
        )
        next_draft = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()
        response = self.client_.post(issue_url(next_draft["id"]), {}, format="json")

        self.assertEqual(response.json()["invoice_number"], "INV/26-27/00002")
        cancelled = Invoice.objects.get(pk=self.issued["id"])
        self.assertEqual(cancelled.invoice_number, "INV/26-27/00001")

    def test_cancelling_twice_is_a_conflict(self):
        self.client_.post(
            cancel_url(self.issued["id"]), {"reason": "First"}, format="json"
        )
        response = self.client_.post(
            cancel_url(self.issued["id"]), {"reason": "Second"}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "ALREADY_CANCELLED")

    def test_a_draft_cannot_be_cancelled(self):
        draft = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()
        response = self.client_.post(
            cancel_url(draft["id"]), {"reason": "Never issued"}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "NOT_ISSUED")


class WalkInPartyStateTests(TestCase):
    """
    Decision 9: a party with no billing state - above all the auto-created
    "Walk-in / Cash Customer" - is supplied from the business's own state.

    Without this, a walk-in sale is the one invoice type that cannot be created
    at all, because the walk-in party is created before the business state is
    known. Found by the step E contract smoke test.
    """

    def setUp(self):
        self.user = make_user(state_code="36")
        self.client_ = api_client_for(self.user)
        self.item = make_item(self.user)
        # Exactly the shape the walk-in backfill migration creates.
        self.walk_in = Party.objects.create(
            business=self.user.business, name="Walk-in / Cash Customer"
        )
        self.assertEqual(self.walk_in.state_code, "")

    def test_a_walk_in_sale_previews_against_the_business_state(self):
        response = self.client_.post(
            "/api/v1/invoices/preview/",
            {"party": self.walk_in.pk, "items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        self.assertEqual(body["place_of_supply"], "36")
        self.assertEqual(body["supply_type"], "INTRA")
        self.assertEqual(body["state_tax_label"], "SGST")
        self.assertNotEqual(body["totals"]["cgst_total"], "0.00")

    def test_a_walk_in_sale_saves_and_issues(self):
        draft = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.walk_in.pk, "items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content[:300])

        response = self.client_.post(issue_url(draft.json()["id"]), {}, format="json")
        # PARTY_STATE_MISSING still applies to a *named* party with no state, but
        # a walk-in sale is supplied from the business, so it must issue.
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(response.json()["place_of_supply"], "36")

    def test_a_party_state_still_wins_over_the_business_state(self):
        other = make_party(self.user, name="Karnataka Buyer", state_code="29")
        response = self.client_.post(
            "/api/v1/invoices/preview/",
            {"party": other.pk, "items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["place_of_supply"], "29")
        self.assertEqual(response.json()["supply_type"], "INTER")

    def test_nothing_resolvable_still_reports_an_error(self):
        """With no party state AND no business state, the calculator must refuse."""
        self.user.business.state_code = ""
        self.user.business.save(update_fields=["state_code"])
        response = self.client_.post(
            "/api/v1/invoices/preview/",
            {"party": self.walk_in.pk, "items": [line_payload(self.item)]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_a_party_with_no_state_falls_back_to_the_business_state(self):
        """
        `Party` has no `is_walk_in` column, so the schema cannot separate a
        walk-in from a named customer who left their state blank. The only rule
        the data supports - and the one decision 9 asks for - is: fall back to
        the business state. Supplying a customer standing at your counter from
        your own state is correct, and the place of supply stays overridable.
        """
        draft = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.walk_in.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(response.json()["place_of_supply"], "36")

    def test_party_state_missing_is_refused_when_nothing_can_resolve_it(self):
        """With no party state AND no business state, the tax treatment is unknowable."""
        draft = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.walk_in.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()
        # Blank the business state only AFTER the draft exists: an unresolvable
        # place of supply is refused at save time too.
        self.user.business.state_code = ""
        self.user.business.save(update_fields=["state_code"])

        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        # The blank business state is caught first, by the more specific gate.
        self.assertEqual(response.json()["error"], "BUSINESS_PROFILE_INCOMPLETE")

    def test_an_explicit_place_of_supply_still_overrides_the_fallback(self):
        draft = self.client_.post(
            INVOICE_LIST_URL,
            {
                "party": self.walk_in.pk,
                "place_of_supply": "29",
                "items": [line_payload(self.item)],
            },
            format="json",
        ).json()
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(response.json()["place_of_supply"], "29")
        self.assertEqual(response.json()["supply_type"], "INTER")


class FrozenAfterIssueTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)
        self.issued = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()
        self.client_.post(issue_url(self.issued["id"]), {}, format="json")

    def test_an_issued_invoice_cannot_be_edited(self):
        response = self.client_.patch(
            detail_url(self.issued["id"]), {"notes": "changed"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Invoice.objects.get(pk=self.issued["id"]).notes, "")

    def test_an_issued_invoice_cannot_be_fully_replaced(self):
        response = self.client_.put(
            detail_url(self.issued["id"]),
            {"party": self.party.pk, "items": [line_payload(self.item, quantity="9")]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_an_issued_invoice_cannot_be_deleted(self):
        response = self.client_.delete(detail_url(self.issued["id"]))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Invoice.objects.filter(pk=self.issued["id"]).exists())

    def test_an_issued_invoice_cannot_have_its_lines_replaced(self):
        response = self.client_.patch(
            detail_url(self.issued["id"]),
            {"items": [line_payload(self.item, quantity="7")]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            InvoiceItem.objects.get(invoice_id=self.issued["id"]).quantity,
            InvoiceItem.objects.get(invoice_id=self.issued["id"]).quantity,
        )
        self.assertEqual(InvoiceItem.objects.filter(invoice_id=self.issued["id"]).count(), 1)

    def test_a_cancelled_invoice_is_also_frozen(self):
        self.client_.post(
            cancel_url(self.issued["id"]), {"reason": "Void"}, format="json"
        )
        self.assertEqual(
            self.client_.patch(
                detail_url(self.issued["id"]), {"notes": "x"}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(self.client_.delete(detail_url(self.issued["id"])).status_code, 400)


class IssueTenantIsolationTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.rival = make_user("rival@example.com", state_code="29", prefix="RIV")
        self.client_ = api_client_for(self.owner)
        self.party = make_party(self.owner)
        self.item = make_item(self.owner)
        self.draft = self.client_.post(
            INVOICE_LIST_URL,
            {"party": self.party.pk, "items": [line_payload(self.item)]},
            format="json",
        ).json()

    def test_another_tenant_cannot_issue_my_invoice(self):
        response = api_client_for(self.rival).post(
            issue_url(self.draft["id"]), {}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Invoice.objects.get(pk=self.draft["id"]).status, Invoice.Status.DRAFT)

    def test_another_tenant_cannot_cancel_my_invoice(self):
        response = api_client_for(self.rival).post(
            cancel_url(self.draft["id"]), {"reason": "hostile"}, format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_another_tenant_does_not_see_my_next_number(self):
        response = api_client_for(self.rival).get(
            "/api/v1/invoices/next_number/?date=2026-04-01"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["next_number"], "RIV/26-27/00001")

    def test_each_tenant_numbers_independently(self):
        rival_party = make_party(self.rival, name="Their Buyer", state_code="29")
        rival_item = make_item(self.rival, code="R-1", name="Their goods")
        rival_draft = api_client_for(self.rival).post(
            INVOICE_LIST_URL,
            {"party": rival_party.pk, "items": [line_payload(rival_item)]},
            format="json",
        ).json()

        mine = self.client_.post(issue_url(self.draft["id"]), {}, format="json").json()
        theirs = api_client_for(self.rival).post(
            issue_url(rival_draft["id"]), {}, format="json"
        ).json()

        self.assertEqual(mine["invoice_number"], "INV/26-27/00001")
        self.assertEqual(theirs["invoice_number"], "RIV/26-27/00001")


class FinancialYearIssueTests(TestCase):
    def test_financial_year_rollover_on_issue(self):
        user = make_user()
        client_ = api_client_for(user)
        party = make_party(user)
        item = make_item(user)

        numbers = []
        for invoice_date in ("2026-03-31", "2026-04-01", "2027-03-31", "2027-04-01"):
            draft = client_.post(
                INVOICE_LIST_URL,
                {
                    "party": party.pk,
                    "invoice_date": invoice_date,
                    "items": [line_payload(item)],
                },
                format="json",
            ).json()
            numbers.append(
                client_.post(issue_url(draft["id"]), {}, format="json").json()["invoice_number"]
            )

        self.assertEqual(
            numbers,
            [
                "INV/25-26/00001",
                "INV/26-27/00001",
                "INV/26-27/00002",
                "INV/27-28/00001",
            ],
        )

    def test_the_fy_comes_from_the_invoice_date_not_today(self):
        """A backdated invoice belongs to its own financial year."""
        user = make_user()
        client_ = api_client_for(user)
        party = make_party(user)
        item = make_item(user)
        draft = client_.post(
            INVOICE_LIST_URL,
            {
                "party": party.pk,
                "invoice_date": date(2024, 8, 15).isoformat(),
                "items": [line_payload(item)],
            },
            format="json",
        ).json()
        response = client_.post(issue_url(draft["id"]), {}, format="json")
        self.assertEqual(response.json()["invoice_number"], "INV/24-25/00001")
