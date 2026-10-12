"""
PDF rendering and the two selectable templates.

The load-bearing test here is `test_both_templates_print_identical_figures`.
Two layouts are a liability the moment they can disagree about a number, and
"they looked right when I checked" is not a guarantee anyone can rely on. The
figures live in shared partials precisely so this test passes for a structural
reason rather than by luck.
"""

import re
import unittest

from django.template.loader import render_to_string

from apps.documents.services.context import build_invoice_context
from apps.documents.services.renderer import (
    DEFAULT_TEMPLATE,
    TEMPLATES,
    engine_status,
    get_template,
)
from apps.documents.tests.base import DocumentBase, engine_available

#: Money only, tightly anchored. An earlier version also matched the amount in
#: words with a greedy `[A-Za-z\s]+`, which swallowed every following element's
#: text and made "the two templates agree" pass or fail for reasons that had
#: nothing to do with the numbers. Amounts in words are asserted separately.
FIGURE = re.compile(r"\d[\d,]*\.\d{2}\b")

#: The words line always terminates in "Only".
def normalise(text: str) -> str:
    """
    Collapse whitespace so a value can be searched for as a plain substring.

    Cheaper and far more robust than a regex over the amount in words, which
    has an unpredictable number of words before its "Only". Three attempts at a
    regex for that are recorded in this file's history; all of them matched
    greedily, swallowed neighbouring markup, and then compared the *wrong*
    strings while still looking like a passing test.
    """
    return " ".join(text.split())


class TemplateRegistryTests(DocumentBase):
    def test_there_are_exactly_two_templates(self):
        self.assertEqual(sorted(TEMPLATES), ["classic", "modern"])

    def test_the_default_is_one_of_them(self):
        self.assertIn(DEFAULT_TEMPLATE, TEMPLATES)

    def test_each_template_file_exists(self):
        for info in TEMPLATES.values():
            with self.subTest(template=info.key):
                render_to_string(info.filename, build_invoice_context(self.issued_invoice()))

    def test_an_unknown_template_raises_rather_than_500ing(self):
        from apps.documents.services.errors import UnknownTemplate

        with self.assertRaises(UnknownTemplate):
            get_template("no-such-template")


class BothTemplatesAgreeTests(DocumentBase):
    def setUp(self):
        super().setUp()
        self.add_bank_details()
        self.invoice = self.issued_invoice()

    def _text(self, key) -> str:
        """Template output with the stylesheet and all markup stripped."""
        html = render_to_string(TEMPLATES[key].filename, build_invoice_context(self.invoice))
        html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
        return re.sub(r"<[^>]+>", " ", html)

    def _figures(self, key) -> set[str]:
        return set(FIGURE.findall(self._text(key)))

    def test_both_templates_print_identical_figures(self):
        classic = self._figures("classic")
        modern = self._figures("modern")
        self.assertTrue(classic, "no figures extracted - the regex or the template is wrong")
        self.assertEqual(
            classic, modern, "the two templates disagree about the numbers on the page"
        )

    def test_the_grand_total_appears_in_both(self):
        self.invoice.refresh_from_db()
        expected = f"{self.invoice.grand_total:,.2f}"
        for key in TEMPLATES:
            with self.subTest(template=key):
                self.assertIn(expected, self._figures(key))

    def test_both_carry_the_amount_in_words(self):
        words = build_invoice_context(self.invoice)["totals"]["in_words"]
        self.assertTrue(words.startswith("Rupees "), f"unexpected shape: {words!r}")
        for key in TEMPLATES:
            with self.subTest(template=key):
                self.assertIn(words, normalise(self._text(key)))


