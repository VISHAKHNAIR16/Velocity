"""
Everything a template is allowed to see, built from the invoice alone.

This module is the reason a reprint in 2031 shows the same numbers as the copy
the customer received in 2026. It reads **only** the invoice and its lines.
It never touches `Party`, `Item`, `BusinessProfile`, the GST calculator, or
anything else live.

That restraint is the whole design. The obvious alternative - letting templates
reach for the live party record - would mean that correcting a customer's name
or an item's rate silently rewrites every document already sent. There is a
test for exactly that (`test_a_party_rename_cannot_reach_the_pdf`), because the
rule is easy to state and easy to break the first time somebody adds a "helpful"
lookup.

Helpers already exist and are reused rather than reimplemented:
`amount_in_words()` from `core.money`, `state_tax_label()` from the calculator,
`state_name()` from `core.constants`. Between them they are the reason the PDF
and the on-screen totals cannot drift apart.
"""

from __future__ import annotations

from decimal import Decimal

from apps.core.constants import state_name
from apps.core.fiscal import financial_year
from apps.core.money import amount_in_words
from apps.invoices.services.gst_calculator import state_tax_label

ZERO = Decimal("0.00")

#: Printed on every tax invoice. Reverse charge is not implemented (deferred to
#: 2.4 by decision 14), so this is a constant rather than a derived value -
#: printing a computed "No" would imply the code can compute "Yes" later.
REVERSE_CHARGE_LINE = "Tax payable on reverse charge: No"


def _d(value) -> Decimal:
    """Coerce to Decimal without ever producing a float."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _lines(invoice) -> list[dict]:
    """
    One dict per line, in document order.

    Prefetched and ordered explicitly rather than relying on an unordered
    related manager: a PDF whose rows come out in a different order between two
    downloads of the same invoice would be its own class of bug, and the
    underlying SQL gives no ordering guarantee without an explicit clause.
    """
    return [
        {
            "name": line.item_name or "",
            "type_label": line.get_item_type_display(),
            "hsn": line.hsn_sac_code or "",
            "unit": line.unit or "",
            "quantity": _d(line.quantity),
            "unit_price": _d(line.unit_price),
            "discount": _d(line.line_discount),
            "tax_rate": _d(line.tax_rate),
            "taxable_value": _d(line.taxable_value),
            "cgst": _d(line.cgst_amount),
            "sgst": _d(line.sgst_amount),
            "igst": _d(line.igst_amount),
            "total": _d(line.total_amount),
            # A service carries no stock movement, and a template that prints
            # units for one implies something the invoice does not assert.
            "is_service": line.item_type == "SERVICE",
        }
        for line in invoice.items.select_related("item").order_by("id")
    ]


def build_invoice_context(invoice) -> dict:
    """
    The complete template context for one invoice.

    Everything a template can print is a key here. If a template needs
    something that is not in this dict, the correct fix is to add it here -
    never to reach into the ORM from inside the template.
    """
    lines = _lines(invoice)
    label = state_tax_label(invoice.place_of_supply, invoice.supply_type)

    tax_breakup = (
        {"label": "IGST", "amount": _d(invoice.igst_total)}
        if label == "IGST"
        else {
            "label": label,  # SGST or UTGST
            "amount": _d(invoice.sgst_total),
        }
    )

    return {
        "invoice": invoice,
        "lines": lines,
        "meta": {
            "title": invoice.document_title or "Tax Invoice",
            "number": invoice.invoice_number or "",
            "date": invoice.invoice_date,
            "financial_year": financial_year(invoice.invoice_date),
            "issued_at": invoice.issued_at,
            "reverse_charge_line": REVERSE_CHARGE_LINE,
        },
        "supplier": {
            "name": invoice.business_name,
            "address": invoice.business_address,
            "gstin": invoice.business_gstin,
            "state_code": invoice.business_state_code,
            "state_name": state_name(invoice.business_state_code),
            "logo_url": invoice.business_logo_url,
            "signature_url": invoice.business_signature_url,
            # Drives whether the signature box is drawn at all. An empty URL
            # must produce a blank area, not a broken-image icon and not a
            # silently missing block that shifts the layout.
            "has_signature": bool(invoice.business_signature_url),
            "has_logo": bool(invoice.business_logo_url),
        },
        "recipient": {
            "name": invoice.recipient_name,
            "address": invoice.recipient_address,
            "gstin": invoice.recipient_gstin,
            "state_code": invoice.recipient_state_code,
            "state_name": state_name(invoice.recipient_state_code),
        },
        "shipping": {
            "address": invoice.shipping_address,
            "is_different": bool(
                invoice.shipping_address and invoice.shipping_address != invoice.recipient_address
            ),
        },
        "supply": {
            "place_of_supply_code": invoice.place_of_supply,
            "place_of_supply_name": state_name(invoice.place_of_supply),
            "supply_type": invoice.supply_type,
            "tax_label": label,
            "prices_include_tax": invoice.prices_include_tax,
        },
        "totals": {
            "subtotal": _d(invoice.subtotal),
            "invoice_discount": _d(invoice.invoice_discount),
            "total_discount": _d(invoice.total_discount),
            "taxable_total": _d(invoice.taxable_total),
            "tax_breakup": tax_breakup,
            "round_off": _d(invoice.round_off),
            "grand_total": _d(invoice.grand_total),
            "tax_total": _d(invoice.cgst_total) + _d(invoice.sgst_total) + _d(invoice.igst_total),
            "in_words": amount_in_words(_d(invoice.grand_total)),
        },
        "bank": {
            "account_name": invoice.bank_account_name,
            "account_number": invoice.bank_account_number,
            "ifsc": invoice.bank_ifsc,
            "name": invoice.bank_name,
            "branch": invoice.bank_branch,
            # None of these are required. A business with no bank account still
            # issues invoices, and the block is simply omitted.
            "has_any": invoice.has_bank_details,
        },
        "notes": invoice.notes or "",
        "terms": invoice.terms or "",
    }
