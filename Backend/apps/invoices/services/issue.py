"""
The invoice lifecycle: DRAFT -> ISSUED -> CANCELLED (step D).

Two rules shape this module.

**Lock order is always invoice -> counter.** Locking only the counter lets two
concurrent issues both observe `status == DRAFT` and both allocate a number, so
the invoice lock is what serialises them. Reversing the order brings that bug
back; the assertion at the top of `issue_invoice()` exists to stop it.

**Issue is one transaction.** Number allocation, recalculation, snapshotting,
lifecycle stamping and (from 2.1) stock deduction all commit together, so a
half-issued invoice cannot exist.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import HsnRequirement
from apps.core.constants import is_hsn, is_sac
from apps.inventory.models import Item

from ..models import Invoice
from .draft import apply_totals, compute_totals, resolve_place_of_supply
from .gst_calculator import GSTCalculationError
from .numbering import NumberingError, allocate_invoice_number


class IssueError(Exception):
    """A gate refused the issue. `code` is machine-readable."""

    def __init__(self, code: str, message: str, status: int = 400, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.field = field


class InvoiceConflict(IssueError):
    """The invoice is not in a state that allows the requested transition."""

    def __init__(self, message: str, code: str = "INVALID_STATE", status: int = 409):
        super().__init__(code, message, status=status)


# ---------------------------------------------------------------------------
# Issue-time gates
# ---------------------------------------------------------------------------
def check_issue_gates(invoice: Invoice) -> None:
    """
    Refuse to issue an invoice that would not survive an audit.

    Raises `IssueError` with a machine-readable code for each failure.
    """
    business = invoice.business
    party = invoice.party

    if not business.state_code:
        raise IssueError(
            "BUSINESS_PROFILE_INCOMPLETE",
            "Set your business state on the profile before issuing an invoice.",
            field="business",
        )

    if not resolve_place_of_supply(party, list(invoice.items.all()), business):
        # Defence in depth. With the business-state fallback added for decision 9
        # (a party with no state is supplied from the business), this is currently
        # unreachable: the business-state check above already refuses that case,
        # which is why the API reports BUSINESS_PROFILE_INCOMPLETE rather than
        # PARTY_STATE_MISSING. Kept because it is the true statement of what is
        # required - an unresolvable place of supply is a wrong-tax risk - and it
        # guards a future change that adds a new way for resolution to fail.
        raise IssueError(
            "PARTY_STATE_MISSING",
            f"{party.name} has no billing state, and your business profile has no "
            "state either. One of them decides CGST/SGST vs IGST, so set it "
            "before issuing.",
            field="party",
        )

    # HSN/SAC gate.
    if not _hsn_gate_applies(business, party, invoice):
        return

    if not invoice.items.exists():
        raise IssueError(
            "INVOICE_EMPTY",
            "An empty invoice cannot be issued. Add at least one line.",
            field="items",
        )
    for line in invoice.items.all():
        if not line.hsn_sac_code:
            raise IssueError(
                "HSN_REQUIRED",
                f"'{line.item_name}' has no HSN/SAC code. It is mandatory on "
                "every line of a tax invoice to a GSTIN holder.",
                field="items",
            )
        is_valid = (
            is_sac(line.hsn_sac_code)
            if line.item_type == Item.ItemType.SERVICE
            else is_hsn(line.hsn_sac_code)
        )
        if not is_valid:
            kind = "SAC" if line.item_type == Item.ItemType.SERVICE else "HSN"
            raise IssueError(
                "HSN_INVALID",
                f"'{line.item_name}' has an invalid {kind} code "
                f"'{line.hsn_sac_code}'.",
                field="items",
            )


#: GST Notification 12/2017-CT: HSN is required on a B2B invoice only when the
#: invoice value exceeds Rs 5,000. See `BusinessProfile.hsn_requirement`.
STATUTORY_HSN_THRESHOLD = Decimal("5000.00")


def _hsn_gate_applies(business, party, invoice) -> bool:
    """
    Whether this invoice must carry an HSN/SAC on every line.

    Two modes, chosen per business:

    * `STRICT` (default) - always, for a REGULAR business. Deliberately stricter
      than the statute: over-complying is recoverable, under-complying is not.
    * `STATUTORY` - only for a B2B invoice above Rs 5,000, i.e. when the
      recipient has a GSTIN and the total exceeds the threshold.

    A non-REGULAR business issues a Bill of Supply, which carries no HSN
    requirement, so the gate never applies to one.
    """
    if business.gst_registration_type != business.GstRegistrationType.REGULAR:
        return False

    if business.hsn_requirement == HsnRequirement.STRICT:
        return True

    # STATUTORY
    if not party.gstin:
        return False
    return invoice.grand_total > STATUTORY_HSN_THRESHOLD


# ---------------------------------------------------------------------------
# Issue
# ---------------------------------------------------------------------------
@transaction.atomic
def issue_invoice(invoice_id: int, *, user=None) -> tuple[Invoice, bool]:
    """
    Issue a draft. Returns `(invoice, already_issued)`.

    Idempotent: issuing an already-ISSUED invoice returns it unchanged with
    `already_issued=True` and **does not burn a second number** - a double-click
    or a retried request must never create a gap in the series.
    """
    # 1. Lock the INVOICE row first. Always before the counter - see module docstring.
    invoice = (
        Invoice.objects.select_for_update()
        .select_related("party", "business")
        .filter(pk=invoice_id)
        .first()
    )
    if invoice is None:
        raise InvoiceConflict("That invoice does not exist.", code="NOT_FOUND", status=404)

    if invoice.status == Invoice.Status.ISSUED:
        return invoice, True
    if invoice.status == Invoice.Status.CANCELLED:
        raise InvoiceConflict(
            f"Invoice {invoice.display_number} was cancelled and cannot be issued. "
            "Copy it as a new draft instead.",
            code="ALREADY_CANCELLED",
        )

    # 2. Gates. Checked before any number is consumed.
    check_issue_gates(invoice)

    # 3. Recalculate from the STORED line inputs - never from today's item
    #    master. The catalogue may have been re-priced since the draft was made;
    #    what the customer agreed to is what is on the stored lines.
    try:
        totals = compute_totals(
            business=invoice.business,
            party=invoice.party,
            lines=list(invoice.items.all()),
            place_of_supply=invoice.place_of_supply,
            invoice_discount=invoice.invoice_discount,
            prices_include_tax=invoice.prices_include_tax,
            round_invoice_total=invoice.business.round_invoice_total,
        )
    except GSTCalculationError as exc:
        raise IssueError("CALCULATION_FAILED", exc.message, field=exc.field or "") from exc
    apply_totals(invoice, totals)

    # 4. Freeze the party and business onto the invoice. From here on the
    #    document stands on its own even if the party is renamed or deleted.
    _snapshot_parties(invoice)

    # 5. Allocate the number from this business's FY counter.
    try:
        invoice.invoice_number = allocate_invoice_number(
            invoice.business, invoice.invoice_date
        )
    except NumberingError as exc:
        raise IssueError(exc.code, exc.message, status=exc.status) from exc

    invoice.status = Invoice.Status.ISSUED
    invoice.issued_at = timezone.now()
    invoice.issued_by = user if (user and user.is_authenticated) else None

    try:
        invoice.save(
            update_fields=[
                "invoice_number", "status", "issued_at", "issued_by",
                "recipient_name", "recipient_gstin", "recipient_state_code",
                "recipient_address", "shipping_address",
                "business_name", "business_address", "business_gstin",
                "business_state_code", "document_title", "place_of_supply",
                "supply_type", "subtotal", "total_discount", "taxable_total",
                "cgst_total", "sgst_total", "igst_total", "round_off",
                "grand_total", "updated_at",
            ]
        )
    except IntegrityError as exc:
        # The (business, invoice_number) unique constraint fired despite the
        # row locks. Surface it as a clean 409, never a 500.
        raise InvoiceConflict(
            "That invoice number was just allocated to another session - "
            "please reload.",
            code="NUMBER_RACE",
        ) from exc

    # 6. Stock deduction slots in here, inside the same transaction (step 2.1),
    #    skipping free-text lines and SERVICE lines.
    _deduct_stock_for_issue(invoice)

    return invoice, False


def _snapshot_parties(invoice: Invoice) -> None:
    """Freeze who the invoice is for and who issued it."""
    party, business = invoice.party, invoice.business

    invoice.recipient_name = party.name
    invoice.recipient_gstin = party.gstin or ""
    invoice.recipient_state_code = party.state_code or ""
    invoice.recipient_address = _compose_address(
        party.billing_address, party.billing_city, party.billing_pincode
    )
    invoice.shipping_address = _compose_address(
        party.shipping_address or party.billing_address,
        party.shipping_city or party.billing_city,
        party.shipping_pincode or party.billing_pincode,
    )

    invoice.business_name = business.company_name or business.trade_name
    invoice.business_address = _compose_address(
        business.address_line, business.city, business.pincode
    )
    invoice.business_gstin = business.gstin or ""
    invoice.business_state_code = business.state_code or ""
    # A non-REGULAR business issues a Bill of Supply, never a Tax Invoice.
    invoice.document_title = (
        "Bill of Supply"
        if business.gst_registration_type != business.GstRegistrationType.REGULAR
        else "Tax Invoice"
    )


def _compose_address(line, city, pincode) -> str:
    parts = [p for p in (line, city, pincode) if p]
    return ", ".join(str(p) for p in parts)


def _deduct_stock_for_issue(invoice: Invoice) -> None:
    """
    Seam for stock deduction (step 2.1).

    Intentionally a no-op in 1.4: `tasks.md` 1.4.6 defers the deduction itself,
    but requires it to be placed *inside* the issue transaction so it can never
    commit separately from the number. Free-text lines (no `item`) and SERVICE
    lines have no stock and must be skipped when it lands.
    """
    return


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------
@transaction.atomic
def cancel_invoice(invoice_id: int, *, reason: str, user=None) -> Invoice:
    """
    Cancel an issued invoice.

    The number is **never freed or reused**: it already appears on a document
    the customer holds and in that period's GSTR-1. If that period has been
    filed, the correct instrument is a credit note, not a cancellation.
    """
    if not (reason or "").strip():
        raise IssueError(
            "CANCELLATION_REASON_REQUIRED",
            "A cancellation reason is required.",
            field="reason",
        )

    invoice = (
        Invoice.objects.select_for_update()
        .select_related("party", "business")
        .filter(pk=invoice_id)
        .first()
    )
    if invoice is None:
        raise InvoiceConflict("That invoice does not exist.", code="NOT_FOUND", status=404)

    if invoice.status == Invoice.Status.CANCELLED:
        raise InvoiceConflict(
            f"Invoice {invoice.display_number} is already cancelled.",
            code="ALREADY_CANCELLED",
        )
    if invoice.status != Invoice.Status.ISSUED:
        raise InvoiceConflict(
            "Only an issued invoice can be cancelled. A draft can just be deleted.",
            code="NOT_ISSUED",
        )

    invoice.status = Invoice.Status.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.cancelled_by = user if (user and user.is_authenticated) else None
    invoice.cancellation_reason = reason.strip()
    # invoice_number deliberately left untouched.
    invoice.save(
        update_fields=[
            "status", "cancelled_at", "cancelled_by", "cancellation_reason", "updated_at",
        ]
    )
    return invoice
