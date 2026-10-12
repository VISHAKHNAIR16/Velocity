"""
Shared setup for the 2.3 document tests.

Built on the invoices app's factories rather than re-declaring them, so a
document test and an invoicing test describing "the same invoice" cannot drift
into describing different ones.

Everything here goes through the public HTTP surface with literal URLs, per the
project convention (1.3.7): a routing mistake should fail a test rather than
hide behind `reverse()`.
"""

from django.test import TestCase

from apps.invoices.tests.base import (
    INVOICE_LIST_URL,
    api_client_for,
    line_payload,
    make_item,
    make_party,
    make_user,
)


def issue_url(invoice) -> str:
    pk = invoice.pk if hasattr(invoice, "pk") else invoice
    return f"/api/v1/invoices/{pk}/issue/"


def pdf_url(invoice) -> str:
    pk = invoice.pk if hasattr(invoice, "pk") else invoice
    return f"/api/v1/invoices/{pk}/pdf/"


def templates_url() -> str:
    return "/api/v1/invoices/pdf-templates/"


def engine_available() -> bool:
    """
    Whether this machine can render PDFs at all.

    Every test that actually invokes WeasyPrint is gated on this. Without the
    gate, a missing Pango turns one machine's suite red while CI - which has
    Pango - stays green, and the two disagree for no reason a reader can see.
    Mirrors `backend_supports_row_locks()` in the concurrency tests.
    """
    from apps.documents.services.renderer import engine_status

    return engine_status().available


class DocumentBase(TestCase):
    """An issued invoice, ready to render."""

    def setUp(self):
        self.user = make_user()
        self.client_ = api_client_for(self.user)
        self.party = make_party(self.user)
        self.item = make_item(self.user)

    def make_draft(self, **payload) -> dict:
        body = {"party": self.party.pk, "items": [line_payload(self.item)]}
        body.update(payload)
        response = self.client_.post(INVOICE_LIST_URL, body, format="json")
        self.assertEqual(response.status_code, 201, response.content[:400])
        return response.json()

    def issue(self, draft) -> dict:
        """
        Issue a draft and return the issued body.

        Takes the draft dict but passes its `id` on: `issue_url` reads `.pk` or
        treats its argument as a pk, so handing it the whole dict builds a URL
        containing `{'party': 1, ...}`. The router matches that as a pk just
        fine and answers 404, which is a genuinely confusing way to fail.
        """
        response = self.client_.post(issue_url(draft["id"]), {}, format="json")
        # 201, not 200: issuing creates the numbered document.
        self.assertEqual(response.status_code, 201, response.content[:400])
        return response.json()

    def latest_invoice(self):
        from apps.invoices.models import Invoice

        return Invoice.objects.order_by("-id").first()

    def issued_invoice(self, **payload):
        """A draft taken all the way to ISSUED, as a model instance."""
        self.issue(self.make_draft(**payload))
        return self.latest_invoice()

    def add_bank_details(self, **overrides):
        """Fill in the business profile so the letterhead has something to print."""
        profile = self.user.business
        fields = {
            "bank_account_name": "Velocity Traders",
            "bank_account_number": "50100234567890",
            "bank_ifsc": "HDFC0001234",
            "bank_name": "HDFC Bank",
            "bank_branch": "Hyderabad Main",
        }
        for field, value in fields.items():
            setattr(profile, field, overrides.get(field, value))
        profile.save()
        return profile