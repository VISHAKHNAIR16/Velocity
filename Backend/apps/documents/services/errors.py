"""Errors the 2.3 document layer raises.

Each one maps to a distinct HTTP status in the view. Collapsing them into a
single 500 would have been less code and useless in production: "500" does not
tell a user whether they picked the wrong template, or whether the server is
missing a system library.
"""


class DocumentError(Exception):
    """Base for every document-rendering failure."""

    code = "DOCUMENT_ERROR"
    status = 400


class UnknownTemplate(DocumentError):
    code = "UNKNOWN_TEMPLATE"
    status = 400


class DocumentNotRenderable(DocumentError):
    """A draft or cancelled invoice. Only issued invoices are documents."""

    code = "NOT_ISSUED"
    status = 400


class DocumentTooLarge(DocumentError):
    """Refused rather than attempted: WeasyPrint is not linear in row count."""

    code = "DOCUMENT_TOO_LARGE"
    status = 400


class EngineUnavailable(DocumentError):
    """
    WeasyPrint could not be imported - almost always a missing Pango.

    Status 503, not 400: nothing about the request was wrong, the server is
    simply not configured to serve it. The distinction matters because a 503 is
    retryable and a 400 is not, and because it tells a deployer to look at the
    image rather than at the URL.
    """

    code = "PDF_ENGINE_UNAVAILABLE"
    status = 503