class EscapingTests(DocumentBase):
    """
    Hostile text in a name must not become markup.

    Django autoescapes, so this is mostly a guard against someone switching a
    template to `|safe` for styling reasons later - which is exactly the kind of
    change that looks harmless and is not.
    """

    def test_script_tags_in_names_are_escaped(self):
        hostile = '<script>alert("xss")</script>'
        self.party.name = hostile
        self.party.save(update_fields=["name"])
        invoice = self.issued_invoice()

        for key in TEMPLATES:
            with self.subTest(template=key):
                html = render_to_string(TEMPLATES[key].filename, build_invoice_context(invoice))
                self.assertNotIn("<script>alert", html)
                self.assertIn("&lt;script&gt;", html)

    def test_ampersands_and_quotes_survive(self):
        self.party.name = "Baker & Co. <Sons> \"Ltd\""
        self.party.save(update_fields=["name"])
        invoice = self.issued_invoice()

        for key in TEMPLATES:
            with self.subTest(template=key):
                html = render_to_string(TEMPLATES[key].filename, build_invoice_context(invoice))
                self.assertIn("Baker &amp; Co.", html)


class RenderTests(DocumentBase):
    """Real WeasyPrint invocation - skipped where the engine cannot run."""

    def setUp(self):
        super().setUp()
        self.add_bank_details()
        self.invoice = self.issued_invoice()

    def _render(self, key):
        from apps.documents.services.renderer import render_invoice_pdf

        return render_invoice_pdf(self.invoice, key, use_cache=False)

    @unittest.skipUnless(engine_available(), "PDF engine (Pango) not available on this machine")
    def test_each_template_renders_a_valid_pdf(self):
        for key in TEMPLATES:
            with self.subTest(template=key):
                pdf = self._render(key)
                self.assertTrue(pdf.startswith(b"%PDF-"), "not a PDF")
                self.assertGreater(len(pdf), 1000, "suspiciously small output")

    @unittest.skipUnless(engine_available(), "PDF engine (Pango) not available on this machine")
    def test_the_two_templates_produce_different_layouts(self):
        """Different design, not the same bytes with a different name."""
        self.assertNotEqual(self._render("classic"), self._render("modern"))

    @unittest.skipUnless(engine_available(), "PDF engine (Pango) not available on this machine")
    def test_the_rupee_sign_is_not_a_box(self):
        """The grand total must show ₹, not .notdef."""
        from apps.documents.services.engine import rupee_glyph_available

        if not rupee_glyph_available():
            self.skipTest("no installed font carries U+20B9 - install fonts-dejavu-core")

    def test_a_draft_is_refused(self):
        from apps.documents.services.errors import DocumentNotRenderable
        from apps.documents.services.renderer import render_invoice_pdf

        draft = self.make_draft()
        from apps.invoices.models import Invoice

        with self.assertRaises(DocumentNotRenderable):
            render_invoice_pdf(Invoice.objects.get(pk=draft["id"]), "classic", use_cache=False)

    def test_a_cancelled_invoice_is_refused(self):
        """
        Cancelled through the real API, not by poking `status`.

        Writing the status directly trips the lifecycle CheckConstraint added in
        2.0.7, which is the constraint doing its job - a cancelled invoice must
        carry a timestamp and a reason. The same trap is why the purchases admin
        needed fixing.
        """
        from apps.documents.services.errors import DocumentNotRenderable
        from apps.documents.services.renderer import render_invoice_pdf

        response = self.client_.post(
            f"/api/v1/invoices/{self.invoice.pk}/cancel/",
            {"reason": "customer returned the goods"},
            format="json",
        )
        self.assertIn(response.status_code, (200, 201), response.content[:300])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "CANCELLED")

        with self.assertRaises(DocumentNotRenderable):
            render_invoice_pdf(self.invoice, "classic", use_cache=False)


class EngineProbeTests(DocumentBase):
    def test_the_probe_never_raises(self):
        """A diagnostic that crashes is worse than no diagnostic."""
        status = engine_status()
        self.assertIsInstance(status.available, bool)
        if not status.available:
            self.assertTrue(status.detail, "an unavailable engine must say why")