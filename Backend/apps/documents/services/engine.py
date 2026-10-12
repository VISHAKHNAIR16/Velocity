"""
The thin seam around WeasyPrint.

This module exists so that swapping the engine is a one-file change. Every
other file in `apps.documents` talks to `render_html_to_pdf` and never imports
WeasyPrint directly, because the engine choice was the largest open risk in
2.3: WeasyPrint needs Pango and Cairo as system libraries, which not every host
provides. If that assumption turns out to be wrong on some deployment, this is
the only file that has to be rewritten.

It also keeps the import lazy. Importing at module level would make every
Django start-up depend on a system library that may not be installed, turning
a missing apt package into an application that cannot boot at all.
"""

from __future__ import annotations

from .errors import EngineUnavailable

#: Kept generous. A cold Render instance plus a 150-line invoice is not fast,
#: and a timeout that fires late is far better than one that fires early.
DEFAULT_TIMEOUT = 30


def render_html_to_pdf(html: str, *, base_url: str = "", timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """
    Turn rendered HTML into PDF bytes.

    `base_url` is what relative URLs resolve against. Our templates carry
    absolute image URLs (snapshotted onto the invoice), so it is a courtesy for
    local-file development rather than a requirement in production.
    """
    try:
        from weasyprint import HTML  # noqa: PLC0415 - lazy on purpose
    except Exception as exc:  # ImportError, or CFFI failing to find Pango
        raise EngineUnavailable(
            "PDF generation is not available on this server "
            f"({type(exc).__name__}). The deployment is missing the Pango "
            "system libraries WeasyPrint needs; switch the service to the "
            "Docker runtime, which installs them."
        ) from exc

    try:
        return HTML(string=html, base_url=base_url or None).write_pdf(timeout=timeout)
    except EngineUnavailable:
        raise
    except Exception as exc:  # pragma: no cover - rendering faults are engine-specific
        raise EngineUnavailable(f"PDF rendering failed: {type(exc).__name__}: {exc}") from exc


def rupee_glyph_available() -> bool:
    """
    Whether the grand total would render as a rupee sign, or as a box.

    Helvetica has no U+20B9, and a PDF that prints an empty box where the total
    should be is the single worst thing that can happen to a tax invoice.

    Tested by rendering rather than by inspecting font tables, because the real
    question is not "does a font on this box have the glyph" but "does *this*
    render draw it". WeasyPrint 70 exposes `font_map` as a raw CFFI pointer
    that cannot be iterated from Python, so the introspection route is not
    available anyway.

    The trick: render the rupee sign and a codepoint that is guaranteed absent
    (U+E000, private use). If the two PDFs come out byte-identical then both
    drew the same missing-glyph box, so the rupee is missing. If they differ,
    the rupee really was drawn.
    """
    template = (
        '<html><body><p style="font-size:40pt">%s</p></body></html>'
    )
    try:
        from weasyprint import HTML  # noqa: PLC0415

        with_rupee = HTML(string=template % "&#8377;").write_pdf()
        without_rupee = HTML(string=template % "&#xE000;").write_pdf()
    except Exception:
        return False
    return with_rupee != without_rupee
