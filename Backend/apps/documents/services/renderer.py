"""
Invoice PDF rendering (2.3).

Two rules shape this file.

**1. WeasyPrint is imported lazily, inside the functions.** Not at module
level, and not for tidiness. WeasyPrint binds to Pango through CFFI at import
time, so on any machine without that system library the import raises. The
original plan assumed every developer machine lacked it; measurement then showed
this one has it. A module-level import would still be wrong, because the suite
has to run on whatever machine picks it up next - a colleague's fresh clone, a
CI container without the apt packages, a laptop that has not been through MSYS2.
Lazy import turns "the whole app will not start" into "one endpoint explains
itself", which is the difference between a five-minute fix and a five-hour one.

**2. Nothing is stored.** The PDF is generated on demand from the invoice's own
frozen snapshots. No `pdf_file_url`, no cleanup job, no drift between the file
and the row. A reprint a decade from now shows today's numbers forever.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache
from django.template.loader import render_to_string

from .context import build_invoice_context
from .errors import DocumentNotRenderable, DocumentTooLarge, UnknownTemplate

#: How long a rendered PDF is cached. An issued invoice is immutable - its
#: money fields are locked and its letterhead is frozen - so the output for a
#: given (invoice, template) pair cannot change. The cache exists only to avoid
#: re-running a slow, memory-hungry render on every repeat download.
CACHE_SECONDS = 60 * 60 * 24  # one day

#: Guard against a pathological invoice. WeasyPrint is not linear in the number
#: of rows and will happily spend minutes on a pathological one.
MAX_LINES = 2000


@dataclass(frozen=True)
class TemplateInfo:
    """One selectable layout."""

    key: str
    label: str
    description: str
    filename: str


#: The registry. Adding a third template means adding one entry here and one
#: HTML file - no branching anywhere else in the codebase.
TEMPLATES: dict[str, TemplateInfo] = {
    "classic": TemplateInfo(
        key="classic",
        label="Classic",
        description="Bordered table with a header band. Formal, for B2B and filing.",
        filename="Invoice_Templates/invoice_classic.html",
    ),
    "modern": TemplateInfo(
        key="modern",
        label="Modern",
        description="Hairline rules and generous whitespace. Clean, for retail.",
        filename="Invoice_Templates/invoice_modern.html",
    ),
}

DEFAULT_TEMPLATE = "classic"


def get_template(key: str) -> TemplateInfo:
    """Look up a template or raise `UnknownTemplate`. Never KeyError."""
    try:
        return TEMPLATES[key]
    except KeyError:
        raise UnknownTemplate(
            f"'{key}' is not a template. Choose one of: {', '.join(sorted(TEMPLATES))}."
        ) from None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@dataclass
class EngineStatus:
    """Why the PDF engine can or cannot be used, in a form worth logging."""

    available: bool
    version: str = ""
    detail: str = ""
    #: Pango/Cairo are what WeasyPrint actually binds to; reporting the version
    #: tells a deployer immediately whether the apt packages landed.
    pango_version: str = ""


def engine_status() -> EngineStatus:
    """
    Probe the PDF engine without raising.

    Called by a management command and a diagnostics endpoint so that "the PDF
    button does nothing" becomes a sentence with a cause attached, rather than
    a stack trace nobody can act on.
    """
    try:
        import weasyprint  # noqa: PLC0415 - deliberately lazy, see module docstring
    except Exception as exc:  # ImportError, or CFFI/AttributeError from Pango
        return EngineStatus(
            available=False,
            detail=f"{type(exc).__name__}: {exc}".strip(),
        )

    pango = ""
    try:
        from weasyprint.text.ffi import ffi  # noqa: PLC0415
        from weasyprint.text.ffi import pango as pango_ffi

        pango = pango_ffi.pango_version_string().decode() if pango_ffi else ""
        del ffi
    except Exception:  # pragma: no cover - diagnostics only
        pango = "unknown"

    return EngineStatus(
        available=True,
        version=getattr(weasyprint, "__version__", "unknown"),
        pango_version=pango,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _cache_key(invoice, template_key: str) -> str:
    """
    Cache key that cannot serve a stale document.

    The invoice id alone is not enough: an invoice's rows can change while it
    is still a draft, and a PDF cached from a draft must not resurface after it
    is issued. Including the status and the row count means any state the PDF
    depends on invalidates the entry.
    """
    return f"pdf:invoice:{invoice.pk}:{template_key}:{invoice.status}:{invoice.items.count()}"


def render_invoice_pdf(
    invoice, template_key: str = DEFAULT_TEMPLATE, *, use_cache: bool = True
) -> bytes:
    """
    Render one issued invoice to PDF bytes.

    Raises `UnknownTemplate` for a bad key and `EngineUnavailable` when Pango is
    missing, so the view can answer 400 and 503 respectively rather than
    collapsing both into a 500.
    """
    template = get_template(template_key)

    if invoice.status != "ISSUED":
        # Drafts and cancellations are refused here rather than in the view, so
        # that every future caller inherits the same rule. A cancellation is
        # excluded too: a cancelled document is not a document anyone should be
        # able to present as valid.
        raise DocumentNotRenderable(
            "Only issued invoices can be downloaded as a PDF."
            f" This one is {invoice.get_status_display().lower()}."
        )

    if invoice.items.count() > MAX_LINES:
        raise DocumentTooLarge(
            f"This invoice has {invoice.items.count()} lines, over the "
            f"{MAX_LINES}-line limit for PDF generation."
        )

    key = _cache_key(invoice, template.key)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached

    from .engine import render_html_to_pdf  # noqa: PLC0415 - lazy by design

    context = build_invoice_context(invoice)
    html = render_to_string(template.filename, context, request=None)
    pdf = render_html_to_pdf(html, base_url=str(invoice.business_logo_url or ""))

    if use_cache:
        _cache_set(key, pdf)
    return pdf


# The cache is backed by a database table. That is the right choice for a
# multi-instance deployment, but it introduces a failure mode the rest of the
# app does not have: if the table is missing or the database hiccups, every PDF
# request would 500 because of a *speed optimisation*. A cache that breaks the
# feature it exists to accelerate is worse than no cache, so both helpers
# swallow their errors and degrade to "not cached".


def _cache_get(key: str) -> bytes | None:
    try:
        return cache.get(key)
    except Exception:
        return None


def _cache_set(key: str, value: bytes) -> None:
    try:
        cache.set(key, value, CACHE_SECONDS)
    except Exception:
        pass


def clear_invoice_pdf_cache(invoice, template_key: str | None = None) -> None:
    """
    Forget a cached PDF.

    Called whenever an invoice changes. Cheap insurance for the day a field
    that the PDF shows turns out to be editable after all.
    """
    keys = (
        [_cache_key(invoice, k) for k in TEMPLATES]
        if template_key is None
        else [_cache_key(invoice, template_key)]
    )
    try:
        cache.delete_many(keys)
    except Exception:
        pass


__all__ = [
    "DEFAULT_TEMPLATE",
    "EngineStatus",
    "TEMPLATES",
    "TemplateInfo",
    "build_invoice_context",
    "clear_invoice_pdf_cache",
    "engine_status",
    "get_template",
    "render_invoice_pdf",
]
