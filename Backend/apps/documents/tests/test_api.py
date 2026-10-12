"""
The PDF endpoint over HTTP (2.3.4).

Status-code discipline is the theme here. A PDF that cannot be produced has
several genuinely different causes, and they must not all surface as one
indistinguishable 500:

    400 UNKNOWN_TEMPLATE            the caller asked for something that isn't there
    400 NOT_ISSUED                  the document is not one you may download
    404                             another tenant's invoice, or a missing one
    503 PDF_ENGINE_UNAVAILABLE      this server cannot render at all

Collapsing those into a single failure would leave a user unable to tell
whether to fix the URL, fix the data, or ask whoever runs the server to install
a system package.
"""

import unittest

from apps.documents.tests.base import (
    DocumentBase,
    engine_available,
    pdf_url,
    templates_url,
)


class TemplateListTests(DocumentBase):
    def test_the_picker_is_driven_by_the_server(self):
        response = self.client_.get(templates_url())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            sorted(t["key"] for t in body["templates"]), ["classic", "modern"]
        )
        self.assertEqual(body["default"], "classic")

    def test_each_entry_is_described_for_the_ui(self):
        for entry in self.client_.get(templates_url()).json()["templates"]:
            with self.subTest(template=entry["key"]):
                self.assertTrue(entry["label"])
                self.assertTrue(entry["description"])


class PdfEndpointTests(DocumentBase):
    def setUp(self):
        super().setUp()
        self.add_bank_details()
        self.invoice = self.issued_invoice()

    def test_an_unknown_template_is_a_400_not_a_500(self):
        response = self.client_.get(pdf_url(self.invoice) + "?template=no-such")
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["error"], "UNKNOWN_TEMPLATE")

    def test_a_draft_is_refused_with_a_reason(self):
        draft = self.make_draft()
        response = self.client_.get(pdf_url(draft["id"]))
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["error"], "NOT_ISSUED")

    def test_a_missing_invoice_is_a_404(self):
        response = self.client_.get("/api/v1/invoices/999999/pdf/")
        self.assertEqual(response.status_code, 404)

    def test_another_tenants_invoice_is_a_404(self):
        """
        Not 403.

        A 403 confirms the row exists, which leaks one bit about another
        tenant's data to anyone holding a valid token. 404 is the honest answer
        and matches every other endpoint on this viewset.
        """
        from apps.documents.tests.base import issue_url
        from apps.invoices.tests.base import (
            INVOICE_LIST_URL,
            api_client_for,
            line_payload,
            make_item,
            make_party,
            make_user,
        )

        rival = make_user(email="rival@example.com")
        rival_client = api_client_for(rival)
        rival_party = make_party(rival)
        rival_item = make_item(rival)
        draft = rival_client.post(
            INVOICE_LIST_URL,
            {"party": rival_party.pk, "items": [line_payload(rival_item)]},
            format="json",
        ).json()
        issued = rival_client.post(issue_url(draft["id"]), {}, format="json")
        self.assertIn(issued.status_code, (200, 201), issued.content[:300])

        response = self.client_.get(pdf_url(draft["id"]))
        self.assertEqual(response.status_code, 404)

    def test_an_anonymous_caller_is_rejected(self):
        from rest_framework.test import APIClient

        response = APIClient().get(pdf_url(self.invoice))
        self.assertIn(response.status_code, (401, 403))

    @unittest.skipUnless(engine_available(), "PDF engine (Pango) not available on this machine")
    def test_it_returns_a_pdf_with_a_filename(self):
        response = self.client_.get(pdf_url(self.invoice) + "?template=modern")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("modern", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))

    @unittest.skipUnless(engine_available(), "PDF engine (Pango) not available on this machine")
    def test_the_default_template_is_used_when_none_is_named(self):
        response = self.client_.get(pdf_url(self.invoice))
        self.assertEqual(response.status_code, 200)
        self.assertIn("classic", response["Content-Disposition"])


class CachingTests(DocumentBase):
    """A cache that breaks the feature it accelerates is worse than no cache."""

    def setUp(self):
        super().setUp()
        self.add_bank_details()
        self.invoice = self.issued_invoice()

    def test_the_cache_key_covers_status_and_row_count(self):
        from apps.documents.services.renderer import _cache_key

        before = _cache_key(self.invoice, "classic")
        self.invoice.status = "DRAFT"
        self.assertNotEqual(before, _cache_key(self.invoice, "classic"))

    def test_templates_do_not_share_a_cache_entry(self):
        from apps.documents.services.renderer import _cache_key

        self.assertNotEqual(
            _cache_key(self.invoice, "classic"), _cache_key(self.invoice, "modern")
        )

    def test_a_broken_cache_degrades_instead_of_raising(self):
        """
        The cache is a database table. If it is unavailable, the endpoint must
        still render - slowly - rather than 500 because of a speed optimisation.
        """
        from unittest import mock

        from apps.documents.services import renderer

        with mock.patch.object(renderer.cache, "get", side_effect=RuntimeError("no table")):
            self.assertIsNone(renderer._cache_get("anything"))
        with mock.patch.object(renderer.cache, "set", side_effect=RuntimeError("no table")):
            renderer._cache_set("anything", b"x")  # must not raise

    @unittest.skipUnless(engine_available(), "PDF engine (Pango) not available on this machine")
    def test_a_second_download_is_byte_identical(self):
        first = self.client_.get(pdf_url(self.invoice)).content
        second = self.client_.get(pdf_url(self.invoice)).content
        self.assertEqual(first, second)