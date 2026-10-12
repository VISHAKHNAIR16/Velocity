"""
Reports whether this machine can actually render PDFs, and why not if it cannot.

Exists because "the Download PDF button does nothing" is the least debuggable
sentence in the whole application. WeasyPrint's most common failure - Pango
missing - surfaces as a CFFI error at import time that mentions none of the
system packages you needed to install. This command prints the answer directly,
so the fix does not have to be guessed.

Run it on the server:

    python manage.py check_pdf_engine

Exit codes: 0 ready, 1 engine missing, 2 usable but no rupee glyph.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check whether PDF generation is available, and diagnose it if not."

    def handle(self, *args, **options):
        from apps.documents.services.engine import rupee_glyph_available
        from apps.documents.services.renderer import TEMPLATES, engine_status

        status = engine_status()
        self.stdout.write("")

        if not status.available:
            self.stdout.write(self.style.ERROR("PDF ENGINE: UNAVAILABLE"))
            self.stdout.write(f"  reason : {status.detail}")
            self.stdout.write("")
            self.stdout.write("  WeasyPrint needs Pango and Cairo as SYSTEM libraries;")
            self.stdout.write("  they cannot be pip-installed. Either:")
            self.stdout.write("")
            self.stdout.write("    a) switch this Render service's Runtime to Docker")
            self.stdout.write("       (the repository Dockerfile installs them), or")
            self.stdout.write("    b) install them on the host image")
            self.stdout.write("")
            self.stdout.write("  Details: https://doc.courtbouillon.org/weasyprint/stable/first_steps.html")
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS("PDF ENGINE: AVAILABLE"))
        self.stdout.write(f"  weasyprint : {status.version}")
        self.stdout.write(f"  pango      : {status.pango_version or 'unknown'}")
        self.stdout.write(f"  templates  : {', '.join(sorted(TEMPLATES))}")

        if rupee_glyph_available():
            self.stdout.write("  rupee sign : glyph U+20B9 present in at least one installed font")
            self.stdout.write("")
            return

        # Not fatal on its own - a business could get away without the sign -
        # but a boxed-out grand total is the single worst thing that can happen
        # to a tax invoice, so it is reported as a distinct failure.
        self.stdout.write(self.style.ERROR("  rupee sign : NO installed font has the U+20B9 glyph"))
        self.stdout.write("")
        self.stdout.write("  The grand total would print as a box. Install a font that has it:")
        self.stdout.write("    Debian/Ubuntu : apt install fonts-dejavu-core fonts-noto-core")
        self.stdout.write("")
        raise SystemExit(2)